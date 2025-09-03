import os
import asyncio
import time
import aiohttp
from twitchio.ext import commands

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

                                        if msgtext.strip().lower() == "!ping":
                                            await self.privmsg("pong")
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
        print(f"✅ Connected as {self.user.name}")
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
