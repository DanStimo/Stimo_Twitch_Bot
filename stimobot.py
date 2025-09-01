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
            initial_channels=[CHANNEL],   # joins IRC channel
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            bot_id=BOT_ID,
        )
        self.spotify = SpotifyClient(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REFRESH_TOKEN)
        self._last_track_id = None
        self._irc_channel = None  # will hold IRC channel object once joined

    async def event_ready(self):
        print(f"✅ Connected as {self.user.name}")
        # Force join IRC so we appear in viewer list
        try:
            await self.join_channels([CHANNEL])
            print(f"[DEBUG] joined IRC channel: {CHANNEL}")
        except Exception as e:
            print(f"[DEBUG] join_channels error: {e}")
        # Start Spotify loop
        asyncio.create_task(self.spotify_loop())

    async def event_join(self, channel, user):
        # Cache IRC channel once *this bot* joins
        if user.name.lower() == self.nick.lower():
            self._irc_channel = channel
            print(f"[DEBUG] Cached IRC channel: {channel.name}")
            try:
                await channel.send("👋 StimoBot is here!")
            except Exception as e:
                print(f"[Startup Error] Could not send startup message: {e}")

    async def spotify_loop(self):
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    track = await self.spotify.get_current_track(session)
                    if track and track["id"] != self._last_track_id:
                        self._last_track_id = track["id"]
                        msg = f"🎶 Now playing: {track['title']} — {track['artists']} {track['url']}"
                        print(f"[DEBUG] Sending message: {msg}")
                        if self._irc_channel:
                            await self._irc_channel.send(msg)
                        else:
                            print("[DEBUG] No IRC channel cached yet")
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
        print("❌ Missing or invalid Twitch IRC token (must start with 'oauth:')")
    else:
        print("[DEBUG] Running Bot() now...")
        Bot().run()
