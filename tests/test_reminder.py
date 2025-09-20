import asyncio
import datetime
import importlib.util
import pathlib
import unittest
import tempfile
from unittest import mock


spec = importlib.util.spec_from_file_location(
    "reminder", pathlib.Path(__file__).resolve().parents[1] / "cogs" / "reminder.py"
)
reminder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reminder)
Reminder = reminder.Reminder


class ResolveIntervalTest(unittest.TestCase):
    def test_defaults_daily(self):
        interval, unit = Reminder._resolve_interval(None, None, None, True)
        self.assertEqual(interval, 1)
        self.assertEqual(unit, "days")

    def test_defaults_weekly(self):
        interval, unit = Reminder._resolve_interval(None, None, 2, False)
        self.assertEqual(interval, 7)
        self.assertEqual(unit, "days")

    def test_requires_both_interval_and_unit(self):
        with self.assertRaises(ValueError):
            Reminder._resolve_interval(1, None, None, False)
        with self.assertRaises(ValueError):
            Reminder._resolve_interval(None, "days", None, False)

    def test_requires_info(self):
        with self.assertRaises(ValueError):
            Reminder._resolve_interval(None, None, None, False)


class SecondsUntilNextMinuteTest(unittest.TestCase):
    def test_exact_boundary(self):
        now = datetime.datetime(2024, 1, 1, 12, 0, 0)
        self.assertEqual(Reminder._seconds_until_next_minute(now), 0.0)

    def test_half_minute(self):
        now = datetime.datetime(2024, 1, 1, 12, 0, 30)
        self.assertEqual(Reminder._seconds_until_next_minute(now), 30)

    def test_fractional_second(self):
        now = datetime.datetime(2024, 1, 1, 12, 0, 59, 500000)
        self.assertAlmostEqual(Reminder._seconds_until_next_minute(now), 0.5)


class ParseTimesArgumentTest(unittest.TestCase):
    def test_parse_with_weekdays(self):
        entries = Reminder._parse_times_argument("Mon@09:00, Tue@10:30")
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0], {"weekday": 0, "hour": 9, "minute": 0})
        self.assertEqual(entries[1], {"weekday": 1, "hour": 10, "minute": 30})

    def test_parse_daily(self):
        entries = Reminder._parse_times_argument("09:15")
        self.assertEqual(entries, [{"weekday": None, "hour": 9, "minute": 15}])

    def test_parse_shared_weekday(self):
        entries = Reminder._parse_times_argument("Mon@09:00, 10:30")
        self.assertEqual(entries[0], {"weekday": 0, "hour": 9, "minute": 0})
        self.assertEqual(entries[1], {"weekday": 0, "hour": 10, "minute": 30})

    def test_parse_conflicting_shared_weekday(self):
        with self.assertRaises(ValueError):
            Reminder._parse_times_argument("Mon@09:00, Tue@10:30, 11:45")

    def test_invalid_entry(self):
        with self.assertRaises(ValueError):
            Reminder._parse_times_argument("notatime")


class MergeRemoveTimesTest(unittest.TestCase):
    def test_merge_adds_unique_entries(self):
        existing = [{"weekday": None, "hour": 9, "minute": 0, "last": 0.0}]
        additions = [
            {"weekday": None, "hour": 9, "minute": 0},
            {"weekday": 2, "hour": 14, "minute": 30},
        ]
        merged, added = Reminder._merge_time_entries(existing, additions)
        self.assertEqual(added, 1)
        identities = {Reminder._time_identity(entry) for entry in merged}
        self.assertIn((None, 9, 0), identities)
        self.assertIn((2, 14, 30), identities)

    def test_remove_eliminates_matches(self):
        existing = [
            {"weekday": 0, "hour": 9, "minute": 0, "last": 0.0},
            {"weekday": 1, "hour": 9, "minute": 0, "last": 0.0},
        ]
        removals = [{"weekday": 0, "hour": 9, "minute": 0}]
        reduced, removed = Reminder._remove_time_entries(existing, removals)
        self.assertEqual(removed, 1)
        self.assertEqual(len(reduced), 1)
        self.assertEqual(Reminder._time_identity(reduced[0]), (1, 9, 0))

    def test_ensure_times_converts_single_schedule(self):
        info = {"weekday": 3, "hour": 12, "minute": 45, "last": 5.0}
        times = Reminder._ensure_times_container(info)
        self.assertEqual(len(times), 1)
        self.assertEqual(times[0]["weekday"], 3)
        self.assertEqual(times[0]["hour"], 12)
        self.assertEqual(times[0]["minute"], 45)
        self.assertIsNone(info.get("weekday"))
        self.assertIsNone(info.get("hour"))
        self.assertIsNone(info.get("minute"))


class ReminderChannelUpdateTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._original_data_dir = reminder.DATA_DIR
        self._temp_dir = tempfile.TemporaryDirectory()
        reminder.DATA_DIR = pathlib.Path(self._temp_dir.name)
        reminder.DATA_DIR.mkdir(parents=True, exist_ok=True)

    async def asyncTearDown(self):
        reminder.DATA_DIR = self._original_data_dir
        self._temp_dir.cleanup()

    async def test_loop_uses_updated_channel_id(self):
        class FakeChannel:
            def __init__(self, channel_id: int):
                self.id = channel_id
                self.sent: list[dict] = []
                self.mention = f"<#{channel_id}>"
                self.name = f"channel-{channel_id}"

            async def send(self, **kwargs):
                self.sent.append(kwargs)

        class FakeAsyncLoop:
            def __init__(self):
                self.tasks: list[asyncio.Task] = []

            def create_task(self, coro):
                task = asyncio.create_task(coro)
                self.tasks.append(task)
                return task

            def call_soon(self, func, *args, **kwargs):
                func(*args, **kwargs)

        class FakeBot:
            def __init__(self):
                self.loop = FakeAsyncLoop()
                self.channels: dict[int, FakeChannel] = {}

            async def wait_until_ready(self):
                return

            def get_channel(self, channel_id: int):
                return self.channels.get(channel_id)

            def get_cog(self, name: str):
                return None

        class StubLoop:
            def __init__(self, coro):
                self.coro = coro

            def start(self):
                return None

            def stop(self):
                return None

            def cancel(self):
                return None

        def stub_tasks_loop(*args, **kwargs):
            def decorator(coro):
                return StubLoop(coro)

            return decorator

        fake_bot = FakeBot()
        original_channel = FakeChannel(101)
        updated_channel = FakeChannel(202)
        fake_bot.channels = {
            original_channel.id: original_channel,
            updated_channel.id: updated_channel,
        }

        with mock.patch.object(reminder.tasks, "loop", new=stub_tasks_loop):
            cog = Reminder(fake_bot)
            cog.reminders.clear()
            cog.guild_settings.clear()
            cog.create_reminder(
                guild_id=1,
                name="demo",
                interval=1,
                unit="minutes",
                channel_id=original_channel.id,
                message="hello",
            )
            await asyncio.gather(*fake_bot.loop.tasks, return_exceptions=True)

            info = cog.reminders[1]["demo"]
            info["channel_id"] = updated_channel.id
            info["last"] = 0.0

            original_gmtime = reminder.time.gmtime

            with mock.patch.object(reminder.time, "time", return_value=120), mock.patch.object(
                reminder.time, "gmtime", side_effect=lambda ts=None: original_gmtime(120)
            ):
                await info["task"].coro()

        self.assertEqual(original_channel.sent, [])
        self.assertEqual(len(updated_channel.sent), 1)
        self.assertEqual(updated_channel.sent[0].get("content"), "hello")


if __name__ == '__main__':
    unittest.main()
