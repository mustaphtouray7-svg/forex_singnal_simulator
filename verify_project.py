"""Read-only Forex Signal Lab verification.

Runs the existing unit tests, checks the seven requested FX symbols against
Yahoo Finance, exercises indicators/signals/backtesting/CSV export with
synthetic data, and optionally inspects the configured PostgreSQL schema.
Never prints database URLs, passwords, or access codes. Does not write trades.
"""
from __future__ import annotations

import csv
import importlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

PAIRS = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "JPY=X",
    "USD/CHF": "CHF=X",
    "USD/CAD": "CAD=X",
    "NZD/USD": "NZDUSD=X",
    "AUD/USD": "AUDUSD=X",
}
REQUIRED_TABLES = {
    "analysis_records",
    "alerts",
    "app_settings",
    "paper_account",
    "paper_trades",
    "system_events",
}
EXPECTED_FLOAT_COLUMNS = {
    "analysis_records": {"close", "rsi", "atr", "support", "resistance"},
    "paper_account": {"initial_balance", "balance"},
    "paper_trades": {
        "quantity", "entry_price", "stop_price", "target_1", "target_2",
        "entry_cost", "exit_price", "exit_cost", "gross_pnl", "net_pnl",
    },
}
results: list[tuple[str, str, str]] = []


def record(name: str, status: str, detail: str) -> None:
    results.append((name, status, detail.replace("\n", " ")[:240]))
    print(f"[{status}] {name}: {detail}")


def check_dependencies() -> None:
    required = ["pandas", "yfinance", "streamlit", "plotly", "psycopg"]
    missing = []
    for package in required:
        try:
            importlib.import_module(package)
        except Exception:
            missing.append(package)
    if missing:
        record("Dependencies", "FAIL", "Missing/unimportable: " + ", ".join(missing))
    else:
        record("Dependencies", "PASS", "All runtime packages import successfully.")


def check_unit_tests() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    tail = "\n".join(proc.stdout.splitlines()[-8:])
    record("Existing unit tests", "PASS" if proc.returncode == 0 else "FAIL",
           f"exit={proc.returncode}; {tail or 'No test output'}")


def check_market_logic_and_csv() -> None:
    try:
        from market_analysis import analyze_prices, backtest, backtest_summary, latest_analysis

        index = pd.date_range(end=pd.Timestamp.now(tz="UTC").normalize(), periods=100, freq="D")
        close = pd.Series([100.0 + i * 0.5 for i in range(100)], index=index)
        frame = pd.DataFrame({
            "Open": close, "High": close + 1, "Low": close - 1, "Close": close,
        })
        analyzed = analyze_prices(frame, 5, 20, 14, 14)
        summary = latest_analysis(analyzed)
        if summary["signal"] not in (-1, 0, 1):
            raise AssertionError("Signal is outside -1/0/1.")
        if pd.isna(analyzed["EMA_Fast"].iloc[-1]) or pd.isna(analyzed["RSI"].iloc[-1]):
            raise AssertionError("EMA/RSI returned missing latest values.")
        simulation = backtest(analyzed, 10_000, 2)
        metrics = backtest_summary(simulation, 10_000)
        if "maximum_drawdown_pct" not in metrics or simulation["equity"].isna().any():
            raise AssertionError("Backtest equity/drawdown output is incomplete.")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "backtest.csv"
            simulation.to_csv(target, index=True)
            with target.open("r", newline="", encoding="utf-8") as handle:
                header = next(csv.reader(handle))
            if not {"equity", "position", "strategy_return"}.issubset(set(header)):
                raise AssertionError("CSV export is missing required columns.")
        record("EMA / RSI / signal / backtest / drawdown / CSV", "PASS",
               f"Synthetic-data check passed; latest signal={summary['signal']}; drawdown={metrics['maximum_drawdown_pct']:.3f}%.")
    except Exception as exc:
        record("EMA / RSI / signal / backtest / drawdown / CSV", "FAIL",
               f"{type(exc).__name__}: {exc}")


def check_live_pairs() -> None:
    try:
        from market_data import fetch_prices
    except Exception as exc:
        record("Seven live FX pairs", "FAIL", f"Could not import market adapter: {exc}")
        return
    for name, symbol in PAIRS.items():
        try:
            prices = fetch_prices("yfinance", symbol, "1d", "1y")
            if prices.empty:
                record(name, "FAIL", f"{symbol}: provider returned no daily bars.")
                continue
            timestamp = pd.Timestamp(prices.index[-1])
            if timestamp.tzinfo is None:
                timestamp = timestamp.tz_localize("UTC")
            from market_analysis import analyze_prices, latest_analysis
            analysis = analyze_prices(prices, 5, 20, 14, 14)
            summary = latest_analysis(analysis)
            record(name, "PASS", f"{symbol}: {len(prices)} bars; latest bar {timestamp.isoformat()}; signal={summary['signal']}.")
        except Exception as exc:
            record(name, "FAIL", f"{symbol}: {type(exc).__name__}: {exc}")


def check_postgres_schema() -> None:
    database_url = os.environ.get("FOREX_SIGNAL_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not database_url:
        record("Neon/PostgreSQL schema", "SKIP",
               "No DATABASE_URL or FOREX_SIGNAL_DATABASE_URL set in this PowerShell session.")
        return
    try:
        import psycopg
        with psycopg.connect(database_url, connect_timeout=8, sslmode="require") as connection:
            rows = connection.execute(
                "SELECT table_name, column_name, data_type "
                "FROM information_schema.columns "
                "WHERE table_schema = current_schema()"
            ).fetchall()
        tables = {row[0] for row in rows}
        missing = sorted(REQUIRED_TABLES - tables)
        if missing:
            record("Neon/PostgreSQL schema", "FAIL", "Missing tables: " + ", ".join(missing))
            return
        columns = {(table, column): data_type for table, column, data_type in rows}
        bad = []
        for table, names in EXPECTED_FLOAT_COLUMNS.items():
            for column in names:
                actual = columns.get((table, column))
                if actual not in ("double precision", "real", "numeric"):
                    bad.append(f"{table}.{column}={actual or 'missing'}")
        if bad:
            record("Neon/PostgreSQL schema", "FAIL", "Unexpected/missing numeric columns: " + ", ".join(bad))
        else:
            record("Neon/PostgreSQL schema", "PASS",
                   f"All {len(REQUIRED_TABLES)} required tables and expected numeric columns exist. Credentials were not displayed.")
    except Exception as exc:
        record("Neon/PostgreSQL schema", "FAIL",
               f"{type(exc).__name__}: connection/schema check failed; secret values suppressed.")


def main() -> int:
    print("Forex Signal Lab — read-only verification")
    print("Live market requests use Yahoo Finance; availability may vary.")
    print("No broker connection, order, paper trade, or database write is performed.\n")
    check_dependencies()
    check_unit_tests()
    check_market_logic_and_csv()
    check_live_pairs()
    check_postgres_schema()
    print("\nRESULTS TABLE")
    print(f"{'CHECK':<39} {'STATUS':<7} DETAIL")
    print("-" * 110)
    for name, status, detail in results:
        print(f"{name:<39} {status:<7} {detail}")
    failed = sum(status == "FAIL" for _, status, _ in results)
    passed = sum(status == "PASS" for _, status, _ in results)
    skipped = sum(status == "SKIP" for _, status, _ in results)
    print(f"\nTOTAL: {passed} passed, {failed} failed, {skipped} skipped.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
