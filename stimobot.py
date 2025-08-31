import os
import asyncio
import time
import aiohttp
from twitchio.ext import commands

# --- Railway Env Vars ---
TOKEN               = os.getenv("TOKEN")               # Twitch IRC token (must start with oauth:)
CLIENT_ID           = os.getenv("CLIENT_ID")           # Twitch client id
CLIENT_SECRET       = os.getenv("CLIENT_SECRET")       # Twitch client secret
BOT_ID              = os.getenv("BOT_ID")              # Twitch bot user id (string ok)
CHANNEL             = os.getenv("CHANNEL", "stimo").lower()

SPOTIFY_CLIENT_ID     = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN")

POLL_SECONDS = int(os.getenv("SPOTIFY_POLL_SECONDS", "5"))


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
                "url": item.get("external_urls", {}).get("spotify", ""),
                "progress_ms": j.get("progress_ms", 0),
            }


# --- Twitch Bot ---
class Bot(commands.Bot):
    def __init__(self):
        super().__init__(
            token=TOKEN,
            prefix="!",
            initial_channels=[CHANNEL],  # still join IRC, but we'll post via Helix channel object
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            bot_id=BOT_ID,
        )
        self.spotify = SpotifyClient(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REFRESH_TOKEN)
        self._last_track_id = None
        self._chan = None           # twitchio PartialChannel
        self._broadcaster_id = None # numeric id as string

    async def event_ready(self):
        print(f"✅ Connected as {self.user.name}")
        asyncio.create_task(self.bootstrap_and_run())

    async def bootstrap_and_run(self):
        # Resolve broadcaster id and cache a PartialChannel we can send to
        async with aiohttp.ClientSession() as session:
            try:
                self._broadcaster_id = await self._resolve_broadcaster_id(session, CHANNEL)
                print(f"[DEBUG] Resolved broadcaster_id for {CHANNEL}: {self._broadcaster_id}")
            except Exception as e:
                print(f"[Startup Error] Could not resolve broadcaster id for '{CHANNEL}': {e}")
                return

            try:
                self._chan = await self.fetch_channel(int(self._broadcaster_id))
                print(f"[DEBUG] Fetched PartialChannel for id {self._broadcaster_id}")
                await self._chan.send("✅ StimoBot is online and watching Spotify 🎶")
            except Exception as e:
                print(f"[Startup Error] Could not fetch/send to channel id {self._broadcaster_id}: {e}")
                return

        # Start Spotify announcer
        asyncio.create_task(self.spotify_loop())

    async def _resolve_broadcaster_id(self, session: aiohttp.ClientSession, login_name: str) -> str:
        """
        Get an app access token, then resolve the numeric broadcaster_id
        for the given channel login via Helix /users.
        """
        # 1) App access token
        token_url = "https://id.twitch.tv/oauth2/token"
        data = {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "client_credentials",
        }
        async with session.post(token_url, data=data) as r:
            tok = await r.json()
            if "access_token" not in tok:
                raise RuntimeError(f"Failed to get app token: {tok}")
            app_token = tok["access_token"]
            print("[DEBUG] Obtained Twitch app access token")

        # 2) Helix users lookup
        users_url = f"https://api.twitch.tv/helix/users?login={login_name}"
        headers = {"Client-Id": CLIENT_ID, "Authorization": f"Bearer {app_token}"}
        async with session.get(users_url, headers=headers) as r:
            j = await r.json()
            if r.status != 200 or "data" not in j or not j["data"]:
                raise RuntimeError(f"Helix users lookup failed: {r.status} {j}")
            return j["data"][0]["id"]  # numeric id as string

    async def spotify_loop(self):
        # Ensure channel is ready
        while not self._chan:
            print("[DEBUG] Waiting for PartialChannel...")
            await asyncio.sleep(1)

        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    track = await self.spotify.get_current_track(session)
                    if track and track["id"] != self._last_track_id:
                        # Small debounce to avoid flapping on quick seeks
                        if track["progress_ms"] < 1500:
                            await asyncio.sleep(1.5)
                            track2 = await self.spotify.get_current_track(session)
                            if not track2 or track2["id"] != track["id"]:
                                print("[DEBUG] Debounce: initial track changed, skipping announce")
                                await asyncio.sleep(POLL_SECONDS)
                                continue

                        self._last_track_id = track["id"]
                        msg = f"🎶 Now playing: {track['title']} — {track['artists']} {track['url']}"
                        print(f"[DEBUG] Sending message: {msg}")
                        await self._chan.send(msg)
                    else:
                        print("[DEBUG] No new track or nothing playing")
                except Exception as e:
                    print(f"[Spotify Error] {e}")
                await asyncio.sleep(POLL_SECONDS)


# --- Run bot ---
if __name__ == "__main__":
    if not TOKEN or not TOKEN.startswith("oauth:"):
        print("❌ Missing or invalid Twitch IRC token (must start with 'oauth:')")
    else:
        Bot().run()
