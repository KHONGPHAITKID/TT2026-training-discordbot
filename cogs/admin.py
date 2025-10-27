import logging
from typing import Optional

import discord
from discord.ext import commands

from services import db

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

    @commands.hybrid_command(name="question", with_app_command=True, description="Force post a new question now.")
    @is_admin()
    async def question(self, ctx: commands.Context, *, topic: Optional[str] = None) -> None:
        question_cog = self.bot.get_cog("QuestionCog")
        if not question_cog:
            await ctx.reply("Question system is not ready. Try again later.")
            return

        interaction = getattr(ctx, "interaction", None)
        if interaction and not interaction.response.is_done():
            await interaction.response.defer(thinking=True)

        await question_cog.publish_question(ctx.channel, topic)


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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
