import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from instrument_registry import add_instrument, load_instruments
from market_analysis import (
    analyze_prices,
    backtest,
    backtest_summary,
    build_setup_scenario,
    latest_analysis,
    market_data_freshness,
)


class AnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.index = pd.date_range("2025-01-01", periods=80, freq="D", tz="UTC")

    def make_prices(self, close: list) -> pd.DataFrame:
        values = pd.Series(close, index=self.index[: len(close)], dtype=float)
        return pd.DataFrame(
            {"Open": values, "High": values + 1, "Low": values - 1, "Close": values}
        )

    def test_indicators_handle_flat_rising_and_falling_prices(self) -> None:
        flat = analyze_prices(self.make_prices([10.0] * 20), 2, 4, 3)
        rising = analyze_prices(self.make_prices([float(i) for i in range(1, 21)]), 2, 4, 3)
        falling = analyze_prices(self.make_prices([float(i) for i in range(20, 0, -1)]), 2, 4, 3)

        self.assertTrue((flat["RSI"].iloc[3:] == 50).all())
        self.assertEqual(rising["RSI"].iloc[-1], 100)
        self.assertEqual(falling["RSI"].iloc[-1], 0)
        self.assertGreater(rising["ATR"].iloc[-1], 0)
        self.assertGreater(rising["EMA_Fast"].iloc[-1], rising["EMA_Slow"].iloc[-1])
        self.assertLess(falling["EMA_Fast"].iloc[-1], falling["EMA_Slow"].iloc[-1])

    def test_signal_explains_passed_and_failed_rules(self) -> None:
        rising = self.make_prices([float(i) for i in range(1, 41)])
        result = latest_analysis(analyze_prices(rising, 2, 5, 3))

        self.assertEqual(result["signal"], 1)
        self.assertTrue(result["rules"][0]["passed"])
        self.assertTrue(result["rules"][1]["passed"])
        self.assertFalse(result["rules"][2]["passed"])
        self.assertFalse(result["rules"][3]["passed"])

    def test_falling_market_generates_bearish_signal(self) -> None:
        falling = self.make_prices([float(i) for i in range(40, 0, -1)])
        result = latest_analysis(analyze_prices(falling, 2, 5, 3))

        self.assertEqual(result["signal"], -1)
        self.assertTrue(result["rules"][2]["passed"])
        self.assertTrue(result["rules"][3]["passed"])

    def test_unclear_signal_is_no_trade(self) -> None:
        prices = self.make_prices([10.0] * 30)
        analysis = analyze_prices(prices, 2, 5, 3)
        self.assertEqual(analysis["Signal"].iloc[-1], 0)
        self.assertEqual(int(backtest(analysis, 1000, 2)["position"].iloc[-1]), 0)

    def test_backtest_uses_previous_bar_signal_and_charges_costs(self) -> None:
        prices = pd.DataFrame(
            {"Close": [100.0, 110.0, 121.0], "Signal": [1, 1, 0]},
            index=self.index[:3],
        )
        result = backtest(prices, 1000, 10)

        self.assertEqual(result["position"].tolist(), [0, 1, 1])
        self.assertAlmostEqual(result["equity"].iloc[1], 1099.0)
        self.assertAlmostEqual(result["equity"].iloc[2], 1208.9)

    def test_stale_and_future_market_data_are_reported(self) -> None:
        now = datetime(2025, 1, 10, tzinfo=timezone.utc)
        self.assertEqual(
            market_data_freshness(now - timedelta(hours=1), "60m", now), "Fresh"
        )
        self.assertEqual(
            market_data_freshness(now - timedelta(days=2), "1d", now), "Fresh"
        )
        self.assertEqual(
            market_data_freshness(now - timedelta(days=4), "1d", now), "Stale"
        )
        self.assertEqual(
            market_data_freshness(now + timedelta(minutes=1), "1d", now),
            "Invalid (future timestamp)",
        )

    def test_market_structure_and_atr_scenario_are_explained(self) -> None:
        close = [float(i) for i in range(1, 31)]
        close[-1] += 5
        analysis = analyze_prices(self.make_prices(close), 2, 5, 3, 3)
        self.assertEqual(analysis["Market_Structure"].iloc[-1], "Upside range breakout")

        long_scenario = build_setup_scenario(100, 2, 1)
        short_scenario = build_setup_scenario(100, 2, -1)
        self.assertIsNotNone(long_scenario)
        self.assertEqual(long_scenario["invalidation"], 97)
        self.assertEqual(long_scenario["target_1"], 103)
        self.assertEqual(long_scenario["target_2"], 106)
        self.assertEqual(short_scenario["invalidation"], 103)
        self.assertIsNone(build_setup_scenario(100, float("nan"), 1))

    def test_backtest_summary_includes_benchmark_and_drawdown(self) -> None:
        prices = pd.DataFrame(
            {"Close": [100.0, 110.0, 99.0], "Signal": [1, 1, 1]},
            index=self.index[:3],
        )
        simulation = backtest(prices, 1000, 0)
        summary = backtest_summary(simulation, 1000)
        self.assertAlmostEqual(summary["benchmark_return_pct"], -1.0)
        self.assertLess(summary["maximum_drawdown_pct"], 0)

    def test_energy_prices_can_be_analyzed_but_not_percent_backtested_through_zero(self) -> None:
        close = [-3.0, -2.0, -1.0, 0.0] + [float(i) for i in range(1, 31)]
        analysis = analyze_prices(self.make_prices(close), 2, 5, 3, 3)
        self.assertEqual(analysis["Signal"].iloc[-1], 1)
        with self.assertRaisesRegex(ValueError, "strictly positive"):
            backtest(analysis, 1000, 2)

    def test_empty_price_result_is_unavailable(self) -> None:
        from market_analysis import latest_bar_timestamp

        self.assertIsNone(latest_bar_timestamp(pd.DataFrame()))


