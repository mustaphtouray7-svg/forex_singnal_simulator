import unittest
from unittest.mock import patch

import pandas as pd

from market_data import fetch_prices


class MarketDataTests(unittest.TestCase):
    @patch("market_data.yf.download")
    def test_supported_provider_normalizes_single_symbol_history(self, download) -> None:
        index = pd.date_range("2025-01-01", periods=2, tz="UTC")
        download.return_value = pd.DataFrame(
            {
                "open": [1.0, 1.1],
                "high": [1.2, 1.3],
                "low": [0.9, 1.0],
                "close": [1.1, 1.2],
                "volume": [100, 200],
            },
            index=index,
        )

        result = fetch_prices("yfinance", "EURUSD=X", "1d", "1mo")

        self.assertEqual(list(result.columns), ["Open", "High", "Low", "Close", "Volume"])
        self.assertEqual(result["Close"].iloc[-1], 1.2)
        download.assert_called_once()

    @patch("market_data.yf.download")
    def test_unavailable_provider_data_returns_empty_frame(self, download) -> None:
        download.return_value = pd.DataFrame()
        result = fetch_prices("yfinance", "MISSING=X", "1d", "1mo")
        self.assertTrue(result.empty)

    @patch("market_data.yf.download")
    def test_provider_bars_are_sorted_chronologically(self, download) -> None:
        index = pd.to_datetime(["2025-01-02", "2025-01-01"], utc=True)
        download.return_value = pd.DataFrame(
            {
                "Open": [2.0, 1.0],
                "High": [2.5, 1.5],
                "Low": [1.5, 0.5],
                "Close": [2.0, 1.0],
            },
            index=index,
        )
        result = fetch_prices("yfinance", "EURUSD=X", "1d", "1mo")
        self.assertEqual(result.index[0], pd.Timestamp("2025-01-01", tz="UTC"))

    def test_unconfigured_provider_fails_explicitly(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported data provider"):
            fetch_prices("unconfigured", "EURUSD=X", "1d", "1mo")

    @patch("market_data.yf.download")
    def test_usd_base_yahoo_symbols_keep_usd_quote_convention(self, download) -> None:
        index = pd.date_range("2025-01-01", periods=1, tz="UTC")
        candle = pd.DataFrame(
            {"Open": [157.0], "High": [158.0], "Low": [156.0], "Close": [157.5]},
            index=index,
        )
        for symbol in ("JPY=X", "CHF=X", "CAD=X"):
            with self.subTest(symbol=symbol):
                download.return_value = candle.copy()
                result = fetch_prices("yfinance", symbol, "1d", "1mo")
                self.assertEqual(float(result["Open"].iloc[0]), 157.0)
                self.assertEqual(float(result["High"].iloc[0]), 158.0)
                self.assertEqual(float(result["Low"].iloc[0]), 156.0)
                self.assertEqual(float(result["Close"].iloc[0]), 157.5)


if __name__ == "__main__":
    unittest.main()
