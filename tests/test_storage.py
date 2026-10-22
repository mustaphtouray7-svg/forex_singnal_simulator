import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from market_analysis import build_setup_scenario
import storage


class StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "test.sqlite3"
        storage.init_db(self.database_path, initial_balance=10_000)
        self.instrument = {
            "name": "EUR/USD",
            "instrument_type": "Forex major",
            "provider": "yfinance",
            "provider_symbol": "EURUSD=X",
            "data_source": "Yahoo Finance",
        }
        self.summary = self.make_summary(datetime.now(timezone.utc))

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    @staticmethod
    def make_summary(timestamp: datetime, signal: int = 1) -> dict:
        return {
            "signal": signal,
            "close": 100.0,
            "rsi": 65.0,
            "atr": 2.0,
            "timestamp": timestamp.isoformat(),
            "market_structure": "Inside prior range",
            "support": 95.0,
            "resistance": 105.0,
            "scenario": build_setup_scenario(100.0, 2.0, signal),
            "rules": [
                {"description": "Trend", "passed": signal == 1},
                {"description": "Momentum", "passed": True},
            ],
        }

    def create_alert(self, timestamp: datetime = None) -> int:
        summary = self.summary if timestamp is None else self.make_summary(timestamp)
        result = storage.record_analysis(
            self.instrument, "1d", summary, "Fresh", self.database_path
        )
        self.assertIsNotNone(result["alert_id"])
        return result["alert_id"]

    def test_analysis_and_alert_creation_are_idempotent(self) -> None:
        first = storage.record_analysis(
            self.instrument, "1d", self.summary, "Fresh", self.database_path
        )
        second = storage.record_analysis(
            self.instrument, "1d", self.summary, "Fresh", self.database_path
        )
        self.assertTrue(first["alert_created"])
        self.assertFalse(second["alert_created"])
        self.assertEqual(first["alert_id"], second["alert_id"])
        self.assertEqual(len(storage.get_analysis_history(self.database_path)), 1)
        self.assertEqual(len(storage.get_alerts(self.database_path)), 1)

    def test_no_trade_analysis_is_saved_without_creating_alert(self) -> None:
        no_trade = self.make_summary(datetime.now(timezone.utc), signal=0)
        result = storage.record_analysis(
            self.instrument, "1d", no_trade, "Fresh", self.database_path
        )
        self.assertIsNone(result["alert_id"])
        self.assertEqual(storage.get_alerts(self.database_path), [])
        self.assertEqual(len(storage.get_analysis_history(self.database_path)), 1)

    def test_paper_trade_open_close_records_costs_and_net_pnl(self) -> None:
        alert_id = self.create_alert()
        opened = storage.open_paper_trade(
            alert_id,
            1,
            100.0,
            build_setup_scenario(100.0, 2.0, 1),
            1.0,
            3.0,
            5,
            2.0,
            self.database_path,
        )
        self.assertAlmostEqual(opened["risk_amount"], 100.0)
        self.assertGreater(opened["quantity"], 0)
        mark = storage.mark_paper_trade(
            storage.get_paper_trades("open", self.database_path)[0], 103.0, 2.0
        )
        self.assertGreater(mark["estimated_net_unrealized_pnl"], 0)
        with self.assertRaisesRegex(ValueError, "already exists"):
            storage.open_paper_trade(
                alert_id,
                1,
                100.0,
                build_setup_scenario(100.0, 2.0, 1),
                1.0,
                3.0,
                5,
                2.0,
                self.database_path,
            )

        closed = storage.close_paper_trade(
            opened["trade_id"], 103.0, 2.0, self.database_path
        )
        self.assertGreater(closed["gross_pnl"], closed["net_pnl"])
        self.assertEqual(storage.get_paper_trades("open", self.database_path), [])
        self.assertEqual(storage.get_paper_trades("closed", self.database_path)[0]["status"], "closed")
        self.assertGreater(
            storage.get_paper_account(self.database_path)["balance"], 10_000
        )

    def test_risk_limit_rejects_additional_open_exposure(self) -> None:
        first_alert = self.create_alert()
        storage.open_paper_trade(
            first_alert,
            1,
            100.0,
            build_setup_scenario(100.0, 2.0, 1),
            1.0,
            1.0,
            5,
            0.0,
            self.database_path,
        )
        second_alert = self.create_alert(datetime.now(timezone.utc) + timedelta(minutes=1))
        with self.assertRaisesRegex(ValueError, "total open-risk limit"):
            storage.open_paper_trade(
                second_alert,
                1,
                100.0,
                build_setup_scenario(100.0, 2.0, 1),
                1.0,
                1.0,
                5,
                0.0,
                self.database_path,
            )

    def test_rejected_alert_cannot_create_a_paper_trade(self) -> None:
        alert_id = self.create_alert()
        storage.update_alert_status(alert_id, "rejected", self.database_path)
        with self.assertRaisesRegex(ValueError, "not eligible"):
            storage.open_paper_trade(
                alert_id,
                1,
                100.0,
                build_setup_scenario(100.0, 2.0, 1),
                1.0,
                3.0,
                5,
                0,
                self.database_path,
            )

    def test_notification_claim_prevents_duplicates_and_records_retry(self) -> None:
        alert_id = self.create_alert()
        claimed = storage.claim_notification(alert_id, self.database_path)
        self.assertIsNotNone(claimed)
        self.assertIsNone(storage.claim_notification(alert_id, self.database_path))

        storage.finish_notification(
            alert_id, False, "temporary failure", self.database_path
        )
        self.assertIsNone(storage.claim_notification(alert_id, self.database_path))
        storage.finish_notification(alert_id, True, path=self.database_path)
        self.assertIsNone(storage.claim_notification(alert_id, self.database_path))

    def test_stale_analysis_does_not_create_an_actionable_alert(self) -> None:
        stale_summary = self.make_summary(datetime.now(timezone.utc))
        result = storage.record_analysis(
            self.instrument, "1d", stale_summary, "Stale", self.database_path
        )
        self.assertIsNone(result["alert_id"])
