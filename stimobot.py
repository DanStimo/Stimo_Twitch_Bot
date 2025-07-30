import os
<<<<<<< HEAD
from twitchio.ext import commands
=======
import discord

BOT_NICK = os.getenv("BOT_NICK")
TOKEN = os.getenv("TOKEN")
CHANNEL = os.getenv("CHANNEL")
CLUB_ID = os.getenv("CLUB_ID")
PLATFORM = os.getenv("PLATFORM", "common-gen5")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID"))
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_ACCESS_TOKEN = os.getenv("TWITCH_ACCESS_TOKEN")
BROADCASTER_ID = os.getenv("BROADCASTER_ID")

discord_client = discord.Client(intents=discord.Intents.default())

# Load or initialize club mapping
try:
    with open('club_mapping.json', 'r') as f:
        club_mapping = json.load(f)
except FileNotFoundError:
    club_mapping = {}

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
    
async def update_club_mapping_from_recent_matches(club_id, platform='common-gen5'):
    url = f"https://proclubs.ea.com/api/fc/clubs/matches?matchType=leagueMatch&platform=common-gen5&clubIds=167054&matchType=gameType0"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                matches = response.json()
                updated = False
                for match in matches:
                    opponent = match.get('opponentClub', {})
                    opponent_id = str(opponent.get('clubId'))
                    opponent_name = opponent.get('name')
                    if opponent_id and opponent_name and opponent_id not in club_mapping:
                        club_mapping[opponent_id] = opponent_name
                        updated = True
                if updated:
                    with open('club_mapping.json', 'w') as f:
                        json.dump(club_mapping, f, indent=4)
            elif response.status_code == 404:
                print("Error: The requested resource was not found (404).")
            else:
                print(f"Failed to fetch recent matches, status code: {response.status_code}")
    except Exception as e:
        print(f"Error fetching recent matches: {e}")    

# Twitch VIP Check
async def is_vip(username):
    headers = {
        "Client-ID": TWITCH_CLIENT_ID,
        "Authorization": f"Bearer {TWITCH_ACCESS_TOKEN}"
    }
    url = f"https://api.twitch.tv/helix/channels/vips?broadcaster_id={BROADCASTER_ID}"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                vips = response.json()
                vip_list = vips.get("data", [])

                return any(vip["user_name"].lower() == username.lower() for vip in vip_list)
            else:
                print(f"[ERROR] VIP check failed: {response.status_code} - {response.text}")
        except Exception as e:
            print(f"[ERROR] Exception during VIP check: {e}")
    return False

async def get_broadcaster_id():
    headers = {
        "Client-ID": TWITCH_CLIENT_ID,
        "Authorization": f"Bearer {TWITCH_ACCESS_TOKEN}"
    }
    url = "https://api.twitch.tv/helix/users"

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        if response.status_code == 200:
            user_data = response.json()
            print(f"✅ Your Broadcaster ID: {user_data['data'][0]['id']}")
        else:
            print(f"[ERROR] Failed to fetch broadcaster ID: {response.status_code} - {response.text}")

async def get_club_stats():
    url = f"https://proclubs.ea.com/api/fc/clubs/overallStats?platform=common-gen5&clubIds=167054"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list) and len(data) > 0:
                    club = data[0]
                    return {
                        "skillRating": club.get("skillRating", "N/A"),
                        "matchesPlayed": club.get("gamesPlayed", "N/A"),
                        "wins": club.get("wins", "N/A"),
                        "draws": club.get("ties", "N/A"),
                        "losses": club.get("losses", "N/A"),
                        "winStreak": club.get("wstreak", "0"),
                        "unbeatenStreak": club.get("unbeatenstreak", "0")
                    }
            else:
                print(f"Failed to fetch stats, status code: {response.status_code}")
    except Exception as e:
        print(f"Error fetching stats: {e}")
    return None