class RegistryTests(unittest.TestCase):
    def test_project_registry_has_multiple_markets_and_provider_symbols(self) -> None:
        path = Path(__file__).parents[1] / "config" / "instruments.json"
        registry = load_instruments(path)

        self.assertTrue(any(item["instrument_type"] == "Forex major" for item in registry))
        self.assertTrue(any(item["instrument_type"] == "Metal" for item in registry))
        self.assertTrue(any(item["instrument_type"] == "Energy" for item in registry))
        self.assertTrue(any(item["instrument_type"] == "Stock index" for item in registry))
        self.assertTrue(
            any(item["instrument_type"] == "Cryptocurrency" for item in registry)
        )
        self.assertTrue(all(item["provider_symbol"] for item in registry))

    def test_registry_rejects_missing_fields_and_duplicates(self) -> None:
        invalid_registries = [
            [{"name": "Incomplete"}],
            [
                {
                    "name": "Same",
                    "instrument_type": "Forex major",
                    "provider": "yfinance",
                    "provider_symbol": "EURUSD=X",
                    "data_source": "Yahoo Finance",
                }
            ]
            * 2,
            [
                {
                    "name": "First",
                    "instrument_type": "Forex major",
                    "provider": "yfinance",
                    "provider_symbol": "EURUSD=X",
                    "data_source": "Yahoo Finance",
                },
                {
                    "name": "Second",
                    "instrument_type": "Forex minor",
                    "provider": "yfinance",
                    "provider_symbol": "EURUSD=X",
                    "data_source": "Yahoo Finance",
                },
            ],
        ]
        for registry in invalid_registries:
            with self.subTest(registry=registry), tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".json", delete=False
            ) as registry_file:
                json.dump(registry, registry_file)
                path = Path(registry_file.name)
            try:
                with self.assertRaises(ValueError):
                    load_instruments(path)
            finally:
                path.unlink()

    def test_candidate_instrument_addition_is_validated_and_atomic(self) -> None:
        project_registry = load_instruments(
            Path(__file__).parents[1] / "config" / "instruments.json"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "instruments.json"
            path.write_text(json.dumps(project_registry[:1]), encoding="utf-8")
            candidate = {
                "name": "Custom pair",
                "instrument_type": "Forex exotic",
                "provider": "yfinance",
                "provider_symbol": "USDCNH=X",
                "data_source": "Yahoo Finance",
            }
            added = add_instrument(path, candidate)
            self.assertEqual(len(added), 2)
            original = path.read_text(encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate provider symbol"):
                add_instrument(path, dict(candidate, name="Alias"))
            self.assertEqual(path.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
