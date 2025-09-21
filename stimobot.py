import os
import asyncio
import time
import aiohttp
from twitchio.ext import commands
import json

# --- Railway Env Vars ---
TOKEN               = os.getenv("TOKEN")               # Twitch user token for IRC (must start with oauth:)
CLIENT_ID           = os.getenv("CLIENT_ID")
CLIENT_SECRET       = os.getenv("CLIENT_SECRET")
BOT_ID              = os.getenv("BOT_ID")              # your bot account's numeric user ID
CHANNEL             = (os.getenv("CHANNEL") or "stimo").lower()

SPOTIFY_CLIENT_ID     = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN")

POLL_SECONDS = int(os.getenv("SPOTIFY_POLL_SECONDS", "5"))

PLATFORM = os.getenv("PLATFORM", "common-gen5")   # EA Pro Clubs platform

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

async def notify_discord_online(bot_name: str):
    """Send a simple 'bot is online' message to Discord via webhook."""
    if not DISCORD_WEBHOOK_URL:
        return
    payload = {"content": f"✅ **{bot_name}** is now online and connected to Twitch chat!"}
    async with aiohttp.ClientSession() as session:
        try:
            await session.post(DISCORD_WEBHOOK_URL, json=payload)
        except Exception as e:
            print(f"[Discord notify] Failed: {e}")

def get_plain_user_token():
    """Return the plain bearer token (no 'oauth:' prefix)."""
    t = os.getenv("TOKEN") or ""
    return t[6:] if t.startswith("oauth:") else t

