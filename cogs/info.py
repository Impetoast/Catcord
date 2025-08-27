# cogs/info.py
from datetime import datetime, timezone
import platform

import discord
from discord import app_commands
from discord.ext import commands

class Info(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="about", description="Info über den Bot (Server, Latenz, Sync-Zeiten).")
    async def about(self, interaction: discord.Interaction):
        bot = self.bot
        now = datetime.now(timezone.utc)

        # Uptime
        if getattr(bot, "start_time", None):
            delta = now - bot.start_time
            days = delta.days
            hours, rem = divmod(delta.seconds, 3600)
            mins, _ = divmod(rem, 60)
            uptime = f"{days}d {hours}h {mins}m"
        else:
            uptime = "–"

        # Sync-Zeiten
        last_global = getattr(bot, "last_global_sync", None)
        last_global_txt = last_global.isoformat(timespec="seconds") if last_global else "–"

        lg = getattr(bot, "last_guild_syncs", {})
        guild_sync = lg.get(interaction.guild_id or -1)
        guild_sync_txt = guild_sync.isoformat(timespec="seconds") if guild_sync else "–"

        # Latenz
        ws_ms = round(bot.latency * 1000)

        embed = discord.Embed(
            title=f"Über {bot.user.name}",
            description="Status & Meta-Informationen",
        )
        embed.add_field(name="Server (Guilds)", value=str(len(bot.guilds)), inline=True)
        embed.add_field(name="WS-Latenz", value=f"{ws_ms} ms", inline=True)
        embed.add_field(name="Uptime", value=uptime, inline=True)
        embed.add_field(name="Letzter Guild-Sync", value=guild_sync_txt, inline=True)
        embed.add_field(name="Letzter Global-Sync", value=last_global_txt, inline=True)
        embed.add_field(name="discord.py", value=discord.__version__, inline=True)
        embed.add_field(name="Python", value=platform.python_version(), inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(Info(bot))
