import pathlib
import importlib.util
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


if __name__ == '__main__':
    unittest.main()
