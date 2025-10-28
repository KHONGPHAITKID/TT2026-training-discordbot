import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

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

OPTION_LABELS = {"A": "Option A", "B": "Option B", "C": "Option C", "D": "Option D"}
QUESTION_COOLDOWN_SECONDS = 7  # Cooldown between question generations


@dataclass
class AnswerResult:
    status: str
    question: Optional[Any]
    choice: str
    option_text: str = ""
    explanation: Optional[str] = None
    solver_id: Optional[int] = None
    correct: bool = False
    difficulty: Optional[str] = None
    model_name: Optional[str] = None


class QuestionCog(commands.Cog):
    """Handles question generation, scheduling, and answer evaluation."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.client = GroqClient()
        tz_name = os.getenv("BOT_TIMEZONE", "UTC")
        self.timezone = self._safe_timezone(tz_name)
        self.scheduler = AsyncIOScheduler(timezone=self.timezone)
        self.active_questions: Dict[int, int] = {}  # channel_id -> question_id
        self.active_views: Dict[int, "AnswerButtons"] = {}
        self.question_meta: Dict[int, Dict[str, str]] = {}
        self.publish_lock = asyncio.Lock()
        self.last_question_time: Dict[int, float] = {}  # channel_id -> timestamp

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
                    self._get_question_metadata(question)
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

    @staticmethod
    @staticmethod
    def _parse_prompt_metadata(prompt: str) -> Tuple[Dict[str, str], str]:
        meta: Dict[str, str] = {}
        remainder = prompt
        while remainder.startswith("["):
            end = remainder.find("]")
            if end == -1:
                break
            segment = remainder[1:end]
            if ':' in segment:
                key, value = segment.split(':', 1)
                meta[key.strip().lower()] = value.strip()
            remainder = remainder[end + 1 :].lstrip()
        return meta, remainder

    def _get_question_metadata(self, question) -> Dict[str, str]:
        stored = dict(self.question_meta.get(question.id, {}))
        meta, cleaned = self._parse_prompt_metadata(question.prompt or "")
        if cleaned != question.prompt:
            question.prompt = cleaned
        for key, value in meta.items():
            if value:
                stored[key] = value
        self.question_meta[question.id] = stored
        return stored

    @staticmethod
    def _extract_options(question) -> Dict[str, str]:
        options = question.options or {}
        if isinstance(options, str):
            try:
                options = json.loads(options)
            except json.JSONDecodeError:
                options = {}
        normalised = {}
        for key, value in options.items():
            normalised[str(key).upper()] = str(value)
        return normalised

    @staticmethod
    def _split_text_into_chunks(text: str, max_length: int) -> list:
        """Split text into chunks at word boundaries, respecting max_length."""
        if len(text) <= max_length:
            return [text]

        chunks = []
        current_chunk = ""

        # Split by paragraphs first (double newline)
        paragraphs = text.split("\n\n")

        for para in paragraphs:
            # If paragraph itself is too long, split by sentences
            if len(para) > max_length:
                sentences = para.replace(". ", ".|").replace(".\n", ".\n|").split("|")
                for sentence in sentences:
                    if len(current_chunk) + len(sentence) + 2 <= max_length:
                        current_chunk += sentence
                    else:
                        if current_chunk:
                            chunks.append(current_chunk.strip())
                        current_chunk = sentence
            else:
                # Try to add whole paragraph
                if len(current_chunk) + len(para) + 2 <= max_length:
                    current_chunk += ("\n\n" if current_chunk else "") + para
                else:
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                    current_chunk = para

        # Add any remaining text
        if current_chunk:
            chunks.append(current_chunk.strip())

        return chunks

    def _resolve_question_for_channel(self, channel_id: int) -> Optional[Any]:
        question_id = self.active_questions.get(channel_id)
        question = None
        if question_id:
            question = db.get_question(question_id)
        if not question:
            question = db.get_latest_question_for_channel(channel_id)
            if question:
                self.active_questions[channel_id] = question.id
        if question:
            self._get_question_metadata(question)
        return question

    def _submit_answer(
        self,
        *,
        user: discord.Member,
        choice: str,
        channel_id: Optional[int] = None,
        question_id: Optional[int] = None,
    ) -> AnswerResult:
        choice = choice.upper()
        if question_id:
            question = db.get_question(question_id)
            if not question:
                return AnswerResult(status="no_question", question=None, choice=choice)
            channel_id = question.channel_id or channel_id
        elif channel_id is not None:
            question = self._resolve_question_for_channel(channel_id)
        else:
            question = None

        if not question or channel_id is None:
            return AnswerResult(status="no_question", question=None, choice=choice)

        meta = self._get_question_metadata(question)
        difficulty = meta.get("difficulty", "Unknown")
        model_name = meta.get("model", "Unknown")

        # Check if question has already been solved by anyone
        first_correct = db.get_first_correct_response(question.id)
        if first_correct:
            self.active_questions.pop(channel_id, None)
            return AnswerResult(
                status="already_solved",
                question=question,
                choice=choice,
                solver_id=first_correct.user_id,
                difficulty=difficulty,
                model_name=model_name,
            )

        if db.has_user_answered(question.id, user.id):
            return AnswerResult(status="already_answered", question=question, choice=choice, difficulty=difficulty, model_name=model_name)

        options = self._extract_options(question)
        option_text = options.get(choice, "")
        is_correct = choice == (question.correct_answer or "").upper()

        db.record_response(
            question_id=question.id,
            user_id=user.id,
            username=str(user),
            answer=choice,
            is_correct=is_correct,
        )

        if is_correct:
            self.active_questions.pop(channel_id, None)
            return AnswerResult(
                status="correct",
                question=question,
                choice=choice,
                option_text=option_text,
                explanation=question.explanation,
                solver_id=user.id,
                correct=True,
                difficulty=difficulty,
                model_name=model_name,
            )

        return AnswerResult(
            status="incorrect",
            question=question,
            choice=choice,
            option_text=option_text,
            difficulty=difficulty,
            model_name=model_name,
        )

    async def _disable_active_view(self, channel_id: int) -> None:
        view = self.active_views.pop(channel_id, None)
        if not view:
            return
        view.disable_all_items()
        message_id = getattr(view, "message_id", None)
        if message_id is None:
            return
        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return
        try:
            message = await channel.fetch_message(message_id)
        except (discord.NotFound, discord.HTTPException):
            return
        try:
            await message.edit(view=view)
        except discord.HTTPException:
            return

    def _build_public_correct_embed(self, member: discord.Member, result: AnswerResult) -> Optional[discord.Embed]:
        question = result.question
        if not question:
            return None
        option_text = result.option_text or "No option text recorded."
        # Replace literal \n with actual newlines
        option_text = option_text.replace("\\n", "\n")
        label = OPTION_LABELS.get(result.choice, f"Option {result.choice}")
        embed = discord.Embed(
            title="Correct Answer!",
            description=f"{member.mention} chose **{label}** and earned **+10 points**!",
            color=discord.Color.from_rgb(28, 187, 140),
        )
        # Split long option texts if needed
        if len(option_text) <= 1024:
            embed.add_field(name="Answer Text", value=option_text, inline=False)
        else:
            chunks = self._split_text_into_chunks(option_text, 1024)
            for idx, chunk in enumerate(chunks):
                field_name = "Answer Text" if idx == 0 else "Answer Text (continued)"
                embed.add_field(name=field_name, value=chunk, inline=False)
        if result.difficulty:
            embed.add_field(name="Difficulty", value=result.difficulty, inline=True)
        if result.model_name:
            embed.add_field(name="Generated By", value=f"model {result.model_name}", inline=True)
        if result.explanation:
            # Replace literal \n with actual newlines
            explanation = result.explanation.replace("\\n", "\n")
            # Split long explanations across multiple fields (max 1024 chars per field)
            if len(explanation) <= 1024:
                embed.add_field(name="Explanation", value=explanation, inline=False)
            else:
                # Split into chunks at word boundaries
                chunks = self._split_text_into_chunks(explanation, 1024)
                for idx, chunk in enumerate(chunks):
                    field_name = "Explanation" if idx == 0 else "Explanation (continued)"
                    embed.add_field(name=field_name, value=chunk, inline=False)
        embed.set_footer(text="Ready for the next challenge? Click the button below!")
        return embed

    def _build_already_solved_embed(self, guild: discord.Guild, solver_id: Optional[int]) -> discord.Embed:
        solver_reference = "someone"
        if solver_id:
            member = guild.get_member(solver_id)
            solver_reference = member.mention if member else f"<@{solver_id}>"
        embed = discord.Embed(
            title="Question Already Solved",
            description=f"This puzzle was already solved by {solver_reference}. Try `/question` to fetch a new one.",
            color=discord.Color.orange(),
        )
        return embed

    async def publish_question(self, channel: discord.abc.Messageable, topic: Optional[str] = None) -> None:
        async with self.publish_lock:
            payload = self.client.generate_question(topic)
            difficulty = payload.difficulty or "Medium"
            model_name = payload.model_name or "Unknown"
            db_prompt = f"[Difficulty: {difficulty}][Model: {model_name}] {payload.question}"
            question_record = db.record_question(
                topic=payload.topic,
                prompt=db_prompt,
                options=payload.options,
                correct_answer=payload.answer,
                explanation=payload.explanation,
                channel_id=getattr(channel, "id", None),
            )

            self.question_meta[question_record.id] = {"difficulty": difficulty, "model": model_name}

            # Add icon based on difficulty
            difficulty_icons = {
                "Easy": "🟢",
                "Medium": "🟡",
                "Hard": "🔴"
            }
            difficulty_icon = difficulty_icons.get(difficulty, "⚪")

            embed = self._build_question_embed(payload, difficulty, model_name)
            intro = (
                f"**Daily CS Quiz** {difficulty_icon} - tap a button below to answer. First correct reply wins 10 pts!\n"
                f"{difficulty_icon} **Difficulty:** {difficulty} | 🤖 **Generated by:** {model_name}"
            )

            channel_id = getattr(channel, "id", None)
            view = AnswerButtons(self, question_record.id, channel_id) if channel_id is not None else None
            message = await channel.send(content=intro, embed=embed, view=view)

            if channel_id is not None:
                self.active_questions[channel_id] = question_record.id
                # Record timestamp for cooldown tracking
                self.last_question_time[channel_id] = time.time()
                if view:
                    view.message_id = message.id
                    self.active_views[channel_id] = view
            if isinstance(channel, (discord.TextChannel, discord.Thread)):
                db.attach_message_id(question_record.id, message.id)

    def _build_question_embed(
        self,
        payload: QuestionPayload,
        difficulty: Optional[str],
        model: Optional[str],
    ) -> discord.Embed:
        # Replace literal \n with actual newlines
        question_text = payload.question.replace("\\n", "\n")

        # Truncate question if it exceeds Discord's 4096 char description limit
        if len(question_text) > 4000:
            question_text = question_text[:3997] + "..."

        embed = discord.Embed(
            title=payload.topic,
            description=f"**Question**\n{question_text}",
            color=discord.Color.from_rgb(76, 110, 245),
            timestamp=datetime.utcnow(),
        )
        embed.set_author(name="Daily Computer Science Quiz")

        # Add options as fields, splitting if they exceed 1024 chars
        for option_key in ("A", "B", "C", "D"):
            option_value = payload.options.get(option_key, "-")
            # Replace literal \n with actual newlines in options too
            option_value = option_value.replace("\\n", "\n")
            label = OPTION_LABELS.get(option_key, f"Option {option_key}")

            # Split long options into multiple fields if needed
            if len(option_value) <= 1024:
                embed.add_field(name=label, value=option_value, inline=False)
            else:
                # Split into chunks
                chunks = self._split_text_into_chunks(option_value, 1024)
                for idx, chunk in enumerate(chunks):
                    field_name = label if idx == 0 else f"{label} (continued)"
                    embed.add_field(name=field_name, value=chunk, inline=False)

        return embed

    def _build_answer_embed(
        self,
        question,
        guild: Optional[discord.Guild],
        difficulty: Optional[str],
        model: Optional[str],
    ) -> discord.Embed:
        options = self._extract_options(question)
        correct_letter = (question.correct_answer or "A").upper()
        correct_text = options.get(correct_letter, "No option text recorded.")
        # Replace literal \n with actual newlines
        correct_text = correct_text.replace("\\n", "\n")
        label = OPTION_LABELS.get(correct_letter, f"Option {correct_letter}")

        embed = discord.Embed(
            title=f"Answer Sheet - {question.topic}",
            color=discord.Color.from_rgb(28, 187, 140),
            timestamp=datetime.utcnow(),
        )

        # Check if correct answer text is too long
        correct_answer_value = f"{label}\n{correct_text}"
        if len(correct_answer_value) <= 1024:
            embed.add_field(name="Correct Answer", value=correct_answer_value, inline=False)
        else:
            # Split into chunks if too long
            chunks = self._split_text_into_chunks(correct_text, 1000)  # Leave room for label
            for idx, chunk in enumerate(chunks):
                field_name = "Correct Answer" if idx == 0 else "Correct Answer (continued)"
                field_value = f"{label}\n{chunk}" if idx == 0 else chunk
                embed.add_field(name=field_name, value=field_value, inline=False)

        embed.add_field(name="Difficulty", value=difficulty or "Unknown", inline=True)
        embed.add_field(name="Generated By", value=f"model {model or 'Unknown'}", inline=True)

        if question.explanation:
            # Replace literal \n with actual newlines in explanation too
            explanation = question.explanation.replace("\\n", "\n")
            # Split long explanations across multiple fields (max 1024 chars per field)
            if len(explanation) <= 1024:
                embed.add_field(name="Why?", value=explanation, inline=False)
            else:
                # Split into chunks at word boundaries
                chunks = self._split_text_into_chunks(explanation, 1024)
                for idx, chunk in enumerate(chunks):
                    field_name = "Why?" if idx == 0 else "Why? (continued)"
                    embed.add_field(name=field_name, value=chunk, inline=False)

        # Present options two per row for readability
        for option_key in ("A", "B", "C", "D"):
            option_value = options.get(option_key, "-")
            # Replace literal \n with actual newlines
            option_value = option_value.replace("\\n", "\n")
            field_label = OPTION_LABELS.get(option_key, f"Option {option_key}")

            # Truncate long options to fit field limit (1024 chars)
            if len(option_value) > 1024:
                option_value = option_value[:1020] + "..."

            embed.add_field(
                name=field_label,
                value=option_value,
                inline=True,
            )

        first_correct = db.get_first_correct_response(question.id)
        if first_correct:
            display_name = f"<@{first_correct.user_id}>"
            if guild:
                member = guild.get_member(first_correct.user_id)
                if member:
                    display_name = member.mention
            embed.add_field(name="Winner", value=display_name, inline=False)

        created_at = question.created_at.strftime("%b %d, %Y %H:%M UTC")
        embed.set_footer(text=f"Question posted on {created_at}")
        return embed

    def get_answer_embed_for_channel(self, channel: discord.abc.Messageable, guild: Optional[discord.Guild]):
        channel_id = getattr(channel, "id", None)
        if channel_id is None:
            return None
        question = db.get_latest_question_for_channel(channel_id)
        if not question:
            return None
        meta = self._get_question_metadata(question)
        difficulty = meta.get("difficulty", "Unknown")
        model = meta.get("model", "Unknown")
        return self._build_answer_embed(question, guild, difficulty, model)

    async def _handle_question_request(self, ctx: commands.Context, topic: Optional[str]) -> None:
        """Allow users to manually request a new question."""
        if not ctx.guild:
            await ctx.reply("This command is only available in servers.")
            return

        # Check cooldown
        channel_id = ctx.channel.id
        current_time = time.time()
        last_time = self.last_question_time.get(channel_id, 0)
        time_elapsed = current_time - last_time

        if time_elapsed < QUESTION_COOLDOWN_SECONDS:
            remaining = QUESTION_COOLDOWN_SECONDS - time_elapsed
            await ctx.reply(
                f"⏱️ Slow down! Please wait {remaining:.1f} more seconds before generating the next question.",
                ephemeral=True,
                delete_after=5
            )
            return

        interaction = getattr(ctx, "interaction", None)
        if interaction and not interaction.response.is_done():
            await interaction.response.defer(thinking=True)

        await self.publish_question(ctx.channel, topic)
        # Silently post the question without extra confirmation messages

    @commands.hybrid_command(name="question", with_app_command=True, description="Request a new CS question.")
    async def question_command(self, ctx: commands.Context, *, topic: Optional[str] = None) -> None:
        await self._handle_question_request(ctx, topic)

    @commands.hybrid_command(name="q", with_app_command=True, description="Alias for /question.")
    async def q_command(self, ctx: commands.Context, *, topic: Optional[str] = None) -> None:
        await self._handle_question_request(ctx, topic)

    @commands.hybrid_command(name="ans", with_app_command=True, description="Reveal the most recent question's answer.")
    async def answer_command(self, ctx: commands.Context) -> None:
        if not ctx.guild:
            await ctx.reply("Use this command inside a server channel.")
            return

        embed = self.get_answer_embed_for_channel(ctx.channel, ctx.guild)
        if not embed:
            await ctx.reply("No question found for this channel yet.", mention_author=False)
            return
        await ctx.reply(embed=embed, mention_author=False)


class AnswerButtons(discord.ui.View):
    def __init__(self, cog: "QuestionCog", question_id: int, channel_id: Optional[int]) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.question_id = question_id
        self.channel_id = channel_id
        self.message_id: Optional[int] = None

        if channel_id is None:
            return

        for letter in ("A", "B", "C", "D"):
            label_text = OPTION_LABELS.get(letter, f"Option {letter}")
            button = discord.ui.Button(
                label=label_text,
                style=discord.ButtonStyle.primary,
                custom_id=f"quiz-answer:{question_id}:{letter}",
            )
            button.callback = self._make_callback(letter)
            self.add_item(button)

        # Add a 5th button to generate a new question
        new_question_button = discord.ui.Button(
            label="New Question",
            style=discord.ButtonStyle.success,
            emoji="🔄",
            custom_id=f"quiz-new:{question_id}",
        )
        new_question_button.callback = self._new_question_callback
        self.add_item(new_question_button)

    def _make_callback(self, letter: str):
        async def callback(interaction: discord.Interaction) -> None:
            await self._handle_answer(interaction, letter)

        return callback

    async def _new_question_callback(self, interaction: discord.Interaction) -> None:
        """Handle the 'New Question' button click."""
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "This action is only available inside a server.",
                ephemeral=True,
            )
            return

        # Disable all buttons on this question
        self.disable_all_items()
        await interaction.response.defer()

        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException:
            pass

        # Generate and publish a new question
        await self.cog.publish_question(interaction.channel)

    def disable_all_items(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

    def _pop_if_current(self) -> None:
        if self.channel_id is None:
            return
        current = self.cog.active_views.get(self.channel_id)
        if current is self:
            self.cog.active_views.pop(self.channel_id, None)

    async def _handle_answer(self, interaction: discord.Interaction, letter: str) -> None:
        if self.channel_id is None or not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "This question is no longer available. Fetch a new one with `/question`.",
                ephemeral=True,
            )
            return

        result = self.cog._submit_answer(
            user=interaction.user,
            choice=letter,
            channel_id=self.channel_id,
            question_id=self.question_id,
        )

        status = result.status
        if status == "no_question":
            await interaction.response.send_message(
                "No active question found here. Start one with `/question`.",
                ephemeral=True,
            )
            return

        if status == "already_solved":
            self.disable_all_items()
            solver_embed = self.cog._build_already_solved_embed(interaction.guild, result.solver_id)
            await interaction.response.send_message(embed=solver_embed, ephemeral=True)
            try:
                await interaction.message.edit(view=self)
            except discord.HTTPException:
                pass
            self._pop_if_current()
            return

        if status == "already_answered":
            await interaction.response.send_message(
                "You've already taken your shot at this one. Wait for the next question!",
                ephemeral=True,
            )
            return

        if status == "incorrect":
            # Build an embed showing their wrong answer and the correct one
            question = result.question
            if not question:
                await interaction.response.send_message(
                    "Something unexpected happened. Try again in a moment.",
                    ephemeral=True,
                )
                return

            options = self.cog._extract_options(question)
            correct_letter = (question.correct_answer or "A").upper()
            correct_text = options.get(correct_letter, "No option text recorded.")
            # Replace literal \n with actual newlines
            correct_text = correct_text.replace("\\n", "\n")
            correct_label = OPTION_LABELS.get(correct_letter, f"Option {correct_letter}")

            chosen_label = OPTION_LABELS.get(letter, f"Option {letter}")
            chosen_text = options.get(letter, "")
            # Replace literal \n with actual newlines
            chosen_text = chosen_text.replace("\\n", "\n")

            # Truncate chosen text if it would exceed description limit (4096 chars)
            description_prefix = f"You chose **{chosen_label}**: "
            max_chosen_length = 4000 - len(description_prefix)
            if len(chosen_text) > max_chosen_length:
                chosen_text = chosen_text[:max_chosen_length - 3] + "..."

            incorrect_embed = discord.Embed(
                title="Not Quite Right",
                description=f"You chose **{chosen_label}**: {chosen_text}",
                color=discord.Color.from_rgb(237, 66, 69),
            )

            # Split correct answer if it's too long for a field
            correct_answer_value = f"**{correct_label}**: {correct_text}"
            if len(correct_answer_value) <= 1024:
                incorrect_embed.add_field(
                    name="Correct Answer",
                    value=correct_answer_value,
                    inline=False,
                )
            else:
                # Split into chunks
                chunks = self.cog._split_text_into_chunks(correct_text, 1000)  # Leave room for label
                for idx, chunk in enumerate(chunks):
                    field_name = "Correct Answer" if idx == 0 else "Correct Answer (continued)"
                    field_value = f"**{correct_label}**: {chunk}" if idx == 0 else chunk
                    incorrect_embed.add_field(name=field_name, value=field_value, inline=False)

            if result.difficulty:
                incorrect_embed.add_field(name="Difficulty", value=result.difficulty, inline=True)
            if result.model_name:
                incorrect_embed.add_field(name="Generated By", value=f"model {result.model_name}", inline=True)

            if question.explanation:
                # Replace literal \n with actual newlines
                explanation = question.explanation.replace("\\n", "\n")
                # Split long explanations across multiple fields (max 1024 chars per field)
                if len(explanation) <= 1024:
                    incorrect_embed.add_field(name="Explanation", value=explanation, inline=False)
                else:
                    # Split into chunks at word boundaries
                    chunks = self._split_text_into_chunks(explanation, 1024)
                    for idx, chunk in enumerate(chunks):
                        field_name = "Explanation" if idx == 0 else "Explanation (continued)"
                        incorrect_embed.add_field(name=field_name, value=chunk, inline=False)

            incorrect_embed.set_footer(text="Keep practicing! Use /question to try another one.")

            await interaction.response.send_message(embed=incorrect_embed, ephemeral=True)
            return

        if status == "correct":
            # Send only the public announcement with the Next Question button
            public_embed = self.cog._build_public_correct_embed(interaction.user, result)
            next_question_view = NextQuestionButton(self.cog, interaction.user)

            await interaction.response.defer()

            if public_embed:
                await interaction.channel.send(embed=public_embed, view=next_question_view)

            self.disable_all_items()
            try:
                await interaction.message.edit(view=self)
            except discord.HTTPException:
                pass
            self._pop_if_current()
            return

        await interaction.response.send_message("Something unexpected happened. Try again in a moment.", ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return

        channel_id = getattr(message.channel, "id", None)
        if channel_id is None:
            return

        canonical_choice = normalise_answer(message.content)
        if canonical_choice not in ("A", "B", "C", "D"):
            return

        result = self._submit_answer(
            user=message.author,
            choice=canonical_choice,
            channel_id=channel_id,
        )

        status = result.status
        if status == "no_question":
            return
        if status == "already_solved":
            await self._safe_react(message, "⛔")
            await message.channel.send(embed=self._build_already_solved_embed(message.guild, result.solver_id))
            await self._disable_active_view(channel_id)
            return
        if status == "already_answered":
            await self._safe_react(message, "⛔")
            await message.reply("You've already answered this question.", mention_author=False, delete_after=6)
            return
        if status == "correct":
            await self._safe_react(message, "✅")
            await self._disable_active_view(channel_id)
            public_embed = self._build_public_correct_embed(message.author, result)
            next_question_view = NextQuestionButton(self, message.author)
            if public_embed:
                await message.channel.send(embed=public_embed, view=next_question_view)
            return
        if status == "incorrect":
            await self._safe_react(message, "❌")
            # Show the correct answer to help them learn
            question = result.question
            if question:
                options = self._extract_options(question)
                correct_letter = (question.correct_answer or "A").upper()
                correct_text = options.get(correct_letter, "No option text recorded.")
                correct_label = OPTION_LABELS.get(correct_letter, f"Option {correct_letter}")
                chosen_label = OPTION_LABELS.get(result.choice, f"Option {result.choice}")

                await message.reply(
                    f"Not quite! You chose **{chosen_label}**, but the correct answer was **{correct_label}**: {correct_text}",
                    mention_author=False,
                    delete_after=10,
                )
            else:
                difficulty_label = result.difficulty or "Unknown"
                model_label = result.model_name or "Unknown"
                await message.reply(
                    f"Good luck next time! (Difficulty: {difficulty_label}, Model: {model_label})",
                    mention_author=False,
                    delete_after=6,
                )

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

        meta = self._get_question_metadata(question)
        difficulty = meta.get("difficulty", "Unknown")
        model_name = meta.get("model", "Unknown")
        payload = QuestionPayload(
            topic=question.topic,
            question=question.prompt,
            options=question.options,
            answer=question.correct_answer,
            explanation=question.explanation,
            difficulty=difficulty,
            model_name=model_name,
        )
        embed = self._build_question_embed(payload, difficulty, model_name)
        await ctx.reply("Here is the current question:", embed=embed)

    async def _safe_react(self, message: discord.Message, emoji: str) -> None:
        try:
            await message.add_reaction(emoji)
        except discord.HTTPException:
            LOGGER.debug("Failed to add reaction %s to message %s", emoji, message.id)


class NextQuestionButton(discord.ui.View):
    """A button that allows users to quickly generate the next question."""

    def __init__(self, cog: "QuestionCog", requester: discord.Member) -> None:
        super().__init__(timeout=300)  # 5 minutes timeout
        self.cog = cog
        self.requester = requester

    @discord.ui.button(label="Next Question", style=discord.ButtonStyle.success, emoji="▶️")
    async def next_question(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "This action is only available inside a server.",
                ephemeral=True,
            )
            return

        # Check cooldown
        channel_id = interaction.channel.id
        current_time = time.time()
        last_time = self.cog.last_question_time.get(channel_id, 0)
        time_elapsed = current_time - last_time

        if time_elapsed < QUESTION_COOLDOWN_SECONDS:
            remaining = QUESTION_COOLDOWN_SECONDS - time_elapsed
            await interaction.response.send_message(
                f"⏱️ Slow down! Please wait {remaining:.1f} more seconds before generating the next question.",
                ephemeral=True,
            )
            return

        # Disable the button after it's clicked
        button.disabled = True
        await interaction.response.defer()

        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException:
            pass

        # Generate and publish a new question
        await self.cog.publish_question(interaction.channel)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(QuestionCog(bot))
