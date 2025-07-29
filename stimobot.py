import os
from twitchio.ext import commands

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
