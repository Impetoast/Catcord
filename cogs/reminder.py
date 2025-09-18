from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta

from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks


DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "reminder"
DATA_DIR.mkdir(parents=True, exist_ok=True)


class Reminder(commands.Cog):
    """Cog managing persistent reminders."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.reminders: dict[int, dict[str, dict]] = {}
        self.guild_settings: dict[int, bool] = {}
        self.load_reminders()

    # slash command group
    reminder = app_commands.Group(name="reminder", description="Reminder utilities")

    def save_reminders(self) -> None:
        existing = {p.stem: p for p in DATA_DIR.glob("*.json")}
        tracked_ids = set(self.reminders) | set(self.guild_settings)
        for gid, path in existing.items():
            if int(gid) not in tracked_ids:
                path.unlink()
        for guild_id in tracked_ids:
            path = DATA_DIR / f"{guild_id}.json"
            rems = self.reminders.get(guild_id, {})
            reminder_payload = {
                name: {
                    "interval": info["interval"],
                    "unit": info["unit"],
                    "headline": info.get("headline"),
                    "weekday": info["weekday"],
                    "hour": info["hour"],
                    "minute": info["minute"],
                    "channel_id": info["channel_id"],
                    "message": info["message"],
                    "last": info["last"],
                    "one_time": info["one_time"],
                }
                for name, info in rems.items()
            }
            payload = {
                "__settings": {"enabled": self.guild_settings.get(guild_id, True)}
            }
            payload.update(reminder_payload)
            with path.open("w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)

    def load_reminders(self) -> None:
        for file in DATA_DIR.glob("*.json"):
            try:
                guild_id = int(file.stem)
            except ValueError:
                continue
            with file.open("r", encoding="utf-8") as f:
                try:
                    data = json.load(f)
                except json.JSONDecodeError:
                    data = {}
            if not isinstance(data, dict):
                data = {}
            settings_info = data.get("__settings")
            if isinstance(settings_info, dict):
                self.guild_settings[guild_id] = bool(settings_info.get("enabled", True))
            else:
                self.guild_settings.setdefault(guild_id, True)
            for name, info in data.items():
                if name.startswith("__"):
                    continue
                self.create_reminder(
                    guild_id,
                    name,
                    info["interval"],
                    info.get("unit", "seconds"),
                    info["channel_id"],
                    info["message"],
                    info.get("headline"),
                    info.get("weekday"),
                    info.get("hour"),
                    info.get("minute"),
                    info.get("last"),
                    info.get("one_time", False),
                    save=False,
                )


    def cog_unload(self) -> None:
        for rems in self.reminders.values():
            for info in rems.values():
                info["task"].cancel()

    def create_reminder(
        self,
        guild_id: int,
        name: str,
        interval: int,
        unit: str,
        channel_id: int,
        message: str,
        headline: str | None = None,
        weekday: int | None = None,
        hour: int | None = None,
        minute: int | None = None,
        last: float | None = None,
        one_time: bool = False,
        save: bool = True,
    ) -> None:
        seconds_per_unit = {"minutes": 60, "hours": 3600, "days": 86400}
        interval_seconds = interval * seconds_per_unit.get(unit, 1)

        async def send_reminder():
            now = time.time()
            info = self.reminders[guild_id][name]
            if not self.guild_settings.get(guild_id, True):
                return
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
                render_text = self._render_message(info["message"])
                embed = None
                if info.get("headline"):
                    embed = discord.Embed(
                        title=info["headline"], description=render_text
                    )
                send_kwargs = {"embed": embed} if embed else {"content": render_text}
                await channel.send(**send_kwargs)

                # Mirror reminders via LangRelay if channel participates in a group
                lr_cog = self.bot.get_cog("LangRelay")
                if lr_cog and getattr(channel, "guild", None):
                    try:
                        guild = channel.guild
                        await lr_cog._ensure_cache(guild)
                        groups = lr_cog._groups(guild.id)
                        gopts = lr_cog._group_options(guild.id)

                        src_groups = [
                            gname
                            for gname, chans in groups.items()
                            if channel.name in chans and gopts.get(gname, True)
                        ]
                        sent_to = {channel.id}
                        base_text = render_text
                        headline_text = info.get("headline")
                        for gname in src_groups:
                            chans = groups.get(gname, {})
                            src_lang = chans.get(channel.name)
                            for tgt_name, tgt_lang in chans.items():
                                if tgt_name == channel.name:
                                    continue
                                tgt_channel = lr_cog._get_channel_by_name(guild.id, tgt_name)
                                if not tgt_channel or tgt_channel.id in sent_to:
                                    continue
                                out_text = base_text
                                if tgt_lang:
                                    try:
                                        out_text = await lr_cog._translate(
                                            base_text, tgt_lang, src_lang, guild.id
                                        )
                                    except Exception as e:  # pragma: no cover - translation optional
                                        print(
                                            f"⚠️ Reminder translation failed ({channel.name} → {tgt_name}): {e}"
                                        )
                                try:
                                    if headline_text:
                                        embed = discord.Embed(
                                            title=headline_text, description=out_text
                                        )
                                        await tgt_channel.send(embed=embed)
                                    else:
                                        await tgt_channel.send(out_text)
                                except Exception as e:  # pragma: no cover - sending may fail
                                    print(
                                        f"⚠️ Reminder mirror failed to #{tgt_name}: {e}"
                                    )
                                else:
                                    sent_to.add(tgt_channel.id)
                    except Exception as e:  # pragma: no cover - safety for LangRelay
                        print(f"⚠️ Reminder LangRelay integration failed: {e}")

                info["last"] = now

                if info["one_time"]:
                    loop_obj = info["task"]
                    loop_obj.stop()
                    self.bot.loop.call_soon(loop_obj.cancel)
                    del self.reminders[guild_id][name]
                    if not self.reminders[guild_id]:
                        del self.reminders[guild_id]
                self.save_reminders()

        loop = tasks.loop(seconds=60)(send_reminder)
        default_last = (
            0.0 if any(v is not None for v in (weekday, hour, minute)) else time.time()
        )
        self.guild_settings.setdefault(guild_id, True)
        self.reminders.setdefault(guild_id, {})[name] = {
            "interval": interval,
            "unit": unit,
            "headline": headline,
            "weekday": weekday,
            "hour": hour,
            "minute": minute,
            "channel_id": channel_id,
            "message": message,
            "task": loop,
            "last": last if last is not None else default_last,
            "one_time": one_time,
        }
        align_to_minute = minute is not None or hour is not None

        async def starter():
            await self.bot.wait_until_ready()
            if align_to_minute:
                delay = self._seconds_until_next_minute()
                if delay:
                    await asyncio.sleep(delay)
            loop.start()

        self.bot.loop.create_task(starter())
        if save:
            self.save_reminders()

    @staticmethod
    def _seconds_until_next_minute(now: datetime | None = None) -> float:
        """Seconds until the next minute boundary from ``now`` (UTC)."""

        now = now or datetime.utcnow()
        current_minute = now.replace(second=0, microsecond=0)
        if now == current_minute:
            return 0.0
        next_minute = current_minute + timedelta(minutes=1)
        return (next_minute - now).total_seconds()

    @staticmethod
    def _resolve_interval(
        interval: int | None,
        unit: str | None,
        weekday: int | None,
        has_time: bool,
    ) -> tuple[int, str]:
        """Determine final interval/unit values.

        If both ``interval`` and ``unit`` are provided, they are used directly.
        If neither is provided but ``weekday`` or ``has_time`` is given, sensible
        defaults are returned (weekly or daily).  Otherwise a ``ValueError`` is
        raised.
        """

        if interval is None and unit is None:
            if weekday is not None or has_time:
                # default to weekly when weekday specified, otherwise daily
                return (7 if weekday is not None else 1, "days")
            raise ValueError("interval and unit required without time/weekday")
        if interval is None or unit is None:
            raise ValueError("interval and unit must be given together")
        return (interval, unit)

    @staticmethod
    def _render_message(message: str) -> str:
        """Convert escaped newline sequences to actual newlines for output."""

        return message.replace("\\n", "\n")

    @reminder.command(name="add", description="Add a repeating reminder.")
    @app_commands.describe(
        name="Name for the reminder",
        channel="Channel to post in",
        message="Reminder message",
        headline="Optional headline for embed output",
        interval="Optional interval amount",
        unit="Optional interval unit",
        weekday="Optional day of week",
        time="Optional time of day HH:MM (UTC)",
        once="Send only once when triggered",
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
        channel: discord.TextChannel,
        message: str,
        headline: str | None = None,
        interval: int | None = None,
        unit: app_commands.Choice[str] | None = None,
        weekday: app_commands.Choice[int] | None = None,
        time: str | None = None,
        once: bool = False,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Guild only.", ephemeral=True)
            return
        guild_id = interaction.guild.id
        if name in self.reminders.get(guild_id, {}):

            await interaction.response.send_message(
                f"Reminder `{name}` exists.", ephemeral=True
            )
            return
        hour = minute = None
        if time:
            try:
                parsed = datetime.strptime(time, "%H:%M")
                hour, minute = parsed.hour, parsed.minute
            except ValueError:
                await interaction.response.send_message(
                    "Invalid time format; use HH:MM.", ephemeral=True
                )
                return

        try:
            interval_value, unit_value = self._resolve_interval(
                interval,
                unit.value if unit else None,
                weekday.value if weekday else None,
                bool(time),
            )
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        self.create_reminder(
            guild_id,
            name,
            interval_value,
            unit_value,
            channel.id,
            message,
            headline,
            weekday.value if weekday else None,
            hour,
            minute,
            one_time=once,
        )
        await interaction.response.send_message(
            f"Reminder `{name}` added.", ephemeral=True
        )

    @reminder.command(name="remove", description="Remove a reminder.")
    @app_commands.describe(name="Reminder to remove")
    async def remove(self, interaction: discord.Interaction, name: str) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Guild only.", ephemeral=True)
            return
        guild_id = interaction.guild.id
        guild_rems = self.reminders.get(guild_id, {})
        info = guild_rems.get(name)
        if not info:
            await interaction.response.send_message(f"No reminder `{name}`.", ephemeral=True)
            return
        info["task"].cancel()
        del guild_rems[name]
        if not guild_rems:
            del self.reminders[guild_id]
        self.save_reminders()
        await interaction.response.send_message(f"Reminder `{name}` removed.", ephemeral=True)

    @remove.autocomplete("name")
    async def remove_autocomplete(self, interaction: discord.Interaction, current: str):
        if not interaction.guild:
            return []
        guild_id = interaction.guild.id
        return [
            app_commands.Choice(name=n, value=n)
            for n in self.reminders.get(guild_id, {})
            if current.lower() in n.lower()
        ]


    @reminder.command(name="toggle", description="Enable or disable reminders for this server.")
    @app_commands.describe(enabled="Whether reminders should be enabled")
    async def toggle(self, interaction: discord.Interaction, enabled: bool) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Guild only.", ephemeral=True)
            return
        guild_id = interaction.guild.id
        self.guild_settings[guild_id] = enabled
        self.save_reminders()
        status = "enabled" if enabled else "disabled"
        await interaction.response.send_message(
            f"Reminders {status}.", ephemeral=True
        )


    @reminder.command(name="list", description="List active reminders.")
    async def list(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Guild only.", ephemeral=True)
            return
        guild_id = interaction.guild.id
        guild_rems = self.reminders.get(guild_id)
        if not guild_rems:
            await interaction.response.send_message("No reminders set.", ephemeral=True)
            return
        lines = []
        for name, info in guild_rems.items():
            channel = self.bot.get_channel(info["channel_id"])
            ch = channel.mention if channel else f"#{info['channel_id']}"
            days = [
                "Monday",
                "Tuesday",
                "Wednesday",
                "Thursday",
                "Friday",
                "Saturday",
                "Sunday",
            ]
            if info["one_time"]:
                schedule = "once"
                if info["weekday"] is not None:
                    schedule += f" on {days[info['weekday']]}"
                if info["hour"] is not None and info["minute"] is not None:
                    schedule += f" at {info['hour']:02d}:{info['minute']:02d}"
                elif info["interval"] and info["unit"]:
                    schedule += f" after {info['interval']} {info['unit']}"
            else:
                schedule = f"every {info['interval']} {info['unit']}"
                if info["weekday"] is not None:
                    schedule += f" on {days[info['weekday']]}"
                if info["hour"] is not None and info["minute"] is not None:
                    schedule += f" at {info['hour']:02d}:{info['minute']:02d}"
            message_preview = self._render_message(info["message"])
            if info.get("headline"):
                message_preview = f"{info['headline']}\n{message_preview}"
            formatted_message = message_preview.replace("\n", "\n    ")
            lines.append(f"`{name}` {schedule} in {ch}:\n    {formatted_message}")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Reminder(bot))