# --- Async token validation to obtain login (nick) & scopes ---
async def validate_token(token: str):
    if not token:
        print("❌ No token provided")
        return None
    plain = token[6:] if token.startswith("oauth:") else token
    url = "https://id.twitch.tv/oauth2/validate"
    headers = {"Authorization": f"OAuth {plain}"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as r:
                if r.status != 200:
                    print(f"❌ Token validate failed: {r.status} {await r.text()}")
                    return None
                data = await r.json()
                print("=== Token Validation ===")
                print(f"Client ID: {data.get('client_id')}")
                print(f"User ID:   {data.get('user_id')}")
                print(f"Login:     {data.get('login')}")
                print(f"Scopes:    {data.get('scopes')}")
                print("========================")
                return {
                    "client_id": data.get("client_id"),
                    "user_id": data.get("user_id"),
                    "login": data.get("login"),
                    "scopes": data.get("scopes"),
                }
    except Exception as e:
        print(f"❌ Token validate exception: {e}")
        return None

# --- Minimal IRC-over-WebSocket client to guarantee viewer-list presence ---
class SimpleIRCClient:
    def __init__(self, token_oauth: str, login: str, channel: str):
        """
        token_oauth: the full token string including 'oauth:' prefix
        login: twitch username for the token (nick)
        channel: channel to join without '#'
        """
        self.token_oauth = token_oauth
        self.login = login
        self.channel = channel
        self.ws = None
        self._running = False

    async def connect_and_run(self):
        """
        Persistent loop: connect, join, respond to PING, log messages, handle !ping.
        Reconnects with backoff if disconnected.
        """
        backoff = 1
        self._running = True
        while self._running:
            try:
                async with aiohttp.ClientSession() as session:
                    print("[IRC-WS] Connecting to wss://irc-ws.chat.twitch.tv:443 ...")
                    async with session.ws_connect("wss://irc-ws.chat.twitch.tv:443") as ws:
                        self.ws = ws
                        # Request capabilities (membership to appear in viewer list, tags, commands)
                        await self._send_raw("CAP REQ :twitch.tv/membership twitch.tv/tags twitch.tv/commands")
                        await self._send_raw(f"PASS {self.token_oauth}")
                        await self._send_raw(f"NICK {self.login}")
                        await self._send_raw(f"JOIN #{self.channel}")
                        print(f"[IRC-WS] Joined #{self.channel} as {self.login}")

                        # Optional hello message via IRC
                        await self.privmsg(f"👋 (IRC-WS) {self.login} connected.")

                        backoff = 1  # reset backoff on success

                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                line = msg.data.rstrip("\r\n")
                                print(f"[IRC RAW] {line}")

                                # Respond to PING to keep the connection alive
                                if line.startswith("PING "):
                                    payload = line.split(" ", 1)[1]
                                    await self._send_raw(f"PONG {payload}")
                                    continue

                                # Simple PRIVMSG parsing for !ping
                                # Example: :user!user@user.tmi.twitch.tv PRIVMSG #channel :message text
                                try:
                                    if " PRIVMSG #" in line:
                                        # Extract channel and message
                                        prefix, rest = line.split(" PRIVMSG #", 1)
                                        chan, msgtext = rest.split(" :", 1)
                                        chan = chan.split(" ", 1)[0]
                                        # Extract author from prefix: starts with ":nick!"
                                        author = prefix.split("!", 1)[0][1:]
                                        print(f"[IRC MSG] #{chan} <{author}> {msgtext}")

                                        text  = msgtext.strip()
                                        lower = text.lower()
                                        
                                        if lower.startswith("!ping"):
                                            await self.privmsg("pong")
                                        
                                        elif lower.startswith("!versus") or lower.startswith("!vs"):
                                            # Extract args after the command
                                            parts = text.split(" ", 1)
                                            argstr = parts[1] if len(parts) > 1 else ""
                                            reply = await handle_versus_command(argstr)
                                            # keep replies short for Twitch; we already truncate in formatter
                                            await self.privmsg(reply)
                                except Exception as e:
                                    print(f"[IRC-WS Parse Error] {e}")

                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                print(f"[IRC-WS] WebSocket error: {msg.data}")
                                break
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE):
                                print("[IRC-WS] WebSocket closed by server.")
                                break

            except Exception as e:
                print(f"[IRC-WS] Connection error: {e}")

            # Reconnect with exponential backoff
            if self._running:
                print(f"[IRC-WS] Reconnecting in {backoff}s ...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _send_raw(self, line: str):
        if self.ws is not None:
            await self.ws.send_str(line + "\r\n")

    async def privmsg(self, text: str):
        await self._send_raw(f"PRIVMSG #{self.channel} :{text}")

    def stop(self):
        self._running = False

# --- Spotify Client ---
class SpotifyClient:
    def __init__(self, client_id, client_secret, refresh_token):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.access_token = None
        self.expires_at = 0

    async def _refresh_access_token(self, session: aiohttp.ClientSession):
        if self.access_token and time.time() < self.expires_at - 10:
            return self.access_token

        data = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        async with session.post("https://accounts.spotify.com/api/token", data=data) as r:
            tok = await r.json()
            if "access_token" not in tok:
                raise RuntimeError(f"Failed to refresh Spotify token: {tok}")
            self.access_token = tok["access_token"]
            self.expires_at = time.time() + tok.get("expires_in", 3600)
            print("[DEBUG] Refreshed Spotify access token")
            return self.access_token

    async def get_current_track(self, session: aiohttp.ClientSession):
        token = await self._refresh_access_token(session)
        headers = {"Authorization": f"Bearer {token}"}
        async with session.get("https://api.spotify.com/v1/me/player/currently-playing", headers=headers) as r:
            if r.status == 204:
                print("[DEBUG] Spotify: 204 No Content (nothing playing)")
                return None
            if r.status != 200:
                print(f"[DEBUG] Spotify API returned status {r.status}")
                try:
                    print("[DEBUG] Spotify body:", await r.text())
                except Exception:
                    pass
                return None
            j = await r.json()
            if not j.get("is_playing"):
                print("[DEBUG] Spotify: not playing")
                return None
            item = j.get("item")
            if not item or item.get("type") != "track":
                print("[DEBUG] Spotify: item missing or not a track")
                return None
            return {
                "id": item.get("id"),
                "title": item.get("name"),
                "artists": ", ".join(a["name"] for a in item.get("artists", [])),
                "progress_ms": j.get("progress_ms", 0),
            }

# --- EA Pro Clubs helpers (aiohttp) ---
EA_BASE = "https://proclubs.ea.com/api/fc"

def _streak_emoji(v):
    try:
        v = int(v)
        return "❄️" if v <= 5 else ("🔥" if v <= 9 else "🔥🔥" if v <= 19 else "🔥🔥🔥")
    except:
        return "❓"

async def _http_json(session, url, headers=None):
    h = {"User-Agent": "Mozilla/5.0"}
    if headers: h.update(headers)
    async with session.get(url, headers=h, timeout=15) as r:
        if r.status != 200:
            txt = await r.text()
            raise RuntimeError(f"HTTP {r.status}: {txt[:200]}")
        return await r.json()

async def ea_search_clubs(session, name_or_id: str):
    """Return list of leaderboard search results. If numeric, try direct id shim."""
    if name_or_id.isdigit():
        # Fake a 'search' style object for direct ID usage
        return [{"clubInfo": {"clubId": int(name_or_id), "name": f"ID:{name_or_id}"}}]
    q = name_or_id.replace(" ", "%20")
    url = f"{EA_BASE}/allTimeLeaderboard/search?platform={PLATFORM}&clubName={q}"
    data = await _http_json(session, url)
    # Filter out EA's 'None of these'
    return [c for c in data if c.get("clubInfo", {}).get("name", "").strip().lower() != "none of these"]

async def ea_club_stats(session, club_id: str):
    url = f"{EA_BASE}/clubs/overallStats?platform={PLATFORM}&clubIds={club_id}"
    data = await _http_json(session, url)
    club = data[0] if isinstance(data, list) and data else {}
    return {
        "matchesPlayed": club.get("gamesPlayed", "N/A"),
        "wins": club.get("wins", "N/A"),
        "draws": club.get("ties", "N/A"),
        "losses": club.get("losses", "N/A"),
        "winStreak": club.get("wstreak", "0"),
        "unbeatenStreak": club.get("unbeatenstreak", "0"),
        "skillRating": club.get("skillRating", "N/A"),
    }

async def ea_recent_form(session, club_id: str, n=5):
    base = f"{EA_BASE}/clubs/matches"
    forms = []
    all_matches = []
    for t in ("leagueMatch", "playoffMatch"):
        url = f"{base}?matchType={t}&platform={PLATFORM}&clubIds={club_id}"
        try:
            all_matches += await _http_json(session, url)
        except Exception:
            pass
    all_matches.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
    for m in all_matches[:n]:
        clubs = m.get("clubs", {})
        c = clubs.get(str(club_id), {})
        opp_id = next((cid for cid in clubs if cid != str(club_id)), None)
        o = clubs.get(opp_id, {}) if opp_id else {}
        us = int(c.get("goals", 0))
        them = int(o.get("goals", 0)) if o else 0
        forms.append("✅" if us > them else "❌" if us < them else "➖")
    return forms

async def ea_last_match_line(session, club_id: str):
    base = f"{EA_BASE}/clubs/matches"
    all_matches = []
    for t in ("leagueMatch", "playoffMatch"):
        url = f"{base}?matchType={t}&platform={PLATFORM}&clubIds={club_id}"
        try:
            all_matches += await _http_json(session, url)
        except Exception:
            pass
    if not all_matches:
        return "Last: n/a"
    all_matches.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
    m = all_matches[0]
    clubs = m.get("clubs", {})
    c = clubs.get(str(club_id), {})
    opp_id = next((cid for cid in clubs if cid != str(club_id)), None)
    o = clubs.get(opp_id, {}) if opp_id else {}
    our = int(c.get("goals", 0))
    their = int(o.get("goals", 0) if o else 0)
    opp_name = (o.get("details") or {}).get("name") or o.get("name") or "Unknown"
    badge = "✅" if our > their else "❌" if our < their else "➖"
    return f"Last: {badge} vs {opp_name} ({our}-{their})"

async def ea_days_since_last(session, club_id: str):
    base = f"{EA_BASE}/clubs/matches"
    all_matches = []
    for t in ("leagueMatch", "playoffMatch"):
        url = f"{base}?matchType={t}&platform={PLATFORM}&clubIds={club_id}"
        try:
            all_matches += await _http_json(session, url)
        except Exception:
            pass
    if not all_matches:
        return None
    all_matches.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
    ts = all_matches[0].get("timestamp", 0)
    if not ts:
        return None
    # ts is epoch seconds (UTC)
    import time as _time
    return int(( _time.time() - ts ) // 86400)

async def ea_club_rank(session, club_id: str):
    url = f"{EA_BASE}/allTimeLeaderboard?platform={PLATFORM}"
    try:
        data = await _http_json(session, url)
        for c in data:
            if str(c.get("clubId")) == str(club_id):
                return c.get("rank", "Unranked")
    except Exception:
        pass
    return "Unranked"

def format_versus_line(name, stats, rank, last_line, form, days):
    # Twitch-friendly single line (keep it compact)
    rtxt = f"#{rank}" if isinstance(rank, int) or (isinstance(rank, str) and rank.isdigit()) else "Unranked"
    form_str = "".join(form) if form else "—"
    days_str = f"{days}d" if days is not None else "n/a"
    return (
        f"{name.upper()} | Rank {rtxt} | SR {stats['skillRating']} | "
        f"W-D-L {stats['wins']}-{stats['draws']}-{stats['losses']} | "
        f"WS {stats['winStreak']}{_streak_emoji(stats['winStreak'])} • UBS {stats['unbeatenStreak']}{_streak_emoji(stats['unbeatenStreak'])} | "
        f"{last_line} | Form: {form_str} | Last played: {days_str}"
    )[:480]  # headroom under ~500 chars

# --- Twitch-chat command handler for Pro Clubs ---
async def handle_versus_command(argstr: str) -> str:
    args = argstr.strip()
    if not args:
        return "Usage: !versus <club name or club id>"

    try:
        async with aiohttp.ClientSession() as session:
            # 1) search clubs
            results = await ea_search_clubs(session, args)
            if not results:
                return "No matching clubs found."

            # 2) if non-numeric query yields multiple, list top 5 with IDs
            if not args.isdigit() and len(results) > 1:
                top = results[:5]
                listing = " | ".join(f"{i+1}) {c['clubInfo']['name']}[{c['clubInfo']['clubId']}]" for i, c in enumerate(top))
                return f"Multiple matches: {listing} — re-run with the club ID (e.g. !versus 123456)"

            # 3) choose first result
            chosen = results[0]
            club_id = str(chosen['clubInfo']['clubId'])
            name    = chosen['clubInfo']['name']

            # 4) fetch stats and compose line
            stats     = await ea_club_stats(session, club_id)
            form      = await ea_recent_form(session, club_id, n=5)
            last_line = await ea_last_match_line(session, club_id)
            days      = await ea_days_since_last(session, club_id)
            rank      = await ea_club_rank(session, club_id)

            return format_versus_line(name, stats, rank, last_line, form, days)
    except Exception as e:
        print(f"[versus] error: {e}")
        return "Error fetching opponent stats. Try again in a moment."

# --- Helix + Spotify Bot (kept as-is for announcements) ---
class Bot(commands.Bot):
    def __init__(self):
        super().__init__(
            token=TOKEN,
            prefix="!",
            initial_channels=[CHANNEL],   # not relied upon anymore for presence
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            bot_id=BOT_ID,
        )
        self.spotify = SpotifyClient(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REFRESH_TOKEN)
        self._last_track_id = None

        self._broadcaster_id = None
        self._user_token_plain = get_plain_user_token()
        self._helix_ready = False

        # Our added raw IRC WS client
        self._irc_ws_client: SimpleIRCClient | None = None

        # --- live gating helpers ---
        self._app_token = None
        self._app_token_exp = 0.0
        self._live_status = None        # True/False
        self._live_checked_at = 0.0     # epoch seconds

    async def event_ready(self):
        bot_name = os.getenv("BOT_NAME", "StimoBot")
        print(f"Logged in as {bot_name}")
        await notify_discord_online(bot_name)
        asyncio.create_task(self.bootstrap_helix_and_run())

    async def bootstrap_helix_and_run(self):
        async with aiohttp.ClientSession() as session:
            # Resolve broadcaster id via Helix
            try:
                self._broadcaster_id = await self._resolve_broadcaster_id(session, CHANNEL)
                print(f"[DEBUG] Resolved broadcaster_id for {CHANNEL}: {self._broadcaster_id}")
            except Exception as e:
                print(f"[Startup Warn] Could not resolve broadcaster id: {e}")

            # Startup announcement via Helix
            try:
                ok = await self._helix_announce(session, "✅ StimoBot is online and watching Spotify 🎶", "green")
                self._helix_ready = ok
                if ok:
                    print("[DEBUG] Helix startup announcement sent")
                else:
                    print("[Startup Warn] Helix startup announcement failed")
            except Exception as e:
                print(f"[Startup Warn] Helix announcement error: {e}")

        # Start our own IRC-WS client to guarantee viewer-list presence
        try:
            # Validate token (again) to get the login for NICK
            tv = await validate_token(TOKEN)
            nick = tv.get("login") if tv else None
            if not nick:
                nick = "stimobot"
                print("[IRC-WS] Warning: could not determine login from token; defaulting to 'stimobot'.")
            self._irc_ws_client = SimpleIRCClient(token_oauth=TOKEN, login=nick, channel=CHANNEL)
            asyncio.create_task(self._irc_ws_client.connect_and_run())
        except Exception as e:
            print(f"[IRC-WS] Failed to start IRC WS client: {e}")

        # Start Spotify loop
        asyncio.create_task(self.spotify_loop())

    async def _resolve_broadcaster_id(self, session: aiohttp.ClientSession, login_name: str) -> str:
        token_url = "https://id.twitch.tv/oauth2/token"
        data = {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "grant_type": "client_credentials"}
        async with session.post(token_url, data=data) as r:
            tok = await r.json()
            if "access_token" not in tok:
                raise RuntimeError(f"Failed to get app token: {tok}")
            app_token = tok["access_token"]
            print("[DEBUG] Obtained Twitch app access token")

        users_url = f"https://api.twitch.tv/helix/users?login={login_name}"
        headers = {"Client-Id": CLIENT_ID, "Authorization": f"Bearer {app_token}"}
        async with session.get(users_url, headers=headers) as r:
            j = await r.json()
            if r.status != 200 or "data" not in j or not j["data"]:
                raise RuntimeError(f"Helix users lookup failed: {r.status} {j}")
            return j["data"][0]["id"]

    async def _get_app_token(self, session: aiohttp.ClientSession) -> str:
        """Get & cache an App Access Token for GET /streams live check."""
        now = time.time()
        if self._app_token and now < (self._app_token_exp - 30):
            return self._app_token
        url = "https://id.twitch.tv/oauth2/token"
        data = {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "client_credentials",
        }
        async with session.post(url, data=data) as r:
            j = await r.json()
            if "access_token" not in j:
                raise RuntimeError(f"App token error: {r.status} {j}")
            self._app_token = j["access_token"]
            self._app_token_exp = now + j.get("expires_in", 3600)
            print("[DEBUG] Obtained App Access Token (for streams)")
            return self._app_token

    async def _is_stream_live(self, session: aiohttp.ClientSession, cache_seconds: int = 60) -> bool:
        """Return True if the channel is live. Cached for cache_seconds."""
        now = time.time()
        if self._live_status is not None and (now - self._live_checked_at) < cache_seconds:
            return self._live_status
        if not self._broadcaster_id:
            return False

        token = await self._get_app_token(session)
        headers = {"Client-Id": CLIENT_ID, "Authorization": f"Bearer {token}"}
        url = f"https://api.twitch.tv/helix/streams?user_id={self._broadcaster_id}"
        async with session.get(url, headers=headers) as r:
            if r.status != 200:
                txt = await r.text()
                print(f"[DEBUG] streams check failed: {r.status} {txt}")
                self._live_status = False
            else:
                data = await r.json()
                self._live_status = bool(data.get("data"))
        self._live_checked_at = now
        print(f"[DEBUG] Live status: {self._live_status}")
        return self._live_status

    async def _helix_announce(self, session: aiohttp.ClientSession, text: str, color: str = "primary") -> bool:
        """Send a Twitch announcement (colored highlight)."""
        if not (self._broadcaster_id and BOT_ID and self._user_token_plain and CLIENT_ID):
            return False

        url = "https://api.twitch.tv/helix/chat/announcements"
        headers = {
            "Client-Id": CLIENT_ID,
            "Authorization": f"Bearer {self._user_token_plain}",
            "Content-Type": "application/json",
        }
        payload = {
            "broadcaster_id": str(self._broadcaster_id),
            "moderator_id": str(BOT_ID),
            "message": text,
            "color": color
        }
        async with session.post(url, headers=headers, json=payload) as r:
            if r.status in (200, 201, 204):
                return True
            body = await r.text()
            print(f"[Helix Announce Error] {r.status} {body}")
            return False

    async def spotify_loop(self):
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    track = await self.spotify.get_current_track(session)

                    # gate announcements to live streams only (cached 60s)
                    is_live = await self._is_stream_live(session, cache_seconds=60)
                    if not is_live:
                        if track and track["id"] != self._last_track_id:
                            self._last_track_id = track["id"]
                            print("[DEBUG] Track changed while OFFLINE; not announcing.")
                        else:
                            print("[DEBUG] Stream offline; skipping announcement")
                        await asyncio.sleep(POLL_SECONDS)
                        continue

                    if track and track["id"] != self._last_track_id:
                        if track["progress_ms"] < 1500:
                            await asyncio.sleep(1.5)
                            track2 = await self.spotify.get_current_track(session)
                            if not track2 or track2["id"] != track["id"]:
                                print("[DEBUG] Debounce: track changed during grace; skipping")
                                await asyncio.sleep(POLL_SECONDS)
                                continue

                        self._last_track_id = track["id"]
                        msg = f"🎶 𝐍𝐨𝐰 𝐏𝐥𝐚𝐲𝐢𝐧𝐠: {track['title']} — {track['artists']}"
                        print(f"[DEBUG] Sending announcement (LIVE): {msg}")
                        await self._helix_announce(session, msg, "purple")
                    else:
                        print("[DEBUG] No new track or nothing playing")
                except Exception as e:
                    print(f"[Spotify Error] {e}")
                await asyncio.sleep(POLL_SECONDS)

    @commands.command(name="versus", aliases=["vs"])
    async def versus_cmd(self, ctx: commands.Context, *args):
        """
        Usage:
          !versus <club name>
          !versus <club id>
        """
        if not args:
            return await ctx.send("Usage: !versus <club name or club id>")

        query = " ".join(args).strip()
        try:
            async with aiohttp.ClientSession() as session:
                # 1) search
                results = await ea_search_clubs(session, query)
                if not results:
                    return await ctx.send("No matching clubs found.")

                # If multiple club-name matches and the user passed a non-numeric query,
                # list top 5 options with IDs so chatter can re-run with an ID.
                if not query.isdigit() and len(results) > 1:
                    top = results[:5]
                    listing = " | ".join(f"{i+1}) {c['clubInfo']['name']}[{c['clubInfo']['clubId']}]" for i, c in enumerate(top))
                    return await ctx.send(f"Multiple matches: {listing} — re-run with the club ID (e.g. !versus 123456)")

                # 2) pick the one
                chosen = results[0]
                club_id = str(chosen["clubInfo"]["clubId"])
                name = chosen["clubInfo"]["name"]

                # 3) pull stats + lines
                stats = await ea_club_stats(session, club_id)
                form = await ea_recent_form(session, club_id, n=5)
                last_line = await ea_last_match_line(session, club_id)
                days = await ea_days_since_last(session, club_id)
                rank = await ea_club_rank(session, club_id)

                # 4) print compact line
                line = format_versus_line(name, stats, rank, last_line, form, days)
                await ctx.send(line)
        except Exception as e:
            print(f"[versus] error: {e}")
            await ctx.send("Error fetching opponent stats. Try again in a moment.")

# --- Run bot ---
if __name__ == "__main__":
    print("=== Environment Debug ===")
    print(f"TOKEN present? {'yes' if TOKEN else 'no'}")
    if TOKEN:
        print(f"TOKEN startswith 'oauth:'? {TOKEN.startswith('oauth:')}")
        print(f"TOKEN preview: {TOKEN[:10]}...")
    print(f"CLIENT_ID: {CLIENT_ID}")
    print(f"CLIENT_SECRET present? {'yes' if CLIENT_SECRET else 'no'}")
    print(f"BOT_ID: {BOT_ID}")
    print(f"CHANNEL: {CHANNEL}")
    print(f"SPOTIFY_CLIENT_ID present? {'yes' if SPOTIFY_CLIENT_ID else 'no'}")
    print(f"SPOTIFY_CLIENT_SECRET present? {'yes' if SPOTIFY_CLIENT_SECRET else 'no'}")
    print(f"SPOTIFY_REFRESH_TOKEN present? {'yes' if SPOTIFY_REFRESH_TOKEN else 'no'}")
    print("=========================")

    # Validate token once at startup (prints scopes & login)
    asyncio.run(validate_token(TOKEN))

    if not TOKEN or not TOKEN.startswith("oauth:"):
        print("❌ Missing or invalid Twitch user token (must start with 'oauth:')")
    else:
        print("[DEBUG] Running Bot() now...")
        Bot().run()
