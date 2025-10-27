from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, Union

import discord
from discord.ext import commands

from services import charting, db

ContextLike = Union[commands.Context, discord.Interaction]


@dataclass
class PersonalStatArtifacts:
    accuracy_chart: Optional[Path]
    topic_chart: Optional[Path]
    history_chart: Optional[Path]


@dataclass
class GlobalStatArtifacts:
    leaderboard_chart: Optional[Path]
    accuracy_chart: Optional[Path]
    topic_chart: Optional[Path]


class StatsCog(commands.Cog):
    """Provides leaderboard, statistics, and quick action utilities."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # Command entrypoints
    # ------------------------------------------------------------------
    @commands.hybrid_command(name="stats", with_app_command=True, description="Show detailed performance stats.")
    async def stats(self, ctx: commands.Context, member: Optional[discord.Member] = None) -> None:
        subject = member or ctx.author
        await self._send_personal_stats(ctx, subject, ephemeral=False)

    @commands.hybrid_command(
        name="leaderboard",
        with_app_command=True,
        description="Reveal the global leaderboard with advanced stats.",
    )
    async def leaderboard(self, ctx: commands.Context, top: Optional[int] = 10) -> None:
        await self._send_leaderboard(ctx, limit=top or 10, ephemeral=False)

    @commands.hybrid_command(name="recent_questions", with_app_command=True, description="Show recent quiz topics.")
    async def recent_questions(self, ctx: commands.Context) -> None:
        questions = db.fetch_recent_questions(limit=5)
        if not questions:
            await ctx.reply("No questions have been posted yet.")
            return

        embed = discord.Embed(
            title="Recent Quiz Questions",
            description="Here are the 5 most recent quiz questions posted:",
            color=discord.Color.from_rgb(76, 110, 245),
        )

        for idx, item in enumerate(questions, start=1):
            # Parse metadata from prompt
            meta, clean_prompt = self._parse_prompt_metadata(item["prompt"])
            difficulty = meta.get("difficulty", "Unknown")
            model = meta.get("model", "Unknown")

            # Format timestamp
            created_at = item["created_at"]
            if isinstance(created_at, str):
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    time_str = dt.strftime("%b %d, %Y at %I:%M %p UTC")
                except Exception:
                    time_str = created_at
            else:
                time_str = created_at.strftime("%b %d, %Y at %I:%M %p UTC") if hasattr(created_at, 'strftime') else str(created_at)

            # Create snippet of the question
            snippet = clean_prompt[:150].rstrip()
            if len(clean_prompt) > 150:
                snippet += "..."

            # Build field value with nice formatting
            field_value = (
                f"**Question:** {snippet}\n"
                f"**Difficulty:** {difficulty} | **Model:** {model}\n"
                f"**Posted:** {time_str}"
            )

            embed.add_field(
                name=f"{idx}. {item['topic']}",
                value=field_value,
                inline=False,
            )

        embed.set_footer(text="Use /question to generate a new quiz or /ans to see answers!")
        await ctx.reply(embed=embed, mention_author=False)

    @staticmethod
    def _parse_prompt_metadata(prompt: str) -> Tuple[Dict[str, str], str]:
        """Parse metadata tags from prompt string."""
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
            remainder = remainder[end + 1:].lstrip()
        return meta, remainder

    @commands.hybrid_command(
        name="tt",
        with_app_command=True,
        description="Display quick quiz actions (new question, leaderboard, stats).",
    )
    async def quick_actions(self, ctx: commands.Context) -> None:
        interaction = getattr(ctx, "interaction", None)

        if not ctx.guild:
            message = "Use this command inside a server."
            if interaction and not interaction.response.is_done():
                await interaction.response.send_message(message, ephemeral=True)
            else:
                await ctx.reply(message, mention_author=False)
            return

        view = QuickActionsView(self, ctx.author)
        embed = discord.Embed(
            title="Quiz Shortcuts",
            description="Use the buttons below for quick actions.",
            color=discord.Color.blurple(),
        )

        if interaction and not interaction.response.is_done():
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        else:
            await ctx.reply(embed=embed, view=view, mention_author=False)

    def _build_help_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="Team Trivia Help",
            description="Key commands to get the most out of the daily CS quiz bot.",
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="Daily Questions",
            value="• `/question` or `/q` – post a new quiz question\n"
            "• `/ans` – reveal the latest answer and explanation\n"
            "• `/tt` – open quick-action buttons",
            inline=False,
        )
        embed.add_field(
            name="Stats & Leaderboards",
            value="• `/leaderboard` – global scores, accuracy, specialists\n"
            "• `/stats [member]` – personal performance report\n"
            "• `/recent_questions` – last five topics",
            inline=False,
        )
        embed.add_field(
            name="Difficulty & Models",
            value=(
                "Each question rotates through easy/medium/hard difficulty and is generated by a random Groq model. "
                "Both details are shown on the question embeds and answer recaps."
            ),
            inline=False,
        )
        embed.add_field(
            name="Need More?",
            value="Admins can use `/set_daily_channel`, `/set_admin_role`, and `/reset_scores` to manage the game.",
            inline=False,
        )
        return embed

    @commands.hybrid_command(
        name="tthelp",
        with_app_command=True,
        description="Show an overview of quiz commands and features.",
    )
    async def tthelp(self, ctx: commands.Context) -> None:
        await ctx.reply(embed=self._build_help_embed(), mention_author=False)

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_guild(target: ContextLike) -> Optional[discord.Guild]:
        if isinstance(target, commands.Context):
            return target.guild
        return target.guild

    @staticmethod
    def _extract_member(target: ContextLike) -> Optional[discord.Member]:
        author = target.author if isinstance(target, commands.Context) else target.user
        return author if isinstance(author, discord.Member) else None

    async def _reply(
        self,
        target: ContextLike,
        *,
        content: Optional[str] = None,
        embed: Optional[discord.Embed] = None,
        files: Optional[List[discord.File]] = None,
        ephemeral: bool = False,
    ) -> None:
        if isinstance(target, discord.Interaction):
            payload: Dict[str, object] = {}
            if content:
                payload["content"] = content
            if embed:
                payload["embed"] = embed
            if files:
                payload["files"] = files

            if not target.response.is_done():
                await target.response.send_message(ephemeral=ephemeral, **payload)
            else:
                await target.followup.send(ephemeral=ephemeral, **payload)
        else:
            kwargs: Dict[str, object] = {"mention_author": False}
            if content:
                kwargs["content"] = content
            if embed:
                kwargs["embed"] = embed
            if files:
                kwargs["files"] = files
            await target.reply(**kwargs)

    # ------------------------------------------------------------------
    # Personal stats handling
    # ------------------------------------------------------------------
    async def _send_personal_stats(
        self,
        target: ContextLike,
        member: Optional[discord.Member],
        *,
        ephemeral: bool,
    ) -> None:
        guild = self._extract_guild(target)
        if not guild or not member:
            await self._reply(target, content="Use this command inside a server.", ephemeral=ephemeral)
            return

        profile = db.get_user_stats(member.id)
        if not profile:
            await self._reply(
                target,
                content=f"No quiz history found for {member.display_name}.",
                ephemeral=ephemeral,
            )
            return

        details = db.get_user_answer_stats(member.id)
        embed = self._build_personal_embed(member, profile, details)
        charts = self._render_personal_charts(member, details)

        files: List[discord.File] = []
        if charts.accuracy_chart and charts.accuracy_chart.exists():
            files.append(discord.File(charts.accuracy_chart, filename="accuracy.png"))
            embed.set_thumbnail(url="attachment://accuracy.png")
        if charts.topic_chart and charts.topic_chart.exists():
            files.append(discord.File(charts.topic_chart, filename="topics.png"))
        if charts.history_chart and charts.history_chart.exists():
            files.append(discord.File(charts.history_chart, filename="history.png"))

        await self._reply(target, embed=embed, files=files or None, ephemeral=ephemeral)

    def _build_personal_embed(
        self, member: discord.Member, profile: Dict[str, object], details: Dict[str, object]
    ) -> discord.Embed:
        total_answers = details["total_answers"]
        correct_answers = details["correct_answers"]
        incorrect_answers = details["incorrect_answers"]
        accuracy = details["accuracy"] * 100

        embed = discord.Embed(
            title=f"Performance Report - {member.display_name}",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Score", value=str(profile["score"]), inline=True)
        embed.add_field(name="Correct", value=str(correct_answers), inline=True)
        embed.add_field(name="Wrong", value=str(incorrect_answers), inline=True)
        embed.add_field(name="Accuracy", value=f"{accuracy:.1f}%", inline=True)
        embed.add_field(name="Answered", value=str(total_answers), inline=True)
        if profile["last_answer_time"]:
            embed.add_field(name="Last Answer", value=str(profile["last_answer_time"]), inline=True)

        if details["topics"]:
            best_topic = max(details["topics"], key=lambda item: item["accuracy"])
            embed.add_field(
                name="Top Topic",
                value=f"{best_topic['topic']} - {best_topic['accuracy'] * 100:.1f}% accuracy",
                inline=False,
            )

        embed.set_footer(text="Keep streaking - first correct answer wins the round!")
        return embed

    def _render_personal_charts(
        self, member: discord.Member, details: Dict[str, object]
    ) -> PersonalStatArtifacts:
        nickname = str(member.id)
        accuracy_chart = charting.render_user_accuracy_chart(
            username=nickname,
            correct=details["correct_answers"],
            incorrect=details["incorrect_answers"],
        )
        topic_chart = charting.render_user_topic_breakdown(
            username=nickname,
            topics=details["topics"],
        )
        history_chart = charting.render_user_history_chart(member.id, member.display_name)
        return PersonalStatArtifacts(accuracy_chart, topic_chart, history_chart)

    # ------------------------------------------------------------------
    # Leaderboard handling
    # ------------------------------------------------------------------
    async def _send_leaderboard(
        self,
        target: ContextLike,
        *,
        limit: int,
        ephemeral: bool,
    ) -> None:
        guild = self._extract_guild(target)
        if not guild:
            await self._reply(target, content="Use this command inside a server.", ephemeral=ephemeral)
            return

        limit = max(3, min(limit, 25))
        leaderboard = db.get_leaderboard(limit=limit)
        if not leaderboard:
            await self._reply(target, content="No leaderboard data yet. Answer some questions first!", ephemeral=ephemeral)
            return

        accuracy_raw = db.get_high_accuracy_players(limit=5, min_answers=5)
        topic_leaders = db.get_top_topic_performers(limit_per_topic=1)
        specialists_raw = [
            {"topic": topic, **entry} for topic, entries in topic_leaders.items() for entry in entries
        ]

        user_ids = [row["id"] for row in leaderboard]
        user_ids.extend(entry["user_id"] for entry in accuracy_raw)
        user_ids.extend(entry["user_id"] for entry in specialists_raw)
        labels = await self._resolve_user_labels(guild, user_ids)

        for row in leaderboard:
            row["name"] = labels.get(row["id"], row.get("name", f"User {row['id']}"))

        accuracy_leaders = [
            {
                **entry,
                "user_label": labels.get(entry["user_id"], f"User {entry['user_id']}"),
            }
            for entry in accuracy_raw
        ]
        specialists = [
            {
                "topic": entry["topic"],
                "correct": entry["correct"],
                "user_label": labels.get(entry["user_id"], f"User {entry['user_id']}"),
            }
            for entry in specialists_raw
        ]

        embed = self._build_leaderboard_embed(target, leaderboard, accuracy_leaders, specialists)
        charts = self._render_global_charts(leaderboard, accuracy_leaders, specialists)

        files: List[discord.File] = []
        if charts.leaderboard_chart and charts.leaderboard_chart.exists():
            files.append(discord.File(charts.leaderboard_chart, filename="leaderboard.png"))
            embed.set_thumbnail(url="attachment://leaderboard.png")
        if charts.accuracy_chart and charts.accuracy_chart.exists():
            files.append(discord.File(charts.accuracy_chart, filename="accuracy.png"))
        if charts.topic_chart and charts.topic_chart.exists():
            files.append(discord.File(charts.topic_chart, filename="specialists.png"))

        await self._reply(target, embed=embed, files=files or None, ephemeral=ephemeral)

    def _build_leaderboard_embed(
        self,
        target: ContextLike,
        leaderboard: List[dict],
        accuracy_leaders: List[dict],
        specialists: List[dict],
    ) -> discord.Embed:
        requester = self._extract_member(target)
        embed = discord.Embed(title="Global Standings", color=discord.Color.gold())
        leaderboard_lines = [
            f"{index}. **{row['name']}** - {row['score']} pts "
            f"(correct {row['correct']} / wrong {row['wrong']})"
            for index, row in enumerate(leaderboard, start=1)
        ]
        embed.add_field(name="Top Players", value="\n".join(leaderboard_lines), inline=False)

        if accuracy_leaders:
            accuracy_lines = [
                f"{idx + 1}. **{entry['user_label']}** - {entry['accuracy'] * 100:.1f}% "
                f"({entry['correct']}/{entry['attempts']} correct)"
                for idx, entry in enumerate(accuracy_leaders)
            ]
            embed.add_field(name="Accuracy Leaders", value="\n".join(accuracy_lines), inline=False)

        if specialists:
            specialist_lines = [
                f"{entry['topic']}: **{entry['user_label']}** ({entry['correct']} correct)"
                for entry in specialists
            ]
            embed.add_field(name="Topic Specialists", value="\n".join(specialist_lines), inline=False)

        if requester:
            embed.set_footer(text=f"Requested by {requester.display_name}")
        return embed

    def _render_global_charts(
        self,
        leaderboard: List[dict],
        accuracy_leaders: List[dict],
        specialists: List[dict],
    ) -> GlobalStatArtifacts:
        leaderboard_chart = charting.render_leaderboard_chart(leaderboard)
        accuracy_chart = charting.render_accuracy_leaders_chart(accuracy_leaders)
        topic_chart = charting.render_topic_leaders_chart(specialists)
        return GlobalStatArtifacts(leaderboard_chart, accuracy_chart, topic_chart)

    async def _resolve_user_labels(self, guild: discord.Guild, user_ids: Iterable[int]) -> Dict[int, str]:
        labels: Dict[int, str] = {}
        for user_id in set(user_ids):
            member = guild.get_member(user_id)
            if member:
                labels[user_id] = member.display_name
                continue
            try:
                user = await self.bot.fetch_user(user_id)
            except discord.HTTPException:
                labels[user_id] = f"User {user_id}"
            else:
                labels[user_id] = user.display_name or user.name or str(user_id)
        return labels


class QuickActionsView(discord.ui.View):
    def __init__(self, cog: StatsCog, requester: discord.Member, *, timeout: Optional[float] = 120) -> None:
        super().__init__(timeout=timeout)
        self.cog = cog
        self.requester = requester

    @discord.ui.button(label="/question", style=discord.ButtonStyle.primary)
    async def question(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This action is only available inside a server.", ephemeral=True)
            return

        question_cog = self.cog.bot.get_cog("QuestionCog")
        if not question_cog:
            await interaction.response.send_message("Question system is not ready. Try again later.", ephemeral=True)
            return

        await interaction.response.defer()
        await question_cog.publish_question(interaction.channel)

    @discord.ui.button(label="/leaderboard", style=discord.ButtonStyle.secondary)
    async def leaderboard(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog._send_leaderboard(interaction, limit=10, ephemeral=False)

    @discord.ui.button(label="/stats", style=discord.ButtonStyle.secondary)
    async def stats(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        await self.cog._send_personal_stats(interaction, member, ephemeral=False)

    @discord.ui.button(label="/ans", style=discord.ButtonStyle.secondary)
    async def answer(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This action is only available inside a server.", ephemeral=True)
            return

        question_cog = self.cog.bot.get_cog("QuestionCog")
        if not question_cog:
            await interaction.response.send_message("Question system is not ready. Try again later.", ephemeral=True)
            return

        embed = question_cog.get_answer_embed_for_channel(interaction.channel, interaction.guild)
        if not embed:
            await interaction.response.send_message("No question found for this channel yet.", ephemeral=True)
            return

        await interaction.response.send_message(embed=embed, ephemeral=False)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StatsCog(bot))

