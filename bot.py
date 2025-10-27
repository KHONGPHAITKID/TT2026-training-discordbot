import asyncio
import logging
import os

import discord
from discord.ext import commands

from services import db
from services.utils import load_environment

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
LOGGER = logging.getLogger("bot")


class QuizBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix=self.determine_prefix, intents=intents, help_command=None)

    async def setup_hook(self) -> None:
        await self.load_extension("cogs.questions")
        await self.load_extension("cogs.stats")
        await self.load_extension("cogs.admin")
        LOGGER.info("Extensions loaded.")

    @staticmethod
    async def determine_prefix(bot: commands.Bot, message: discord.Message) -> str:
        # In the future this could be customised per guild.
        return "/"


async def main() -> None:
    load_environment()
    db.init_db()

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN environment variable is missing.")

    bot = QuizBot()
    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
