from __future__ import annotations

import json
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks


class Reminder(commands.Cog):
    """Cog managing persistent reminders."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.file = Path(__file__).resolve().parent.parent / "reminders.json"
        self.reminders: dict[str, dict] = {}
        self.load_reminders()

    # slash command group
    reminder = app_commands.Group(name="reminder", description="Reminder utilities")

    def save_reminders(self) -> None:
        data = {
            name: {
                "interval": info["interval"],
                "channel_id": info["channel_id"],
                "message": info["message"],
            }
            for name, info in self.reminders.items()
        }
        with self.file.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def load_reminders(self) -> None:
        if not self.file.exists():
            return
        with self.file.open("r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                data = {}
        for name, info in data.items():
            self.create_reminder(name, info["interval"], info["channel_id"], info["message"])

    def cog_unload(self) -> None:
        for info in self.reminders.values():
            info["task"].cancel()

    def create_reminder(self, name: str, interval: int, channel_id: int, message: str) -> None:
        async def send_reminder():
            await self.bot.wait_until_ready()
            channel = self.bot.get_channel(channel_id)
            if channel:
                await channel.send(message)

        loop = tasks.loop(seconds=interval)(send_reminder)
        loop.start()
        self.reminders[name] = {
            "interval": interval,
            "channel_id": channel_id,
            "message": message,
            "task": loop,
        }
        self.save_reminders()

    @reminder.command(name="add", description="Add a repeating reminder.")
    async def add(
        self,
        interaction: discord.Interaction,
        name: str,
        interval: int,
        channel: discord.TextChannel,
        message: str,
    ):
        if name in self.reminders:
            await interaction.response.send_message(f"Reminder `{name}` exists.", ephemeral=True)
            return
        self.create_reminder(name, interval, channel.id, message)
        await interaction.response.send_message(f"Reminder `{name}` added.", ephemeral=True)

    @reminder.command(name="remove", description="Remove a reminder.")
    async def remove(self, interaction: discord.Interaction, name: str):
        info = self.reminders.get(name)
        if not info:
            await interaction.response.send_message(f"No reminder `{name}`.", ephemeral=True)
            return
        info["task"].cancel()
        del self.reminders[name]
        self.save_reminders()
        await interaction.response.send_message(f"Reminder `{name}` removed.", ephemeral=True)

    @reminder.command(name="list", description="List active reminders.")
    async def list(self, interaction: discord.Interaction):
        if not self.reminders:
            await interaction.response.send_message("No reminders set.", ephemeral=True)
            return
        lines = []
        for name, info in self.reminders.items():
            channel = self.bot.get_channel(info["channel_id"])
            ch = channel.mention if channel else f"#{info['channel_id']}"
            lines.append(f"`{name}` every {info['interval']}s in {ch}: {info['message']}")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Reminder(bot))
