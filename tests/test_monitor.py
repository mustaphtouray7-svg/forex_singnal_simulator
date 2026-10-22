import tempfile
import unittest
from pathlib import Path

import pandas as pd

import storage
from instrument_registry import load_instruments
from monitor import scan_once
from settings import load_settings


class MonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "monitor.sqlite3"
        self.settings = load_settings()
        self.settings.update(
            {
                "fast_ema": 2,
                "slow_ema": 4,
                "rsi_period": 3,
                "atr_period": 3,
                "requests_per_minute": 60,
                "monitor_timeframes": ["1d"],
                "monitor_lookbacks": {"1d": "2y"},
                "watchlist": ["EURUSD=X"],
            }
        )
        self.instrument = next(
            instrument
            for instrument in load_instruments(
                Path(__file__).parents[1] / "config" / "instruments.json"
            )
            if instrument["provider_symbol"] == "EURUSD=X"
        )
        storage.init_db(self.database_path, initial_balance=10_000)
        self.now = pd.Timestamp.now(tz="UTC").normalize()
        self.current_prices = self.make_prices(self.now)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    @staticmethod
    def make_prices(last_timestamp: pd.Timestamp) -> pd.DataFrame:
        index = pd.date_range(end=last_timestamp, periods=80, freq="D")
        close = pd.Series(range(1, 81), index=index, dtype=float)
        return pd.DataFrame(
            {"Open": close, "High": close + 1, "Low": close - 1, "Close": close}
        )

    def test_scan_generates_and_persists_fresh_signal(self) -> None:
        result = scan_once(
            self.settings,
            [self.instrument],
            str(self.database_path),
            fetcher=lambda *_args: self.current_prices,
            sleep_fn=lambda _seconds: None,
            monotonic_fn=lambda: 1.0,
        )
        self.assertEqual(result, {"requests": 1, "records": 1, "alerts": 1, "failures": 0})
        self.assertEqual(len(storage.get_alerts(self.database_path)), 1)
        self.assertEqual(storage.get_alerts(self.database_path)[0]["freshness"], "Fresh")

    def test_stale_scan_records_analysis_but_does_not_alert(self) -> None:
        old_prices = self.make_prices(self.now - pd.Timedelta(days=7))
        result = scan_once(
            self.settings,
            [self.instrument],
            str(self.database_path),
            fetcher=lambda *_args: old_prices,
            sleep_fn=lambda _seconds: None,
            monotonic_fn=lambda: 1.0,
        )
        self.assertEqual(result["alerts"], 0)
        self.assertEqual(result["records"], 1)
        self.assertEqual(storage.get_alerts(self.database_path), [])
        self.assertEqual(storage.get_analysis_history(self.database_path)[0]["freshness"], "Stale")

    def test_provider_error_is_logged_and_scan_recovers(self) -> None:
        def fail_fetch(*_args):
            raise RuntimeError("temporary provider error")

        result = scan_once(
            self.settings,
            [self.instrument],
            str(self.database_path),
            fetcher=fail_fetch,
            sleep_fn=lambda _seconds: None,
            monotonic_fn=lambda: 1.0,
        )
        self.assertEqual(result["failures"], 1)
        events = storage.get_system_events(self.database_path)
        self.assertIn("temporary provider error", events[0]["message"])

    def test_unconfigured_watchlist_symbol_is_reported(self) -> None:
        self.settings["watchlist"] = ["UNSUPPORTED"]
        result = scan_once(
            self.settings,
            [self.instrument],
            str(self.database_path),
            sleep_fn=lambda _seconds: None,
            monotonic_fn=lambda: 1.0,
        )
        self.assertEqual(result["failures"], 1)
        self.assertIn("not in the registry", storage.get_system_events(self.database_path)[0]["message"])

    def test_provider_request_rate_limit_is_applied_between_calls(self) -> None:
        self.settings["monitor_timeframes"] = ["1d", "60m"]
        self.settings["monitor_lookbacks"] = {"1d": "2y", "60m": "1mo"}
        self.settings["requests_per_minute"] = 60
        clock = {"now": 0.0}
        sleeps = []

        def fetch(*_args):
            clock["now"] += 0.2
            return self.current_prices

        def sleep(seconds):
            sleeps.append(seconds)
            clock["now"] += seconds

        result = scan_once(
            self.settings,
            [self.instrument],
            str(self.database_path),
            fetcher=fetch,
            sleep_fn=sleep,
            monotonic_fn=lambda: clock["now"],
        )
        self.assertEqual(result["requests"], 2)
        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0], 1.0)
