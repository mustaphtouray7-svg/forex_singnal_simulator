import unittest

import pandas as pd

from paper_trading import revalidate_alert


class PaperRevalidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.alert = {"signal": 1, "timeframe": "1d"}

    @staticmethod
    def prices(last_timestamp: pd.Timestamp) -> pd.DataFrame:
        index = pd.date_range(end=last_timestamp, periods=80, freq="D")
        close = pd.Series(range(1, 81), index=index, dtype=float)
        return pd.DataFrame(
            {"Open": close, "High": close + 1, "Low": close - 1, "Close": close}
        )

    def test_fresh_matching_signal_is_eligible_for_paper_entry(self) -> None:
        prices = self.prices(pd.Timestamp.now(tz="UTC").normalize())
        summary = revalidate_alert(self.alert, prices, 2, 4, 3, 3)
        self.assertEqual(summary["signal"], 1)
        self.assertEqual(summary["freshness"], "Fresh")

    def test_stale_signal_is_blocked(self) -> None:
        old = self.prices(
            pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=7)
        )
        with self.assertRaisesRegex(ValueError, "stale"):
            revalidate_alert(self.alert, old, 2, 4, 3, 3)

    def test_direction_change_is_blocked(self) -> None:
        falling = pd.Series(range(80, 0, -1), dtype=float)
        index = pd.date_range(
            end=pd.Timestamp.now(tz="UTC").normalize(), periods=80, freq="D"
        )
        prices = pd.DataFrame(
            {
                "Open": falling.values,
                "High": falling.values + 1,
                "Low": falling.values - 1,
                "Close": falling.values,
            },
            index=index,
        )
        with self.assertRaisesRegex(ValueError, "no longer matches"):
            revalidate_alert(self.alert, prices, 2, 4, 3, 3)


if __name__ == "__main__":
    unittest.main()
