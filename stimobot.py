import os
import asyncio
import time
import aiohttp
from twitchio.ext import commands

# --- Railway Env Vars ---
TOKEN               = os.getenv("TOKEN")               # Twitch IRC token (must start with oauth:)
CLIENT_ID           = os.getenv("CLIENT_ID")           # Twitch client id
CLIENT_SECRET       = os.getenv("CLIENT_SECRET")       # Twitch client secret
BOT_ID              = os.getenv("BOT_ID")              # Twitch bot user id
CHANNEL             = os.getenv("CHANNEL", "stimo").lower()

SPOTIFY_CLIENT_ID     = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN")

POLL_SECONDS = 5


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
            if r.status == 204:  # nothing playing
                return None
            if r.status != 200:
                print(f"[DEBUG] Spotify API returned status {r.status}")
                return None
            j = await r.json()
            if not j.get("is_playing"):
                return None
            item = j.get("item")
            if not item or item.get("type") != "track":
                return None
            return {
                "id": item.get("id"),
                "title": item.get("name"),
                "artists": ", ".join(a["name"] for a in item.get("artists", [])),
                "url": item.get("external_urls", {}).get("spotify", ""),
            }


# --- Twitch Bot ---
class Bot(commands.Bot):
    def __init__(self):
        super().__init__(
            token=TOKEN,
            prefix="!",
            initial_channels=[CHANNEL],
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            bot_id=BOT_ID,
        )
        self.spotify = SpotifyClient(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REFRESH_TOKEN)
        self._last_track_id = None
        self._chan = None

    async def event_ready(self):
        print(f"✅ Connected as {self.user.name}")
        asyncio.create_task(self.spotify_loop())

    async def event_message(self, message):
        # Cache the channel from first message seen
        if self._chan is None:
            self._chan = message.channel
            print(f"[DEBUG] Cached channel: {self._chan.name}")
            await self._chan.send("✅ StimoBot is online and watching Spotify 🎶")

        # Still process commands if you ever add them
        await self.handle_commands(message)

    async def spotify_loop(self):
        # wait until channel is cached
        while not self._chan:
            print("[DEBUG] Waiting for channel object...")
            await asyncio.sleep(1)

        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    track = await self.spotify.get_current_track(session)
                    if track and track["id"] != self._last_track_id:
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
