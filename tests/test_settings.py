import tempfile
import unittest
from pathlib import Path

from settings import load_settings, save_settings


class SettingsTests(unittest.TestCase):
    def test_settings_save_and_reload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "settings.json"
            settings = load_settings()
            settings["monitor_interval_seconds"] = 1200
            save_settings(settings, destination)
            self.assertEqual(load_settings(destination)["monitor_interval_seconds"], 1200)

    def test_invalid_settings_are_rejected(self) -> None:
        settings = load_settings()
        settings["risk_per_trade_pct"] = 7.0
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                save_settings(settings, Path(directory) / "settings.json")


if __name__ == "__main__":
    unittest.main()
