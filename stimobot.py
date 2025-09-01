import os
import asyncio
import time
import aiohttp
from twitchio.ext import commands

# --- Railway Env Vars ---
TOKEN               = os.getenv("TOKEN")               # Twitch user token for chat (must start with oauth:)
CLIENT_ID           = os.getenv("CLIENT_ID")
CLIENT_SECRET       = os.getenv("CLIENT_SECRET")
BOT_ID              = os.getenv("BOT_ID")              # your bot account's numeric user ID (string ok)
CHANNEL             = (os.getenv("CHANNEL") or "stimo").lower()

SPOTIFY_CLIENT_ID     = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN")

POLL_SECONDS = int(os.getenv("SPOTIFY_POLL_SECONDS", "5"))


def get_plain_user_token():
    """Return the plain bearer token (no 'oauth:' prefix)."""
    t = os.getenv("TOKEN") or ""
    return t[6:] if t.startswith("oauth:") else t


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


# --- Twitch Bot ---
class Bot(commands.Bot):
    def __init__(self):
        super().__init__(
            token=TOKEN,
            prefix="!",
            initial_channels=[CHANNEL],   # attempt IRC join (viewer list)
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            bot_id=BOT_ID,
        )
        self.spotify = SpotifyClient(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REFRESH_TOKEN)
        self._last_track_id = None

        self._irc_channel = None
        self._broadcaster_id = None
        self._user_token_plain = get_plain_user_token()
        self._helix_ready = False

    async def event_raw_data(self, data):
        print(f"[IRC RAW] {data}")

    async def event_ready(self):
        print(f"✅ Connected as {self.user.name}")
        asyncio.create_task(self.bootstrap_helix_and_run())

    async def bootstrap_helix_and_run(self):
        async with aiohttp.ClientSession() as session:
            try:
                self._broadcaster_id = await self._resolve_broadcaster_id(session, CHANNEL)
                print(f"[DEBUG] Resolved broadcaster_id for {CHANNEL}: {self._broadcaster_id}")
            except Exception as e:
                print(f"[Startup Warn] Could not resolve broadcaster id: {e}")

            try:
                ok = await self._helix_announce(session, "✅ StimoBot is online and watching Spotify 🎶", "green")
                self._helix_ready = ok
                if ok:
                    print("[DEBUG] Helix startup announcement sent")
                else:
                    print("[Startup Warn] Helix startup announcement failed")
            except Exception as e:
                print(f"[Startup Warn] Helix announcement error: {e}")

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

    async def _helix_announce(self, session: aiohttp.ClientSession, text: str, color: str = "purple") -> bool:
        """
        Send a Twitch announcement (colored highlight).
        Requires bot to be moderator and token with moderator:manage:announcements.
        """
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
                    if track and track["id"] != self._last_track_id:
                        if track["progress_ms"] < 1500:
                            await asyncio.sleep(1.5)
                            track2 = await self.spotify.get_current_track(session)
                            if not track2 or track2["id"] != track["id"]:
                                print("[DEBUG] Debounce: track changed during grace; skipping")
                                await asyncio.sleep(POLL_SECONDS)
                                continue

                        self._last_track_id = track["id"]
                        msg = f"🎶 Now playing: {track['title']} — {track['artists']}"
                        print(f"[DEBUG] Sending announcement: {msg}")
                        await self._helix_announce(session, msg, "primary")
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

    if not TOKEN or not TOKEN.startswith("oauth:"):
        print("❌ Missing or invalid Twitch user token (must start with 'oauth:')")
    else:
        print("[DEBUG] Running Bot() now...")
        Bot().run()
