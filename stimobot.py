import asyncio
import httpx
import json
from twitchio.ext import commands
from rapidfuzz import process, fuzz
import os
import discord

# --- Load Environment Variables ---
BOT_ID = os.getenv("BOT_ID")
CHANNEL = os.getenv("CHANNEL", "stimo").lower()
CLUB_ID = os.getenv("CLUB_ID")
PLATFORM = os.getenv("PLATFORM", "common-gen5")

# IRC Auth Token (must start with 'oauth:')
TOKEN = os.getenv("TOKEN")  # IRC Chat token

# Helix API Auth
CLIENT_ID = os.getenv("CLIENT_ID")  # Twitch Developer App Client ID
CLIENT_SECRET = os.getenv("CLIENT_SECRET")  # Twitch Developer App Client Secret
TWITCH_ACCESS_TOKEN = os.getenv("TWITCH_ACCESS_TOKEN")  # Helix Bearer token for Helix calls

# Discord
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID"))

# Optional: Used in Helix VIP check
BROADCASTER_ID = os.getenv("BROADCASTER_ID")

discord_client = discord.Client(intents=discord.Intents.default())

# --- Club Mapping Load ---
try:
    with open("club_mapping.json", "r") as f:
        club_mapping = json.load(f)
except FileNotFoundError:
    club_mapping = {}

# --- Utility Functions ---

def normalize(name):
    return ''.join(name.lower().split())

def streak_emoji(value):
    try:
        value = int(value)
        if value <= 5:
            return "❄️"
        elif value <= 9:
            return "🔥"
        elif value <= 19:
            return "🔥🔥"
        else:
            return "🔥🔥🔥"
    except:
        return "❓"

async def update_club_mapping_from_recent_matches(club_id, platform=PLATFORM):
    url = f"https://proclubs.ea.com/api/fc/clubs/matches?matchType=leagueMatch&platform={platform}&clubIds={club_id}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                matches = response.json()
                updated = False
                for match in matches:
                    opponent = match.get("opponentClub", {})
                    opponent_id = str(opponent.get("clubId"))
                    opponent_name = opponent.get("name")
                    if opponent_id and opponent_name and opponent_id not in club_mapping:
                        club_mapping[opponent_id] = opponent_name
                        updated = True
                if updated:
                    with open("club_mapping.json", "w") as f:
                        json.dump(club_mapping, f, indent=4)
    except Exception as e:
        print(f"[ERROR] update_club_mapping: {e}")

async def is_vip(username):
    headers = {
        "Client-ID": CLIENT_ID,
        "Authorization": f"Bearer {TWITCH_ACCESS_TOKEN}"
    }
    url = f"https://api.twitch.tv/helix/channels/vips?broadcaster_id={BROADCASTER_ID}"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                vip_list = response.json().get("data", [])
                return any(vip["user_name"].lower() == username.lower() for vip in vip_list)
        except Exception as e:
            print(f"[ERROR] VIP check failed: {e}")
    return False

# --- Twitch Bot Class ---

class Bot(commands.Bot):

    def __init__(self):
        super().__init__(
            token=TOKEN,
            prefix="!",
            initial_channels=[CHANNEL],
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            bot_id=BOT_ID
        )

    async def event_ready(self):
        print(f"✅ Bot is online as: {self.nick if hasattr(self, 'nick') else self.user.name}")
        await update_club_mapping_from_recent_matches(CLUB_ID)

        async def announce():
            await discord_client.wait_until_ready()
            channel = discord_client.get_channel(DISCORD_CHANNEL_ID)
            if channel:
                try:
                    message = await channel.send("✅ - StimoBot is now online!")
                    await asyncio.sleep(60)
                    await message.delete()
                except Exception as e:
                    print(f"[ERROR] Discord announce failed: {e}")
            await discord_client.close()

        asyncio.create_task(announce())
        asyncio.create_task(discord_client.start(DISCORD_TOKEN))

    async def event_message(self, message):
        if message.echo or message.author is None:
            return
        await self.handle_commands(message)

    @commands.command(name='hi')
    async def hi(self, ctx):
        await ctx.send("Bye.")

# --- Run Bot ---

if __name__ == "__main__":
    if not TOKEN or not TOKEN.startswith("oauth:"):
        print("❌ Invalid IRC token! It must start with 'oauth:'")
    elif not TWITCH_ACCESS_TOKEN or not TWITCH_ACCESS_TOKEN.startswith("Bearer") and len(TWITCH_ACCESS_TOKEN) < 30:
        print("⚠️ Warning: TWITCH_ACCESS_TOKEN might be invalid.")
    else:
        bot = Bot()
        asyncio.run(bot.run())
