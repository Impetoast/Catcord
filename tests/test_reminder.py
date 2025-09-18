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


if __name__ == '__main__':
    unittest.main()
