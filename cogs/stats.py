import logging
from typing import Optional

import discord
from discord.ext import commands

from services import db
from services import charting

LOGGER = logging.getLogger(__name__)


class StatsCog(commands.Cog):
    """Provides leaderboard and performance statistics commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(name="leaderboard", with_app_command=True, description="Show the top quiz performers.")
    async def leaderboard(self, ctx: commands.Context, top: Optional[int] = 10) -> None:
        top = max(3, min(top or 10, 25))
        data = db.get_leaderboard(limit=top)

        if not data:
            await ctx.reply("No leaderboard data yet. Answer some questions first!")
            return

        embed = discord.Embed(title="🏆 Quiz Leaderboard", color=discord.Color.gold())
        lines = []
        for index, row in enumerate(data, start=1):
            lines.append(f"{index}. **{row['name']}** · {row['score']} pts · ✅ {row['correct']} · ❌ {row['wrong']}")
        embed.description = "\n".join(lines)

        chart_path = charting.render_leaderboard_chart(data)
        if chart_path:
            file = discord.File(chart_path, filename="leaderboard.png")
            embed.set_image(url="attachment://leaderboard.png")
            await ctx.reply(embed=embed, file=file)
        else:
            await ctx.reply(embed=embed)

    @commands.hybrid_command(name="stats", with_app_command=True, description="Show detailed stats for a user.")
    async def stats(self, ctx: commands.Context, member: Optional[discord.Member] = None) -> None:
        target = member or ctx.author
        profile = db.get_user_stats(target.id)
        if not profile:
            await ctx.reply(f"No quiz history found for {target.display_name}.")
            return

        embed = discord.Embed(
            title=f"📊 Quiz Stats · {target.display_name}",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Score", value=str(profile["score"]), inline=True)
        embed.add_field(name="Correct", value=str(profile["correct"]), inline=True)
        embed.add_field(name="Wrong", value=str(profile["wrong"]), inline=True)
        if profile["last_answer_time"]:
            embed.add_field(name="Last Answer", value=profile["last_answer_time"], inline=False)

        history_chart = charting.render_user_history_chart(target.id, target.display_name)
        if history_chart:
            file = discord.File(history_chart, filename="history.png")
            embed.set_image(url="attachment://history.png")
            await ctx.reply(embed=embed, file=file)
        else:
            await ctx.reply(embed=embed)

    @commands.hybrid_command(name="recent_questions", with_app_command=True, description="Show recent quiz topics.")
    async def recent_questions(self, ctx: commands.Context) -> None:
        questions = db.fetch_recent_questions(limit=5)
        if not questions:
            await ctx.reply("No questions have been posted yet.")
            return

        embed = discord.Embed(title="🗂️ Recent Quiz Questions", color=discord.Color.green())
        description_lines = []
        for item in questions:
            created_at = item["created_at"]
            description_lines.append(f"• **{item['topic']}** — {item['prompt'][:120]}… ({created_at})")
        embed.description = "\n".join(description_lines)
        await ctx.reply(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StatsCog(bot))
