import asyncio
import httpx
import json
from twitchio.ext import commands
from rapidfuzz import process, fuzz
import os
import discord
from dotenv import load_dotenv

load_dotenv()

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
                vips = response.json().get("data", [])
                return any(vip["user_name"].lower() == username.lower() for vip in vips)
            else:
                print(f"[ERROR] VIP check failed: {response.status_code} - {response.text}")
        except Exception as e:
            print(f"[ERROR] Exception during VIP check: {e}")
    return False

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


class Bot(commands.Bot):

    def __init__(self):
        super().__init__(token=TOKEN, prefix='!', initial_channels=[CHANNEL])

    async def event_ready(self):
        print(f"Logged in as | {self.nick}")
        await update_club_mapping_from_recent_matches(167054)
    
        # Start Discord client just long enough to send the message
        async def announce_in_discord():
            await discord_client.wait_until_ready()
            channel = discord_client.get_channel(DISCORD_CHANNEL_ID)
            if channel:
                await channel.send("✅ - StimoBot (<:twitch:1361925662008541266>) is now online and ready for commands!")
            await discord_client.close()
    
        # Start Discord client in background
        asyncio.create_task(announce_in_discord())
        await discord_client.start(DISCORD_TOKEN)

    async def event_message(self, message):
        if message.echo or message.author is None:
            return
        await self.handle_commands(message) 

    @commands.command(name='record')
    async def record(self, ctx):
        stats = await get_club_stats()
        recent_form = await get_recent_form(CLUB_ID)
        last_match = await get_last_match(CLUB_ID)
        rank = await get_club_rank(CLUB_ID)
        form_string = ' '.join(recent_form) if recent_form else "No recent matches found."
    
        if stats:
            await ctx.send(
                f"Wingus FC Record | "
                f"📈 Rank: {rank} | "
                f"🏅 SR: {stats['skillRating']} | "
                f"🎮: {stats['matchesPlayed']} | "
                f"✅: {stats['wins']} | "
                f"➖: {stats['draws']} | "
                f"❌: {stats['losses']} | "
                f"🔥 Win Streak: {stats['winStreak']} {streak_emoji(stats['winStreak'])} | "
                f"🛡️ Unbeaten Streak: {stats['unbeatenStreak']} {streak_emoji(stats['unbeatenStreak'])} | "
                f"🕹️ Last Match: {last_match} | "
                f"Recent Form: {form_string}"
            )
        else:
            await ctx.send("Could not fetch club stats. EA's servers might be down or the data is unavailable.")


    @commands.command(name='versus', aliases=['vs'])
    async def versus(self, ctx):
        if not (ctx.author.is_mod or ctx.author.is_broadcaster or await is_vip(ctx.author.name)):
        await ctx.send("🚫 You don't have permission to use this command.")
        return
        
        args = ctx.message.content.split(" ", 1)
        if len(args) != 2:
            await ctx.send("Usage: !versus <Club Name or Club ID>")
            return
    
        search_input = args[1].strip()
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                normalized_input = normalize(search_input)
                matched_club_id = None
                for club_id, club_name in club_mapping.items():
                    if normalize(club_name) == normalized_input:
                        matched_club_id = club_id
                        break
    
                if matched_club_id:
                    opponent_id = matched_club_id
                    club_name_formatted = club_mapping[opponent_id].upper()
                elif search_input.isdigit():
                    opponent_id = search_input
                    club_name_formatted = club_mapping.get(opponent_id, f"CLUB ID {opponent_id}")
                else:
                    club_name_encoded = search_input.replace(" ", "%20")
                    search_url = f"https://proclubs.ea.com/api/fc/allTimeLeaderboard/search?platform=common-gen5&clubName={club_name_encoded}"
                    search_response = await client.get(search_url, headers=headers)
    
                    if search_response.status_code != 200:
                        await ctx.send("Club not found or EA search API failed.")
                        return
    
                    search_data = search_response.json()
                    if not search_data or not isinstance(search_data, list):
                        await ctx.send("No matching clubs found.")
                        return
    
                    club_names = [club.get("clubInfo", {}).get("name", "") for club in search_data]
                    matches = process.extract(search_input, club_names, scorer=fuzz.token_set_ratio, limit=5)
                    good_matches = [match for match in matches if match[1] >= 5]
    
                    if not good_matches:
                        await ctx.send(f"No clubs found that match '{search_input}'.")
                        return
    
                    match_list = ', '.join([f"{name} ({round(score)}%)" for name, score, _ in good_matches])
                    await ctx.send(f"Did you mean: {match_list}?")
    
                    best_match_name = good_matches[0][0]
                    club = next((club for club in search_data if club.get("clubInfo", {}).get("name", "") == best_match_name), None)
    
                    if not club:
                        await ctx.send("Could not retrieve club data.")
                        return
    
                    opponent_id = str(club.get("clubInfo", {}).get("clubId"))
                    club_name_formatted = best_match_name.upper()
    
                if opponent_id not in club_mapping:
                    match_url = f"https://proclubs.ea.com/api/fc/clubs/matches?matchType=leagueMatch&platform=common-gen5&clubIds={CLUB_ID}"
                    try:
                        match_response = await client.get(match_url, headers=headers)
                        if match_response.status_code == 200:
                            matches = match_response.json()
                            for match in matches:
                                clubs_data = match.get("clubs", {})
                                for cid, cdata in clubs_data.items():
                                    if cid != str(CLUB_ID) and cid == opponent_id:
                                        name = cdata.get("details", {}).get("name")
                                        if name:
                                            club_mapping[opponent_id] = name
                                            with open('club_mapping.json', 'w') as f:
                                                json.dump(club_mapping, f, indent=4)
                                            club_name_formatted = name.upper()
                    except Exception as e:
                        print(f"[ERROR] Couldn't auto-update club_mapping from match history: {e}")
    
                if opponent_id not in club_mapping:
                    club_mapping[opponent_id] = best_match_name
                    with open('club_mapping.json', 'w') as f:
                        json.dump(club_mapping, f, indent=4)
    
                stats_url = f"https://proclubs.ea.com/api/fc/clubs/overallStats?platform=common-gen5&clubIds={opponent_id}"
                stats_response = await client.get(stats_url, headers=headers)
    
                if stats_response.status_code == 200:
                    stats_data = stats_response.json()
                    if isinstance(stats_data, list) and len(stats_data) > 0:
                        opp_stats = stats_data[0]
                        win_streak = opp_stats.get('wstreak', '0')
                        unbeaten_streak = opp_stats.get('unbeatenstreak', '0')
                        skill_rating = opp_stats.get('skillRating', 'N/A')
    
                        rank = await get_club_rank(opponent_id)
                        rank_display = f"📈 Rank: #{rank}" if rank else "📈 Rank: Unranked"
    
                        recent_form = await get_recent_form(opponent_id)
                        last_match = await get_last_match(opponent_id)
                        form_string = ' '.join(recent_form) if recent_form else "No recent matches found."
    
                        message = (
                            f"{club_name_formatted}'s Record | "
                            f"{rank_display} | "
                            f"🏅 SR: {skill_rating} | "
                            f"🎮: {opp_stats.get('gamesPlayed', 'N/A')} | "
                            f"✅: {opp_stats.get('wins', 'N/A')} | "
                            f"➖: {opp_stats.get('ties', 'N/A')} | "
                            f"❌: {opp_stats.get('losses', 'N/A')} | "
                            f"🔥 Win Streak: {win_streak} {streak_emoji(win_streak)} | "
                            f"🛡️ Unbeaten Streak: {unbeaten_streak} {streak_emoji(unbeaten_streak)} | "
                            f"🕹️ Last Match: {last_match} | "
                            f"Recent Form: {form_string}"
                        )
    
                        await ctx.send(message)
                    else:
                        await ctx.send("Opponent stats not found.")
                else:
                    await ctx.send("Could not fetch opponent stats.")
    
            except Exception as e:
                print(f"Error in !versus command: {e}")
                await ctx.send("An error occurred while fetching opponent stats.")



if __name__ == "__main__":
    bot = Bot()
    asyncio.run(bot.run())
