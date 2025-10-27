import asyncio
import logging
import os
from datetime import datetime
from typing import Dict, Optional

import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from discord.ext import commands

from services import db
from services.groq_client import GroqClient, QuestionPayload
from services.utils import normalise_answer

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore

LOGGER = logging.getLogger(__name__)


class QuestionCog(commands.Cog):
    """Handles question generation, scheduling, and answer evaluation."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.client = GroqClient()
        tz_name = os.getenv("BOT_TIMEZONE", "UTC")
        self.timezone = self._safe_timezone(tz_name)
        self.scheduler = AsyncIOScheduler(timezone=self.timezone)
        self.active_questions: Dict[int, int] = {}  # channel_id -> question_id
        self.publish_lock = asyncio.Lock()

    async def cog_load(self) -> None:
        self.scheduler.start()
        self._schedule_daily_question()
        await self._hydrate_active_questions()
        LOGGER.info("QuestionCog loaded. Scheduler active.")

    async def cog_unload(self) -> None:
        self.scheduler.shutdown(wait=False)
        LOGGER.info("QuestionCog unloaded. Scheduler stopped.")

    def _schedule_daily_question(self) -> None:
        cron_expression = os.getenv("DAILY_QUESTION_CRON")
        if cron_expression:
            trigger = CronTrigger.from_crontab(cron_expression, timezone=self.timezone)
        else:
            trigger = CronTrigger(hour=7, minute=0, timezone=self.timezone)
        self.scheduler.add_job(
            self._dispatch_daily_questions,
            trigger=trigger,
            id="daily-question",
            replace_existing=True,
        )

    @staticmethod
    def _safe_timezone(name: str):
        try:
            return ZoneInfo(name)
        except Exception:  # pragma: no cover - fallback for invalid config
            LOGGER.warning("Unknown timezone '%s', defaulting to UTC.", name)
            return ZoneInfo("UTC")

    async def _hydrate_active_questions(self) -> None:
        for guild in self.bot.guilds:
            for channel in guild.text_channels:
                question = db.get_latest_question_for_channel(channel.id)
                if question and (datetime.utcnow() - question.created_at).days < 1:
                    if db.get_first_correct_response(question.id):
                        continue
                    self.active_questions[channel.id] = question.id

    async def _dispatch_daily_questions(self) -> None:
        LOGGER.info("Running scheduled daily question dispatch.")
        for guild in self.bot.guilds:
            config = db.get_guild_config(guild.id)
            channel = None
            if config.daily_channel_id:
                channel = guild.get_channel(config.daily_channel_id)
            if channel is None:
                channel = self._auto_select_channel(guild)
            if not channel:
                LOGGER.warning("No suitable channel found for guild %s.", guild.id)
                continue
            await self.publish_question(channel)

    def _auto_select_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        for channel in guild.text_channels:
            if channel.permissions_for(guild.me).send_messages:
                return channel
        return None

    async def publish_question(self, channel: discord.abc.Messageable, topic: Optional[str] = None) -> None:
        async with self.publish_lock:
            payload = self.client.generate_question(topic)
            question_record = db.record_question(
                topic=payload.topic,
                prompt=payload.question,
                options=payload.options,
                correct_answer=payload.answer,
                explanation=payload.explanation,
                channel_id=getattr(channel, "id", None),
            )

            embed = self._build_question_embed(question_record, payload)
            footer = "Reply with A, B, C, or D. First correct answer earns 10 points."

            if isinstance(channel, discord.TextChannel):
                message = await channel.send(content=footer, embed=embed)
                db.attach_message_id(question_record.id, message.id)
                self.active_questions[channel.id] = question_record.id
            else:
                await channel.send(content=footer, embed=embed)
                if hasattr(channel, "id"):
                    self.active_questions[channel.id] = question_record.id

    def _build_question_embed(self, record, payload: QuestionPayload) -> discord.Embed:
        embed = discord.Embed(
            title=f"Daily CS Quiz · {payload.topic}",
            description=payload.question,
            color=discord.Color.blue(),
            timestamp=datetime.utcnow(),
        )
        for option_key in ("A", "B", "C", "D"):
            option_value = payload.options.get(option_key, "—")
            embed.add_field(name=option_key, value=option_value, inline=False)
        embed.set_footer(text="Think carefully before answering!")
        return embed

    @commands.hybrid_command(name="new_question", with_app_command=True, description="Request a new CS question.")
    async def new_question_command(self, ctx: commands.Context, *, topic: Optional[str] = None) -> None:
        """Allow users to manually request a new question."""
        if not ctx.guild:
            await ctx.reply("This command is only available in servers.")
            return

        interaction = getattr(ctx, "interaction", None)
        if interaction and not interaction.response.is_done():
            await interaction.response.defer(thinking=True)

        await self.publish_question(ctx.channel, topic)
        if interaction and interaction.response.is_done():
            await interaction.followup.send("New question posted! Good luck!", ephemeral=False)
        else:
            await ctx.reply("New question posted! Good luck!", mention_author=False)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return

        channel_id = message.channel.id
        question_id = self.active_questions.get(channel_id)
        if not question_id:
            return

        canonical_choice = normalise_answer(message.content)
        if canonical_choice not in ("A", "B", "C", "D"):
            return

        question = db.get_question(question_id)
        if not question:
            self.active_questions.pop(channel_id, None)
            return

        first_correct = db.get_first_correct_response(question.id)
        if first_correct:
            self.active_questions.pop(channel_id, None)
            solver_member = message.guild.get_member(first_correct.user_id)
            solver_reference = solver_member.mention if solver_member else f"<@{first_correct.user_id}>"
            await self._safe_react(message, "⛔")
            if first_correct.user_id != message.author.id:
                await message.channel.send(
                    f"This one was already solved by {solver_reference}. Request another with `/question`."
                )
            return

        if db.has_user_answered(question.id, message.author.id):
            await self._safe_react(message, "⛔")
            return

        is_correct = canonical_choice == question.correct_answer
        db.record_response(
            question_id=question.id,
            user_id=message.author.id,
            username=str(message.author),
            answer=canonical_choice,
            is_correct=is_correct,
        )

        if is_correct:
            await self._safe_react(message, "✅")
            self.active_questions.pop(channel_id, None)
            response = (
                f"{message.author.mention} got it first! +10 points."
                f" Correct answer: **{question.correct_answer}**."
            )
            if question.explanation:
                response += f"\nℹ️ {question.explanation}"
            await message.channel.send(response)
        else:
            await self._safe_react(message, "❌")

    @commands.hybrid_command(name="show_question", with_app_command=True, description="Re-post the active question.")
    async def show_question(self, ctx: commands.Context) -> None:
        channel_id = ctx.channel.id
        question_id = self.active_questions.get(channel_id)
        if not question_id:
            latest = db.get_latest_question_for_channel(channel_id)
            if not latest:
                await ctx.reply("No question available to display.")
                return
            question_id = latest.id
            self.active_questions[channel_id] = question_id

        question = db.get_question(question_id)
        if not question:
            await ctx.reply("Unable to load the question from storage.")
            return

        payload = QuestionPayload(
            topic=question.topic,
            question=question.prompt,
            options=question.options,
            answer=question.correct_answer,
            explanation=question.explanation,
        )
        embed = self._build_question_embed(question, payload)
        await ctx.reply("Here is the current question:", embed=embed)

    async def _safe_react(self, message: discord.Message, emoji: str) -> None:
        try:
            await message.add_reaction(emoji)
        except discord.HTTPException:
            LOGGER.debug("Failed to add reaction %s to message %s", emoji, message.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(QuestionCog(bot))
