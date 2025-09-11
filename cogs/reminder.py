from __future__ import annotations

import json
import time
from datetime import datetime
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
                "unit": info["unit"],
                "weekday": info["weekday"],
                "hour": info["hour"],
                "minute": info["minute"],
                "channel_id": info["channel_id"],
                "message": info["message"],
                "last": info["last"],
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
            self.create_reminder(
                name,
                info["interval"],
                info.get("unit", "seconds"),
                info["channel_id"],
                info["message"],
                info.get("weekday"),
                info.get("hour"),
                info.get("minute"),
                info.get("last"),
            )

    def cog_unload(self) -> None:
        for info in self.reminders.values():
            info["task"].cancel()

    def create_reminder(
        self,
        name: str,
        interval: int,
        unit: str,
        channel_id: int,
        message: str,
        weekday: int | None = None,
        hour: int | None = None,
        minute: int | None = None,
        last: float | None = None,
    ) -> None:
        seconds_per_unit = {"minutes": 60, "hours": 3600, "days": 86400}
        interval_seconds = interval * seconds_per_unit.get(unit, 1)

        async def send_reminder():
            await self.bot.wait_until_ready()
            now = time.time()
            info = self.reminders[name]
            if now - info["last"] < interval_seconds:
                return
            tm = time.gmtime(now)
            if weekday is not None and tm.tm_wday != weekday:
                return
            if hour is not None and tm.tm_hour != hour:
                return
            if minute is not None and tm.tm_min != minute:
                return
            channel = self.bot.get_channel(channel_id)
            if channel:
                await channel.send(message)
                info["last"] = now
                self.save_reminders()

        loop = tasks.loop(seconds=60)(send_reminder)
        loop.start()
        default_last = (
            0.0 if any(v is not None for v in (weekday, hour, minute)) else time.time()
        )
        self.reminders[name] = {
            "interval": interval,
            "unit": unit,
            "weekday": weekday,
            "hour": hour,
            "minute": minute,
            "channel_id": channel_id,
            "message": message,
            "task": loop,
            "last": last if last is not None else default_last,
        }
        self.save_reminders()

    @reminder.command(name="add", description="Add a repeating reminder.")
    @app_commands.describe(
        name="Name for the reminder",
        interval="Interval amount",
        unit="Interval unit",
        weekday="Optional day of week",
        channel="Channel to post in",
        message="Reminder message",
        time="Optional time of day HH:MM",
    )
    @app_commands.choices(
        unit=[
            app_commands.Choice(name="Minutes", value="minutes"),
            app_commands.Choice(name="Hours", value="hours"),
            app_commands.Choice(name="Days", value="days"),
        ],
        weekday=[
            app_commands.Choice(name="Monday", value=0),
            app_commands.Choice(name="Tuesday", value=1),
            app_commands.Choice(name="Wednesday", value=2),
            app_commands.Choice(name="Thursday", value=3),
            app_commands.Choice(name="Friday", value=4),
            app_commands.Choice(name="Saturday", value=5),
            app_commands.Choice(name="Sunday", value=6),
        ],
    )
    async def add(
        self,
        interaction: discord.Interaction,
        name: str,
        interval: int,
        unit: app_commands.Choice[str],
        channel: discord.TextChannel,
        message: str,
        weekday: app_commands.Choice[int] | None = None,
        time: str | None = None,
    ) -> None:
        if name in self.reminders:
            await interaction.response.send_message(f"Reminder `{name}` exists.", ephemeral=True)
            return
        hour = minute = None
        if time:
            try:
                parsed = datetime.strptime(time, "%H:%M")
                hour, minute = parsed.hour, parsed.minute
            except ValueError:
                await interaction.response.send_message("Invalid time format; use HH:MM.", ephemeral=True)
                return
        self.create_reminder(
            name,
            interval,
            unit.value,
            channel.id,
            message,
            weekday.value if weekday else None,
            hour,
            minute,
        )
        await interaction.response.send_message(f"Reminder `{name}` added.", ephemeral=True)

    @reminder.command(name="remove", description="Remove a reminder.")
    @app_commands.describe(name="Reminder to remove")
    async def remove(self, interaction: discord.Interaction, name: str) -> None:
        info = self.reminders.get(name)
        if not info:
            await interaction.response.send_message(f"No reminder `{name}`.", ephemeral=True)
            return
        info["task"].cancel()
        del self.reminders[name]
        self.save_reminders()
        await interaction.response.send_message(f"Reminder `{name}` removed.", ephemeral=True)

    @remove.autocomplete("name")
    async def remove_autocomplete(self, interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=n, value=n)
            for n in self.reminders
            if current.lower() in n.lower()
        ]

    @reminder.command(name="list", description="List active reminders.")
    async def list(self, interaction: discord.Interaction):
        if not self.reminders:
            await interaction.response.send_message("No reminders set.", ephemeral=True)
            return
        lines = []
        for name, info in self.reminders.items():
            channel = self.bot.get_channel(info["channel_id"])
            ch = channel.mention if channel else f"#{info['channel_id']}"
            interval = f"{info['interval']} {info['unit']}"
            if info["weekday"] is not None:
                days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
                interval += f" on {days[info['weekday']]}"
            if info["hour"] is not None and info["minute"] is not None:
                interval += f" at {info['hour']:02d}:{info['minute']:02d}"
            lines.append(f"`{name}` every {interval} in {ch}: {info['message']}")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Reminder(bot))
