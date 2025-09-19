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

    DAY_NAME_ALIASES = {
        "monday": 0,
        "mon": 0,
        "tuesday": 1,
        "tue": 1,
        "tues": 1,
        "wednesday": 2,
        "wed": 2,
        "thursday": 3,
        "thu": 3,
        "thur": 3,
        "thurs": 3,
        "friday": 4,
        "fri": 4,
        "saturday": 5,
        "sat": 5,
        "sunday": 6,
        "sun": 6,
    }

    DAY_NAMES = [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ]

    # slash command groups
    reminder = app_commands.Group(name="reminder", description="Reminder utilities")
    group_admin = app_commands.Group(
        name="group", description="Manage reminder groups"
    )
    reminder.add_command(group_admin)

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.reminders: dict[int, dict[str, dict]] = {}
        self.guild_settings: dict[int, bool] = {}
        self.load_reminders()

    def save_reminders(self) -> None:
        existing = {p.stem: p for p in DATA_DIR.glob("*.json")}
        tracked_ids = set(self.reminders) | set(self.guild_settings)
        for gid, path in existing.items():
            if int(gid) not in tracked_ids:
                path.unlink()
        for guild_id in tracked_ids:
            path = DATA_DIR / f"{guild_id}.json"
            rems = self.reminders.get(guild_id, {})
            reminder_payload = {}
            for name, info in rems.items():
                reminder_payload[name] = {
                    "interval": info.get("interval"),
                    "unit": info.get("unit"),
                    "headline": info.get("headline"),
                    "channel_id": info["channel_id"],
                    "message": info["message"],
                    "last": info.get("last", 0.0),
                    "one_time": info.get("one_time", False),
                    "group": info.get("group"),
                    "weekday": info.get("weekday"),
                    "hour": info.get("hour"),
                    "minute": info.get("minute"),
                    "times": [
                        {
                            "weekday": t.get("weekday"),
                            "hour": t.get("hour"),
                            "minute": t.get("minute"),
                            "last": t.get("last", 0.0),
                        }
                        for t in info.get("times", [])
                    ],
                }
            payload = {"__settings": {"enabled": self.guild_settings.get(guild_id, True)}}
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
                interval = info.get("interval")
                unit = info.get("unit")
                channel_id = info.get("channel_id")
                message = info.get("message", "")
                headline = info.get("headline")
                last = info.get("last")
                one_time = info.get("one_time", False)
                group = info.get("group")
                weekday = info.get("weekday")
                hour = info.get("hour")
                minute = info.get("minute")
                times_data = []
                raw_times = info.get("times")
                if isinstance(raw_times, list):
                    for entry in raw_times:
                        if not isinstance(entry, dict):
                            continue
                        times_data.append(
                            {
                                "weekday": entry.get("weekday"),
                                "hour": entry.get("hour"),
                                "minute": entry.get("minute"),
                                "last": entry.get("last"),
                            }
                        )
                elif hour is not None and minute is not None:
                    times_data.append(
                        {
                            "weekday": weekday,
                            "hour": hour,
                            "minute": minute,
                            "last": last,
                        }
                    )
                self.create_reminder(
                    guild_id,
                    name,
                    interval,
                    unit,
                    channel_id,
                    message,
                    headline,
                    weekday,
                    hour,
                    minute,
                    last,
                    one_time,
                    group=group,
                    times=times_data if times_data else None,
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
        interval: int | None,
        unit: str | None,
        channel_id: int,
        message: str,
        headline: str | None = None,
        weekday: int | None = None,
        hour: int | None = None,
        minute: int | None = None,
        last: float | None = None,
        one_time: bool = False,
        group: str | None = None,
        times: list[dict] | None = None,
        save: bool = True,
    ) -> None:
        seconds_per_unit = {"minutes": 60, "hours": 3600, "days": 86400}
        normalized_times = self._prepare_times(times, weekday, hour, minute, last)

        async def send_reminder():
            now = time.time()
            info = self.reminders[guild_id][name]
            if not self.guild_settings.get(guild_id, True):
                return
            interval_value = info.get("interval")
            unit_value = info.get("unit")
            interval_seconds = None
            if interval_value is not None and unit_value:
                interval_seconds = interval_value * seconds_per_unit.get(unit_value, 1)
            tm = time.gmtime(now)
            matching_times: list[dict] = []
            for schedule in info.get("times", []):
                sched_weekday = schedule.get("weekday")
                if sched_weekday is not None and tm.tm_wday != sched_weekday:
                    continue
                if tm.tm_hour != schedule.get("hour"):
                    continue
                if tm.tm_min != schedule.get("minute"):
                    continue
                last_run = float(schedule.get("last", 0.0))
                if now - last_run < 60:
                    continue
                matching_times.append(schedule)
            if not matching_times:
                if interval_seconds is None:
                    return
                last_run = float(info.get("last", 0.0))
                if now - last_run < interval_seconds:
                    return
                stored_weekday = info.get("weekday")
                stored_hour = info.get("hour")
                stored_minute = info.get("minute")
                if stored_weekday is not None and tm.tm_wday != stored_weekday:
                    return
                if stored_hour is not None and tm.tm_hour != stored_hour:
                    return
                if stored_minute is not None and tm.tm_min != stored_minute:
                    return
            channel = self.bot.get_channel(channel_id)
            if channel:
                render_text = self._render_message(info["message"])
                embed = None
                if info.get("headline"):
                    embed = discord.Embed(title=info["headline"], description=render_text)
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
                                    print(f"⚠️ Reminder mirror failed to #{tgt_name}: {e}")
                                else:
                                    sent_to.add(tgt_channel.id)
                    except Exception as e:  # pragma: no cover - safety for LangRelay
                        print(f"⚠️ Reminder LangRelay integration failed: {e}")

                now_time = time.time()
                info["last"] = now_time
                for schedule in matching_times:
                    schedule["last"] = now_time

                if info.get("one_time"):
                    loop_obj = info["task"]
                    loop_obj.stop()
                    self.bot.loop.call_soon(loop_obj.cancel)
                    del self.reminders[guild_id][name]
                    if not self.reminders[guild_id]:
                        del self.reminders[guild_id]
                self.save_reminders()

        loop = tasks.loop(seconds=60)(send_reminder)
        has_time_constraints = bool(normalized_times) or any(
            v is not None for v in (weekday, hour, minute)
        )
        default_last = 0.0 if has_time_constraints else time.time()
        self.guild_settings.setdefault(guild_id, True)
        self.reminders.setdefault(guild_id, {})[name] = {
            "interval": interval,
            "unit": unit,
            "headline": headline,
            "weekday": weekday if not normalized_times else None,
            "hour": hour if not normalized_times else None,
            "minute": minute if not normalized_times else None,
            "channel_id": channel_id,
            "message": message,
            "task": loop,
            "last": last if last is not None else default_last,
            "one_time": one_time,
            "times": normalized_times,
            "group": group,
        }
        align_to_minute = has_time_constraints

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

    def _group_names(self, guild_id: int) -> list[str]:
        names = {
            info.get("group")
            for info in self.reminders.get(guild_id, {}).values()
            if info.get("group")
        }
        return sorted(names, key=lambda value: value.lower())

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
        """Determine final interval/unit values."""

        if interval is None and unit is None:
            if weekday is not None or has_time:
                return (7 if weekday is not None else 1, "days")
            raise ValueError("interval and unit required without time/weekday")
        if interval is None or unit is None:
            raise ValueError("interval and unit must be given together")
        return (interval, unit)

    @staticmethod
    def _render_message(message: str) -> str:
        """Convert escaped newline sequences to actual newlines for output."""

        return message.replace("\\n", "\n")

    @classmethod
    def _parse_times_argument(cls, value: str) -> list[dict[str, int | None]]:
        entries: list[dict[str, int | None]] = []
        for raw in value.split(","):
            token = raw.strip()
            if not token:
                continue
            entries.append(cls._parse_single_time(token))
        if not entries:
            raise ValueError("No valid times provided.")
        return entries

    @classmethod
    def _parse_single_time(cls, token: str) -> dict[str, int | None]:
        token = token.strip()
        weekday = None
        time_part = token
        if "@" in token:
            day_part, time_part = token.split("@", 1)
            weekday = cls._normalize_weekday(day_part.strip())
        else:
            parts = token.split()
            if len(parts) == 2 and cls._maybe_weekday(parts[0]):
                weekday = cls._normalize_weekday(parts[0])
                time_part = parts[1]
        hour, minute = cls._parse_hour_minute(time_part.strip())
        return {"weekday": weekday, "hour": hour, "minute": minute}

    @classmethod
    def _maybe_weekday(cls, value: str) -> bool:
        try:
            cls._normalize_weekday(value)
            return True
        except ValueError:
            return False

    @classmethod
    def _normalize_weekday(cls, value: str | int | None) -> int | None:
        if value is None:
            return None
        if isinstance(value, int):
            if 0 <= value <= 6:
                return value
            raise ValueError("Weekday must be between 0 and 6.")
        value = value.strip().lower()
        if value.isdigit():
            idx = int(value)
            if 0 <= idx <= 6:
                return idx
            raise ValueError("Weekday must be between 0 and 6.")
        normalized = value.replace("-", "")
        if normalized in cls.DAY_NAME_ALIASES:
            return cls.DAY_NAME_ALIASES[normalized]
        raise ValueError(f"Unknown weekday `{value}`.")

    @staticmethod
    def _parse_hour_minute(value: str) -> tuple[int, int]:
        try:
            parsed = datetime.strptime(value, "%H:%M")
        except ValueError as exc:
            raise ValueError("Invalid time format; use HH:MM.") from exc
        return parsed.hour, parsed.minute

    @staticmethod
    def _prepare_times(
        times: list[dict] | None,
        weekday: int | None,
        hour: int | None,
        minute: int | None,
        fallback_last: float | None,
    ) -> list[dict]:
        normalized: list[dict] = []
        default_last = float(fallback_last) if fallback_last is not None else 0.0
        if times:
            for entry in times:
                if not isinstance(entry, dict):
                    continue
                sched_hour = entry.get("hour")
                sched_minute = entry.get("minute")
                if sched_hour is None or sched_minute is None:
                    continue
                normalized.append(
                    {
                        "weekday": entry.get("weekday"),
                        "hour": int(sched_hour),
                        "minute": int(sched_minute),
                        "last": float(entry.get("last", default_last)),
                    }
                )
        if not normalized and hour is not None and minute is not None:
            normalized.append(
                {
                    "weekday": weekday,
                    "hour": hour,
                    "minute": minute,
                    "last": default_last,
                }
            )
        return normalized

    @staticmethod
    def _time_identity(entry: dict) -> tuple[int | None, int, int] | None:
        hour = entry.get("hour")
        minute = entry.get("minute")
        if hour is None or minute is None:
            return None
        weekday = entry.get("weekday")
        weekday_val = int(weekday) if weekday is not None else None
        return (weekday_val, int(hour), int(minute))

    @classmethod
    def _merge_time_entries(
        cls, existing: list[dict], additions: list[dict]
    ) -> tuple[list[dict], int]:
        seen: set[tuple[int | None, int, int]] = set()
        merged: list[dict] = []
        for item in existing:
            ident = cls._time_identity(item)
            if ident is not None:
                seen.add(ident)
            merged.append(item)
        added_count = 0
        for entry in additions:
            ident = cls._time_identity(entry)
            if ident is None or ident in seen:
                continue
            merged.append(
                {
                    "weekday": entry.get("weekday"),
                    "hour": int(entry["hour"]),
                    "minute": int(entry["minute"]),
                    "last": float(entry.get("last", 0.0)),
                }
            )
            seen.add(ident)
            added_count += 1
        return merged, added_count

    @classmethod
    def _remove_time_entries(
        cls, existing: list[dict], removals: list[dict]
    ) -> tuple[list[dict], int]:
        targets: set[tuple[int | None, int, int]] = set()
        for entry in removals:
            ident = cls._time_identity(entry)
            if ident is not None:
                targets.add(ident)
        if not targets:
            return existing, 0
        kept: list[dict] = []
        removed_count = 0
        for entry in existing:
            ident = cls._time_identity(entry)
            if ident is not None and ident in targets:
                removed_count += 1
                continue
            kept.append(entry)
        return kept, removed_count

    @staticmethod
    def _ensure_times_container(info: dict) -> list[dict]:
        times: list[dict] | None = info.get("times")
        if times:
            return times
        hour = info.get("hour")
        minute = info.get("minute")
        if hour is not None and minute is not None:
            entry = {
                "weekday": info.get("weekday"),
                "hour": int(hour),
                "minute": int(minute),
                "last": float(info.get("last", 0.0)),
            }
            times = [entry]
            info["times"] = times
            info["weekday"] = None
            info["hour"] = None
            info["minute"] = None
            return times
        info["times"] = []
        return info["times"]

    @classmethod
    def _format_time_entry(cls, entry: dict) -> str:
        time_part = f"{int(entry['hour']):02d}:{int(entry['minute']):02d}"
        weekday = entry.get("weekday")
        if weekday is None:
            return time_part
        if 0 <= weekday < len(cls.DAY_NAMES):
            return f"{cls.DAY_NAMES[weekday]} {time_part}"
        return time_part

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
        times="Comma separated schedule entries (e.g. Mon@09:00, Tue@18:30)",
        group="Optional group name",
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
        times: str | None = None,
        group: str | None = None,
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

        weekday_value = weekday.value if weekday else None
        hour = minute = None
        schedules: list[dict[str, int | None]] = []
        if times:
            if time or weekday_value is not None:
                await interaction.response.send_message(
                    "Provide either `time` or `times`, not both.", ephemeral=True
                )
                return
            try:
                schedules = self._parse_times_argument(times)
            except ValueError as e:
                await interaction.response.send_message(str(e), ephemeral=True)
                return
        elif time:
            try:
                parsed = datetime.strptime(time, "%H:%M")
            except ValueError:
                await interaction.response.send_message(
                    "Invalid time format; use HH:MM.", ephemeral=True
                )
                return
            hour, minute = parsed.hour, parsed.minute
            schedules = [
                {
                    "weekday": weekday_value,
                    "hour": hour,
                    "minute": minute,
                }
            ]
        if schedules and (interval is not None or unit is not None):
            await interaction.response.send_message(
                "Specific times cannot be combined with an interval.",
                ephemeral=True,
            )
            return

        if schedules:
            interval_value = None
            unit_value = None
        else:
            try:
                interval_value, unit_value = self._resolve_interval(
                    interval,
                    unit.value if unit else None,
                    weekday_value,
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
            weekday_value if not schedules else None,
            hour,
            minute,
            one_time=once,
            group=group,
            times=schedules if schedules else None,
        )
        await interaction.response.send_message(
            f"Reminder `{name}` added.", ephemeral=True
        )

    @reminder.command(name="edit", description="Edit an existing reminder.")
    @app_commands.describe(
        name="Reminder to edit",
        new_name="Optional new reminder name",
        message="Updated reminder message",
        headline="Updated embed headline",
        clear_headline="Remove the stored headline",
        channel="Channel to send the reminder in",
        interval="New interval amount",
        unit="New interval unit",
        clear_interval="Remove the stored interval",
        weekday="Weekday for a single scheduled time",
        time="HH:MM time for a single schedule (UTC)",
        add_times="Comma separated schedule entries to add",
        remove_times="Comma separated schedule entries to remove",
        clear_times="Remove all stored schedule times",
        group="Group label to assign",
        clear_group="Remove the reminder from its group",
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
    async def edit(
        self,
        interaction: discord.Interaction,
        name: str,
        new_name: str | None = None,
        message: str | None = None,
        headline: str | None = None,
        clear_headline: bool = False,
        channel: discord.TextChannel | None = None,
        interval: int | None = None,
        unit: app_commands.Choice[str] | None = None,
        clear_interval: bool = False,
        weekday: app_commands.Choice[int] | None = None,
        time: str | None = None,
        add_times: str | None = None,
        remove_times: str | None = None,
        clear_times: bool = False,
        group: str | None = None,
        clear_group: bool = False,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Guild only.", ephemeral=True)
            return
        guild_id = interaction.guild.id
        guild_rems = self.reminders.get(guild_id, {})
        info = guild_rems.get(name)
        if not info:
            await interaction.response.send_message(
                f"No reminder `{name}`.", ephemeral=True
            )
            return

        updates: list[str] = []
        current_name = name

        if new_name and new_name != name:
            if new_name in guild_rems:
                await interaction.response.send_message(
                    f"Reminder `{new_name}` already exists.", ephemeral=True
                )
                return
            guild_rems[new_name] = info
            del guild_rems[name]
            current_name = new_name
            updates.append(f"renamed to `{new_name}`")

        if message is not None:
            info["message"] = message
            updates.append("updated message")

        if clear_headline:
            if info.get("headline") is not None:
                info["headline"] = None
                updates.append("cleared headline")
        elif headline is not None:
            info["headline"] = headline
            updates.append("updated headline")

        if channel is not None:
            info["channel_id"] = channel.id
            updates.append(f"channel → {channel.mention}")

        weekday_value = weekday.value if weekday else info.get("weekday")
        has_times = bool(info.get("times")) or (
            info.get("hour") is not None and info.get("minute") is not None
        )

        if clear_interval:
            if info.get("interval") is not None or info.get("unit") is not None:
                info["interval"] = None
                info["unit"] = None
                updates.append("cleared interval")
        elif interval is not None or unit is not None:
            try:
                interval_value, unit_value = self._resolve_interval(
                    interval,
                    unit.value if unit else None,
                    weekday_value,
                    has_times or bool(time) or bool(add_times),
                )
            except ValueError as e:
                await interaction.response.send_message(str(e), ephemeral=True)
                return
            info["interval"] = interval_value
            info["unit"] = unit_value
            updates.append(f"interval → every {interval_value} {unit_value}")

        if time is not None:
            try:
                hour, minute = self._parse_hour_minute(time)
            except ValueError as e:
                await interaction.response.send_message(str(e), ephemeral=True)
                return
            info["hour"] = hour
            info["minute"] = minute
            info["weekday"] = weekday.value if weekday else None
            info["times"] = []
            updates.append(
                f"single time → {self._format_time_entry({'weekday': info['weekday'], 'hour': hour, 'minute': minute})}"
            )
        elif weekday and not info.get("times"):
            info["weekday"] = weekday.value
            updates.append("updated weekday")

        if clear_times:
            if info.get("times"):
                info["times"] = []
                updates.append("cleared times")
            if time is None:
                info["weekday"] = None
                info["hour"] = None
                info["minute"] = None

        if add_times:
            try:
                additions = self._parse_times_argument(add_times)
            except ValueError as e:
                await interaction.response.send_message(str(e), ephemeral=True)
                return
            times_list = self._ensure_times_container(info)
            merged, added_count = self._merge_time_entries(times_list, additions)
            info["times"] = merged
            if added_count:
                updates.append(f"added {added_count} time(s)")

        if remove_times:
            try:
                removals = self._parse_times_argument(remove_times)
            except ValueError as e:
                await interaction.response.send_message(str(e), ephemeral=True)
                return
            times_list = self._ensure_times_container(info)
            reduced, removed_count = self._remove_time_entries(times_list, removals)
            info["times"] = reduced
            if removed_count:
                updates.append(f"removed {removed_count} time(s)")

        if group is not None:
            cleaned = group.strip()
            info["group"] = cleaned or None
            updates.append("set group" if cleaned else "cleared group")
        elif clear_group and info.get("group") is not None:
            info["group"] = None
            updates.append("cleared group")

        if not updates:
            await interaction.response.send_message(
                "No updates provided.", ephemeral=True
            )
            return

        self.save_reminders()
        await interaction.response.send_message(
            f"Reminder `{current_name}` updated (" + ", ".join(updates) + ").",
            ephemeral=True,
        )

    @edit.autocomplete("name")
    async def edit_autocomplete(self, interaction: discord.Interaction, current: str):
        if not interaction.guild:
            return []
        guild_id = interaction.guild.id
        return [
            app_commands.Choice(name=n, value=n)
            for n in self.reminders.get(guild_id, {})
            if current.lower() in n.lower()
        ]

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

    @group_admin.command(name="rename", description="Rename a reminder group.")
    @app_commands.describe(name="Existing group name", new_name="New group name")
    async def group_rename(
        self, interaction: discord.Interaction, name: str, new_name: str
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Guild only.", ephemeral=True)
            return
        guild_id = interaction.guild.id
        guild_rems = self.reminders.get(guild_id, {})
        matched = [
            info for info in guild_rems.values() if info.get("group") == name
        ]
        if not matched:
            await interaction.response.send_message(
                f"No group `{name}` found.", ephemeral=True
            )
            return
        cleaned = new_name.strip()
        if not cleaned:
            await interaction.response.send_message(
                "New group name cannot be empty.", ephemeral=True
            )
            return
        if cleaned == name:
            await interaction.response.send_message(
                "Group already has that name.", ephemeral=True
            )
            return
        for info in matched:
            info["group"] = cleaned
        self.save_reminders()
        await interaction.response.send_message(
            f"Group `{name}` renamed to `{cleaned}`.", ephemeral=True
        )

    @group_admin.command(name="remove", description="Remove a reminder group.")
    @app_commands.describe(
        name="Group name to remove",
        delete_reminders="Delete reminders in the group instead of ungrouping",
    )
    async def group_remove(
        self, interaction: discord.Interaction, name: str, delete_reminders: bool = False
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Guild only.", ephemeral=True)
            return
        guild_id = interaction.guild.id
        guild_rems = self.reminders.get(guild_id, {})
        matches = [
            (rem_name, info)
            for rem_name, info in list(guild_rems.items())
            if info.get("group") == name
        ]
        if not matches:
            await interaction.response.send_message(
                f"No group `{name}` found.", ephemeral=True
            )
            return
        if delete_reminders:
            for rem_name, info in matches:
                info["task"].cancel()
                del guild_rems[rem_name]
            if not guild_rems:
                self.reminders.pop(guild_id, None)
            action = "deleted"
        else:
            for _, info in matches:
                info["group"] = None
            action = "cleared"
        self.save_reminders()
        await interaction.response.send_message(
            f"Group `{name}` {action} ({len(matches)} reminder(s)).",
            ephemeral=True,
        )

    @group_rename.autocomplete("name")
    @group_remove.autocomplete("name")
    async def group_name_autocomplete(
        self, interaction: discord.Interaction, current: str
    ):
        if not interaction.guild:
            return []
        guild_id = interaction.guild.id
        return [
            app_commands.Choice(name=grp, value=grp)
            for grp in self._group_names(guild_id)
            if current.lower() in grp.lower()
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

        grouped: dict[str | None, list[tuple[str, dict]]] = {}
        for name, info in guild_rems.items():
            grouped.setdefault(info.get("group"), []).append((name, info))

        lines: list[str] = []
        groups_sorted = sorted(grouped, key=lambda g: (g is None, (g or "").lower()))
        for idx, group_name in enumerate(groups_sorted):
            items = grouped[group_name]
            header = "Ungrouped" if group_name is None else group_name
            if group_name is not None or len(groups_sorted) > 1:
                if lines:
                    lines.append("")
                lines.append(f"**{header}**")
            for name, info in sorted(items, key=lambda item: item[0].lower()):
                channel = self.bot.get_channel(info["channel_id"])
                ch = channel.mention if channel else f"#{info['channel_id']}"
                if info.get("times"):
                    formatted_times = ", ".join(
                        self._format_time_entry(t) for t in info["times"]
                    )
                    if info.get("one_time"):
                        schedule = f"once at {formatted_times}"
                    else:
                        schedule = f"at {formatted_times}"
                elif info.get("interval") is not None and info.get("unit"):
                    schedule = f"every {info['interval']} {info['unit']}"
                    weekday_value = info.get("weekday")
                    if weekday_value is not None and 0 <= weekday_value < len(self.DAY_NAMES):
                        schedule += f" on {self.DAY_NAMES[weekday_value]}"
                    if info.get("hour") is not None and info.get("minute") is not None:
                        schedule += f" at {info['hour']:02d}:{info['minute']:02d}"
                else:
                    schedule = "unscheduled"

                message_preview = self._render_message(info["message"])
                if info.get("headline"):
                    message_preview = f"{info['headline']}\n{message_preview}"
                formatted_message = message_preview.replace("\n", "\n    ")
                lines.append(
                    f"`{name}` {schedule} in {ch}:\n    {formatted_message}"
                )

        await interaction.response.send_message("\n".join(lines), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Reminder(bot))
