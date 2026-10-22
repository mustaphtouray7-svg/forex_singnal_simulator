import argparse
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import signal as signal_module
import time
from typing import Any, Callable, Dict, Iterable, Optional

import pandas as pd

import storage
from instrument_registry import load_instruments
from market_analysis import analyze_prices, latest_analysis, market_data_freshness
from market_data import fetch_prices
from notifications import maybe_send_alert_notification
from settings import DEFAULT_SETTINGS_PATH, load_settings


LOGGER = logging.getLogger("forex_signal_monitor")
_stop_requested = False


def _request_period(settings: Dict[str, Any], interval: str) -> str:
    return str(settings["monitor_lookbacks"][interval])


def scan_once(
    settings: Dict[str, Any],
    instruments: Iterable[Dict[str, str]],
    database_path: Optional[str] = None,
    fetcher: Callable[[str, str, str, str], pd.DataFrame] = fetch_prices,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> Dict[str, int]:
    interval_seconds = 60.0 / float(settings["requests_per_minute"])
    last_request: Optional[float] = None
    counts = {"requests": 0, "records": 0, "alerts": 0, "failures": 0}
    by_symbol = {instrument["provider_symbol"]: instrument for instrument in instruments}
    watchlist = settings["watchlist"]

    for provider_symbol in watchlist:
        instrument = by_symbol.get(provider_symbol)
        if instrument is None:
            message = f"Configured watchlist symbol {provider_symbol} is not in the registry."
            LOGGER.error(message)
            storage.log_system_event(
                "ERROR", "monitor", message, provider_symbol, database_path
            )
            counts["failures"] += 1
            continue

        for interval in settings["monitor_timeframes"]:
            if last_request is not None:
                elapsed = monotonic_fn() - last_request
                if elapsed < interval_seconds:
                    sleep_fn(interval_seconds - elapsed)
            counts["requests"] += 1
            try:
                prices = fetcher(
                    instrument["provider"],
                    provider_symbol,
                    interval,
                    _request_period(settings, interval),
                )
                if prices.empty:
                    raise ValueError("Provider returned no price bars.")
                minimum_history = max(
                    int(settings["slow_ema"]),
                    int(settings["rsi_period"]),
                    int(settings["atr_period"]),
                ) + 5
                if len(prices) < minimum_history:
                    raise ValueError(
                        f"Provider returned {len(prices)} bars; "
                        f"{minimum_history} are required."
                    )
                analysis = analyze_prices(
                    prices,
                    int(settings["fast_ema"]),
                    int(settings["slow_ema"]),
                    int(settings["rsi_period"]),
                    int(settings["atr_period"]),
                )
                summary = latest_analysis(analysis)
                freshness = market_data_freshness(
                    pd.Timestamp(summary["timestamp"]), interval
                )
                if freshness != "Fresh":
                    summary["signal"] = 0
                    summary["scenario"] = None
                    storage.log_system_event(
                        "WARNING",
                        "monitor",
                        f"Skipped alert generation: latest bar is {freshness.lower()}.",
                        provider_symbol,
                        database_path,
                    )
                record = storage.record_analysis(
                    instrument, interval, summary, freshness, database_path
                )
                counts["records"] += 1
                if record["alert_created"]:
                    counts["alerts"] += 1
                    maybe_send_alert_notification(
                        record["alert_id"], database_path
                    )
                LOGGER.info(
                    "%s %s: %s (%s)",
                    provider_symbol,
                    interval,
                    summary["signal"],
                    freshness,
                )
            except Exception as exc:
                counts["failures"] += 1
                LOGGER.exception(
                    "Market scan failed for %s %s", provider_symbol, interval
                )
                storage.log_system_event(
                    "ERROR",
                    "monitor",
                    f"{type(exc).__name__}: {exc}",
                    provider_symbol,
                    database_path,
                )
            finally:
                last_request = monotonic_fn()
    storage.log_system_event(
        "INFO", "monitor", f"Scan completed: {counts}.", path=database_path
    )
    LOGGER.info("Scan completed: %s", counts)
    return counts


def configure_logging() -> None:
    log_directory = Path(__file__).resolve().parent / "logs"
    log_directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_directory / "monitor.log",
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[handler, logging.StreamHandler()],
    )


def run_monitor(
    settings_path: Path = DEFAULT_SETTINGS_PATH,
    once: bool = False,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    configure_logging()
    settings = load_settings(settings_path)
    registry = load_instruments(settings_path.parent / "instruments.json")
    instruments_by_symbol = {
        instrument["provider_symbol"]: instrument for instrument in registry
    }
    storage.init_db(initial_balance=float(settings["initial_paper_balance"]))
    storage.log_system_event(
        "INFO", "monitor", "Scheduled market monitor started."
    )
    LOGGER.info(
        "Monitoring %s symbols across %s timeframes every %s seconds.",
        len(settings["watchlist"]),
        len(settings["monitor_timeframes"]),
        settings["monitor_interval_seconds"],
    )
    while True:
        counts = scan_once(settings, instruments_by_symbol.values())
        LOGGER.info("Scan summary: %s", counts)
        if once:
            return
        sleep_fn(int(settings["monitor_interval_seconds"]))
        if _stop_requested:
            storage.log_system_event(
                "INFO", "monitor", "Scheduled market monitor stopped."
            )
            return


def _request_stop(_signum: int, _frame: Any) -> None:
    global _stop_requested
    _stop_requested = True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the Forex Signal Lab scheduled market scanner."
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single scan and exit instead of recurring.",
    )
    parser.add_argument(
        "--settings",
        type=Path,
        default=DEFAULT_SETTINGS_PATH,
        help="Path to the JSON settings file.",
    )
    arguments = parser.parse_args()
    signal_module.signal(signal_module.SIGINT, _request_stop)
    if hasattr(signal_module, "SIGTERM"):
        signal_module.signal(signal_module.SIGTERM, _request_stop)
    run_monitor(arguments.settings, arguments.once)


if __name__ == "__main__":
    main()
