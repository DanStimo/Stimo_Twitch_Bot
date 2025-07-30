import asyncio
import httpx
import json
from twitchio.ext import commands
from rapidfuzz import process, fuzz
import os
import discord

# --- Environment Variables ---
BOT_ID = os.getenv("BOT_ID")
CHANNEL = os.getenv("CHANNEL", "stimo").lower()
CLUB_ID = os.getenv("CLUB_ID")
PLATFORM = os.getenv("PLATFORM", "common-gen5")

TOKEN = os.getenv("TOKEN")  # IRC token (must start with 'oauth:')
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
TWITCH_ACCESS_TOKEN = os.getenv("TWITCH_ACCESS_TOKEN")  # Helix token
BROADCASTER_ID = os.getenv("BROADCASTER_ID")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID"))

# --- Load Club Mapping ---
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

async def get_club_stats(club_id):
    url = f"https://proclubs.ea.com/api/fc/clubs/overallStats?platform={PLATFORM}&clubIds={club_id}"
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and data:
                return data[0]
    return None

async def get_recent_form(club_id):
    url = f"https://proclubs.ea.com/api/fc/clubs/matches?platform={PLATFORM}&clubIds={club_id}&matchType=leagueMatch"
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        if response.status_code == 200:
            matches = response.json()
            results = []
            for match in sorted(matches, key=lambda x: x.get("timestamp", 0), reverse=True)[:5]:
                clubs = match.get("clubs", {})
                this_club = clubs.get(str(club_id))
                opponent_id = next((cid for cid in clubs if cid != str(club_id)), None)
                opp = clubs.get(opponent_id)
                if not this_club or not opp:
                    continue
                us, them = int(this_club["goals"]), int(opp["goals"])
                results.append("✅" if us > them else "❌" if us < them else "➖")
            return results
    return []

async def get_club_rank(club_id):
    url = f"https://proclubs.ea.com/api/fc/allTimeLeaderboard?platform={PLATFORM}"
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        if response.status_code == 200:
            for idx, entry in enumerate(response.json()):
                if str(entry.get("clubInfo", {}).get("clubId")) == str(club_id):
                    return idx + 1
    return None

# --- Twitch Bot ---
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

        self.add_command(self.versus)
        self.add_command(self.record)
        self.add_command(self.hi)

    async def event_ready(self):
        print(f"✅ Bot is online as: {self.nick if hasattr(self, 'nick') else self.user.name}")

        # Discord announce
        class DiscordAnnouncer(discord.Client):
            async def on_ready(self):
                print(f"✅ Discord bot ready as {self.user}")
                channel = self.get_channel(DISCORD_CHANNEL_ID)
                if channel:
                    try:
                        message = await channel.send("✅ - StimoBot is now online!")
                        await asyncio.sleep(60)
                        await message.delete()
                    except Exception as e:
                        print(f"[ERROR] Discord announce failed: {e}")
                await self.close()

        asyncio.create_task(DiscordAnnouncer(intents=discord.Intents.default()).start(DISCORD_TOKEN))

    async def event_message(self, message):
        print(f"[DEBUG] {message.author.name}: {message.content}")
        if message.echo or message.author is None:
            return
        await self.handle_commands(message)

    @commands.command(name="versus", aliases=["vs"])
    async def versus(self, ctx):
        # if not (ctx.author.is_mod or ctx.author.is_broadcaster or await is_vip(ctx.author.name)):
           # await ctx.send("🚫 You don't have permission to use this command.")
           # return

        args = ctx.message.content.split(" ", 1)
        if len(args) < 2:
            await ctx.send("Usage: !versus <Club Name or ID>")
            return

        search_input = args[1].strip()
        normalized_input = normalize(search_input)
        matched_club_id = None

        for cid, name in club_mapping.items():
            if normalize(name) == normalized_input:
                matched_club_id = cid
                break

        async with httpx.AsyncClient(timeout=10) as client:
            if not matched_club_id:
                if search_input.isdigit():
                    matched_club_id = search_input
                else:
                    search_url = f"https://proclubs.ea.com/api/fc/allTimeLeaderboard/search?platform={PLATFORM}&clubName={search_input}"
                    res = await client.get(search_url)
                    if res.status_code == 200 and isinstance(res.json(), list):
                        best = max(res.json(), key=lambda c: fuzz.token_set_ratio(search_input, c.get("clubInfo", {}).get("name", "")))
                        matched_club_id = str(best.get("clubInfo", {}).get("clubId"))

        if not matched_club_id:
            await ctx.send("Could not find matching club.")
            return

        stats = await get_club_stats(matched_club_id)
        recent_form = await get_recent_form(matched_club_id)
        rank = await get_club_rank(matched_club_id)

        if not stats:
            await ctx.send("Could not fetch opponent stats.")
            return

        club_name = stats.get("name", f"Club {matched_club_id}")
        form = " ".join(recent_form)
        message = (
            f"{club_name.upper()}'s Record | "
            f"📈 Rank: #{rank or 'Unranked'} | "
            f"🏅 SR: {stats.get('skillRating', 'N/A')} | "
            f"🎮 {stats.get('gamesPlayed', 'N/A')} | "
            f"✅ {stats.get('wins', 'N/A')} | "
            f"➖ {stats.get('ties', 'N/A')} | "
            f"❌ {stats.get('losses', 'N/A')} | "
            f"🔥 Win Streak: {stats.get('wstreak', '0')} {streak_emoji(stats.get('wstreak', 0))} | "
            f"🛡️ Unbeaten: {stats.get('unbeatenstreak', '0')} {streak_emoji(stats.get('unbeatenstreak', 0))} | "
            f"Recent Form: {form or 'No matches'}"
        )

        await ctx.send(message)

# --- Run Bot ---
if __name__ == "__main__":
    if not TOKEN or not TOKEN.startswith("oauth:"):
        print("❌ Invalid IRC token! Must start with 'oauth:'")
    else:
        bot = Bot()
        asyncio.run(bot.run())
