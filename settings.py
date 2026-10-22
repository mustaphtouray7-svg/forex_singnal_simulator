import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, Union


ROOT = Path(__file__).resolve().parent
DEFAULT_SETTINGS_PATH = ROOT / "config" / "settings.json"
REQUIRED_KEYS = {
    "initial_paper_balance",
    "estimated_transaction_cost_bps",
    "risk_per_trade_pct",
    "max_total_open_risk_pct",
    "max_open_positions",
    "fast_ema",
    "slow_ema",
    "rsi_period",
    "atr_period",
    "monitor_interval_seconds",
    "requests_per_minute",
    "monitor_timeframes",
    "monitor_lookbacks",
    "watchlist",
}


def load_settings(path: Union[str, Path] = DEFAULT_SETTINGS_PATH) -> Dict[str, Any]:
    with Path(path).open(encoding="utf-8") as settings_file:
        settings: Any = json.load(settings_file)
    if not isinstance(settings, dict):
        raise ValueError("Settings must be a JSON object.")
    missing = REQUIRED_KEYS - settings.keys()
    if missing:
        raise ValueError("Settings are missing: " + ", ".join(sorted(missing)))
    if (
        not isinstance(settings["fast_ema"], int)
        or isinstance(settings["fast_ema"], bool)
        or not isinstance(settings["slow_ema"], int)
        or isinstance(settings["slow_ema"], bool)
        or settings["fast_ema"] <= 0
        or settings["fast_ema"] >= settings["slow_ema"]
        or settings["fast_ema"] > 200
        or settings["slow_ema"] > 500
    ):
        raise ValueError("fast_ema must be positive and lower than slow_ema.")
    if (
        not isinstance(settings["rsi_period"], int)
        or isinstance(settings["rsi_period"], bool)
        or not isinstance(settings["atr_period"], int)
        or isinstance(settings["atr_period"], bool)
        or settings["rsi_period"] <= 0
        or settings["atr_period"] <= 0
        or settings["rsi_period"] > 100
        or settings["atr_period"] > 100
    ):
        raise ValueError("rsi_period and atr_period must be between 1 and 100.")
    for key in (
        "initial_paper_balance",
        "estimated_transaction_cost_bps",
        "risk_per_trade_pct",
        "max_total_open_risk_pct",
    ):
        value = settings[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError(f"{key} must be a finite number.")
    if settings["estimated_transaction_cost_bps"] < 0 or settings[
        "estimated_transaction_cost_bps"
    ] > 100:
        raise ValueError("estimated_transaction_cost_bps must be between 0 and 100.")
    if settings["initial_paper_balance"] <= 0:
        raise ValueError("initial_paper_balance must be positive.")
    if not 0 < settings["risk_per_trade_pct"] <= 5:
        raise ValueError("risk_per_trade_pct must be greater than 0 and at most 5.")
    if not 0 < settings["max_total_open_risk_pct"] <= 10:
        raise ValueError("max_total_open_risk_pct must be greater than 0 and at most 10.")
    if not settings["risk_per_trade_pct"] <= settings["max_total_open_risk_pct"] <= 10:
        raise ValueError(
            "max_total_open_risk_pct must cover per-trade risk and be at most 10."
        )
    if (
        not isinstance(settings["max_open_positions"], int)
        or isinstance(settings["max_open_positions"], bool)
        or not 1 <= settings["max_open_positions"] <= 20
    ):
        raise ValueError("max_open_positions must be between 1 and 20.")
    if (
        not isinstance(settings["monitor_interval_seconds"], int)
        or isinstance(settings["monitor_interval_seconds"], bool)
        or not 60 <= settings["monitor_interval_seconds"] <= 86_400
    ):
        raise ValueError("monitor_interval_seconds must be at least 60.")
    if (
        not isinstance(settings["requests_per_minute"], int)
        or isinstance(settings["requests_per_minute"], bool)
        or not 1 <= settings["requests_per_minute"] <= 60
    ):
        raise ValueError("requests_per_minute must be between 1 and 60.")
    if (
        not isinstance(settings["monitor_timeframes"], list)
        or not settings["monitor_timeframes"]
    ):
        raise ValueError("At least one monitor timeframe must be configured.")
    if not all(isinstance(interval, str) for interval in settings["monitor_timeframes"]):
        raise ValueError("monitor_timeframes must contain strings.")
    supported_timeframes = {"15m", "30m", "60m", "1d", "1wk"}
    unsupported_timeframes = set(settings["monitor_timeframes"]) - supported_timeframes
    if unsupported_timeframes:
        raise ValueError(
            "Unsupported monitor timeframe(s): "
            + ", ".join(sorted(unsupported_timeframes))
        )
    if not isinstance(settings["monitor_lookbacks"], dict):
        raise ValueError("monitor_lookbacks must be a timeframe-to-period JSON object.")
    if not all(
        isinstance(interval, str)
        and isinstance(period, str)
        and period.strip()
        for interval, period in settings["monitor_lookbacks"].items()
    ):
        raise ValueError("monitor lookback periods must be non-empty strings.")
    unknown_intervals = set(settings["monitor_timeframes"]) - set(settings["monitor_lookbacks"])
    if unknown_intervals:
        raise ValueError(
            "Missing monitor lookbacks for: " + ", ".join(sorted(unknown_intervals))
        )
    if not isinstance(settings["watchlist"], list):
        raise ValueError("watchlist must be a JSON list of provider symbols.")
    if not all(isinstance(symbol, str) and symbol.strip() for symbol in settings["watchlist"]):
        raise ValueError("watchlist entries must be non-empty provider symbols.")
    if len(set(settings["watchlist"])) != len(settings["watchlist"]):
        raise ValueError("watchlist symbols must be unique.")
    return settings


def save_settings(
    settings: Dict[str, Any], path: Union[str, Path] = DEFAULT_SETTINGS_PATH
) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(destination.parent),
            prefix=destination.name + ".",
            suffix=".tmp",
            delete=False,
        ) as settings_file:
            temporary_path = Path(settings_file.name)
            json.dump(settings, settings_file, indent=2)
            settings_file.write("\n")
        load_settings(temporary_path)
        os.replace(str(temporary_path), str(destination))
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