async def get_recent_form(club_id):
    base_url = "https://proclubs.ea.com/api/fc/clubs/matches"
    headers = {"User-Agent": "Mozilla/5.0"}
    match_types = ["leagueMatch", "playoffMatch"]
    all_matches = []

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            for match_type in match_types:
                url = f"{base_url}?matchType={match_type}&platform={PLATFORM}&clubIds={club_id}"
                response = await client.get(url, headers=headers)
                if response.status_code == 200:
                    matches = response.json()
                    all_matches.extend(matches)

        # Sort by match timestamp (most recent first)
        all_matches.sort(key=lambda x: x.get("timestamp", 0), reverse=True)

        results = []
        for match in all_matches[:5]:
            clubs_data = match.get("clubs", {})
            club_data = clubs_data.get(str(club_id))
            opponent_id = next((cid for cid in clubs_data if cid != str(club_id)), None)
            opponent_data = clubs_data.get(opponent_id) if opponent_id else None

            if not club_data or not opponent_data:
                continue

            our_score = int(club_data.get("goals", 0))
            opponent_score = int(opponent_data.get("goals", 0))

            if our_score > opponent_score:
                results.append("✅")
            elif our_score < opponent_score:
                results.append("❌")
            else:
                results.append("➖")

        return results

    except Exception as e:
        print(f"[ERROR] Failed to fetch recent form: {e}")
        return []

async def get_last_match(club_id):
    base_url = "https://proclubs.ea.com/api/fc/clubs/matches"
    headers = {"User-Agent": "Mozilla/5.0"}
    match_types = ["leagueMatch", "playoffMatch"]
    all_matches = []

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            for match_type in match_types:
                url = f"{base_url}?matchType={match_type}&platform={PLATFORM}&clubIds={club_id}"
                response = await client.get(url, headers=headers)
                if response.status_code == 200:
                    matches = response.json()
                    all_matches.extend(matches)

        all_matches.sort(key=lambda x: x.get("timestamp", 0), reverse=True)

        if not all_matches:
            return "Last match data not available."

        match = all_matches[0]
        clubs_data = match.get("clubs", {})
        club_data = clubs_data.get(str(club_id))
        opponent_id = next((cid for cid in clubs_data if cid != str(club_id)), None)
        opponent_data = clubs_data.get(opponent_id) if opponent_id else None

        if not club_data or not opponent_data:
            return "Last match data not available."

        # ✅ Safely pull opponent name from multiple possible sources
        opponent_name = (
            opponent_data.get("name")
            or opponent_data.get("details", {}).get("name")
            or match.get("opponentClub", {}).get("name", "Unknown")
        )

        our_score = int(club_data.get("goals", 0))
        opponent_score = int(opponent_data.get("goals", 0))

        result = "✅" if our_score > opponent_score else "❌" if our_score < opponent_score else "➖"
        return f"{result} - {opponent_name} ({our_score}-{opponent_score})"

    except Exception as e:
        print(f"[ERROR] Failed to fetch last match: {e}")
        return "Last match data not available."

async def get_club_rank(club_id):
    url = f"https://proclubs.ea.com/api/fc/allTimeLeaderboard?platform={PLATFORM}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                for idx, entry in enumerate(data):
                    if str(entry.get("clubInfo", {}).get("clubId")) == str(club_id):
                        return idx + 1  # leaderboard is 0-indexed
            return None
    except Exception as e:
        print(f"[ERROR] Failed to fetch leaderboard rank: {e}")
        return None

>>>>>>> parent of fcc2b13 (Update stimobot.py)

class Bot(commands.Bot):
    def __init__(self):
        super().__init__(
            token=os.getenv("TOKEN"),
            client_id=os.getenv("CLIENT_ID"),
            client_secret=os.getenv("TWITCH_CLIENT_SECRET"),
            prefix="!",
            initial_channels=[os.getenv("CHANNEL").lower()],
            bot_id=int(os.getenv("BOT_ID"))
        )

    async def event_ready(self):
        print(f"✅ Bot is online!")

    async def event_message(self, message):
        print(f"[CHAT] {message.author.name}: {message.content}")
        await self.handle_commands(message)

    @commands.command(name="hi")
    async def hi_command(self, ctx):
        await ctx.send("Bye")

if __name__ == "__main__":
    bot = Bot()
    bot.run()
