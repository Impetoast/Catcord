import datetime
import importlib.util
import pathlib
import unittest


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


if __name__ == '__main__':
    unittest.main()
