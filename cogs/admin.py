import logging
from typing import Optional

import discord
from discord.ext import commands

from services import db
from services.llm_client import LLMClient
from pathlib import Path
import json

LOGGER = logging.getLogger(__name__)


def is_admin():
    async def predicate(ctx: commands.Context) -> bool:
        if not ctx.guild:
            return False
        config = db.get_guild_config(ctx.guild.id)
        if config.admin_role_id:
            role = ctx.guild.get_role(config.admin_role_id)
            if role and role in ctx.author.roles:
                return True
        # fallback to manage_guild permission
        permissions = ctx.author.guild_permissions
        return permissions.manage_guild or permissions.administrator

    return commands.check(predicate)


class AdminCog(commands.Cog):
    """Administrative utilities for managing quiz delivery."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(name="set_daily_channel", with_app_command=True, description="Select the channel for daily quizzes.")
    @is_admin()
    async def set_daily_channel(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None) -> None:
        if not ctx.guild:
            await ctx.reply("This command can only be used in a server.")
            return

        target_channel = channel or ctx.channel
        db.update_guild_config(ctx.guild.id, daily_channel_id=target_channel.id)
        await ctx.reply(f"Daily questions will now post in {target_channel.mention}.")

    @commands.hybrid_command(name="set_admin_role", with_app_command=True, description="Assign a role with bot admin privileges.")
    @commands.has_permissions(manage_guild=True)
    async def set_admin_role(self, ctx: commands.Context, role: discord.Role) -> None:
        if not ctx.guild:
            await ctx.reply("Use this command within a server.")
            return

        db.update_guild_config(ctx.guild.id, admin_role_id=role.id)
        await ctx.reply(f"{role.mention} can now manage the quiz bot.")


    @commands.hybrid_command(name="reset_scores", with_app_command=True, description="Reset all quiz scores.")
    @is_admin()
    async def reset_scores(self, ctx: commands.Context) -> None:
        if not ctx.guild:
            await ctx.reply("This command can only be used within a server.")
            return

        # Danger of global reset - confirm by requiring explicit text? Basic implementation only for demonstration.
        from services.db import get_session, User

        with get_session() as session:
            users = session.query(User).all()
            for user in users:
                user.score = 0
                user.correct = 0
                user.wrong = 0
        await ctx.reply("All scores have been reset.")

    @commands.hybrid_command(name="set_model", with_app_command=True, description="Set default LLM model for this server.")
    @is_admin()
    async def set_model(self, ctx: commands.Context, *, model: str) -> None:
        if not ctx.guild:
            await ctx.reply("This command can only be used within a server.")
            return

        model_name = model.strip()
        if not model_name:
            await ctx.reply("Please provide a model name or 'random'.")
            return

        if model_name.lower() == "random":
            db.update_guild_config(ctx.guild.id, default_model=None)
            await ctx.reply("Default model set to random.")
            return

        client = LLMClient()
        supported = client.available_models
        if model_name not in supported:
            supported_list = ", ".join(supported) if supported else "No models configured."
            await ctx.reply(f"Model not supported. Available models: {supported_list}")
            return

        db.update_guild_config(ctx.guild.id, default_model=model_name)
        await ctx.reply(f"Default model set to {model_name}.")

    @commands.hybrid_command(name="remove_model", with_app_command=True, description="Clear the default LLM model.")
    @is_admin()
    async def remove_model(self, ctx: commands.Context) -> None:
        if not ctx.guild:
            await ctx.reply("This command can only be used within a server.")
            return
        db.update_guild_config(ctx.guild.id, default_model=None)
        await ctx.reply("Default model cleared. The bot will use random models.")

    @commands.hybrid_command(name="model", with_app_command=True, description="Show current model and supported options.")
    async def model(self, ctx: commands.Context) -> None:
        if not ctx.guild:
            await ctx.reply("This command can only be used within a server.")
            return

        config = db.get_guild_config(ctx.guild.id)
        current = config.default_model or "random"
        client = LLMClient()
        supported = client.available_models
        supported_list = ", ".join(["random"] + supported) if supported else "random"

        message = (
            f"Current model: {current}\n"
            f"Supported models: {supported_list}\n"
            "Use `/set_model <model>` to pin a model, `/set_model random` or `/remove_model` to reset."
        )
        await ctx.reply(message)

    @commands.hybrid_command(name="add_topic", with_app_command=True, description="Add a quiz topic.")
    @is_admin()
    async def add_topic(self, ctx: commands.Context, *, topic: str) -> None:
        if not ctx.guild:
            await ctx.reply("This command can only be used within a server.")
            return

        topic_name = topic.strip()
        if not topic_name:
            await ctx.reply("Please provide a topic name.")
            return

        topics_path = Path(__file__).parent.parent / "topics.json"
        if not topics_path.exists():
            await ctx.reply("topics.json not found.")
            return

        try:
            with open(topics_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            await ctx.reply("Unable to read topics.json.")
            return

        categories = data.setdefault("categories", {})
        for cat in categories.values():
            existing = [t.lower() for t in cat.get("topics", [])]
            if topic_name.lower() in existing:
                await ctx.reply("Topic already exists.")
                return

        custom = categories.setdefault(
            "custom",
            {
                "display_name": "Custom Topics",
                "enabled": True,
                "topics": [],
            },
        )
        custom_topics = custom.setdefault("topics", [])
        custom_topics.append(topic_name)

        with open(topics_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        await ctx.reply(f"Added topic: {topic_name}")

    @commands.hybrid_command(name="remove_topic", with_app_command=True, description="Remove a quiz topic.")
    @is_admin()
    async def remove_topic(self, ctx: commands.Context, *, topic: str) -> None:
        if not ctx.guild:
            await ctx.reply("This command can only be used within a server.")
            return

        topic_name = topic.strip()
        if not topic_name:
            await ctx.reply("Please provide a topic name.")
            return

        topics_path = Path(__file__).parent.parent / "topics.json"
        if not topics_path.exists():
            await ctx.reply("topics.json not found.")
            return

        try:
            with open(topics_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            await ctx.reply("Unable to read topics.json.")
            return

        categories = data.get("categories", {})
        removed = False
        for cat in categories.values():
            topics = cat.get("topics", [])
            new_topics = [t for t in topics if t.strip().lower() != topic_name.lower()]
            if len(new_topics) != len(topics):
                cat["topics"] = new_topics
                removed = True

        if not removed:
            await ctx.reply("Topic not found.")
            return

        with open(topics_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        await ctx.reply(f"Removed topic: {topic_name}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
