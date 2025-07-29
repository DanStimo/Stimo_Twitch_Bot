import os
from twitchio.ext import commands

TOKEN = os.getenv("TOKEN")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
BOT_ID = os.getenv("BOT_ID")  # must be a string or int
CHANNEL = os.getenv("CHANNEL")

class Bot(commands.Bot):

    def __init__(self):
        super().__init__(
            token=TOKEN,
            prefix="!",
            initial_channels=[CHANNEL.lower()],
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            bot_id=BOT_ID
        )

    async def event_ready(self):
        print(f"✅ Bot is online as: {self._connection.user.name}")

    async def event_message(self, message):
        print(f"[DEBUG] Message from {message.author.name}: {message.content}")
        await self.handle_commands(message)

    @commands.command(name='hi')
    async def hi_command(self, ctx):
        await ctx.send("Bye.")

if __name__ == "__main__":
    bot = Bot()
    bot.run()
