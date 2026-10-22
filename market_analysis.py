from datetime import datetime, timedelta
import math
from typing import Any, Dict, Optional, Union

import pandas as pd


FRESHNESS_LIMITS = {
    "15m": timedelta(hours=2),
    "30m": timedelta(hours=4),
    "60m": timedelta(hours=12),
    "1d": timedelta(days=3),
    "1wk": timedelta(days=14),
}
MARKET_STRUCTURE_LOOKBACK = 20


def _wilder_average(values: pd.Series, period: int) -> pd.Series:
    """Apply Wilder's SMA seed followed by recursive smoothing."""
    smoothed = pd.Series(float("nan"), index=values.index, dtype=float)
    valid_positions = [
        position for position, value in enumerate(values) if pd.notna(value)
    ]
    if len(valid_positions) < period:
        return smoothed

    start = valid_positions[0]
    seed_end = start + period
    seed = values.iloc[start:seed_end]
    if len(seed) != period or seed.isna().any():
        return smoothed
    smoothed.iloc[seed_end - 1] = seed.mean()

    for position in range(seed_end, len(values)):
        value = values.iloc[position]
        previous = smoothed.iloc[position - 1]
        if pd.notna(value) and pd.notna(previous):
            smoothed.iloc[position] = (
                previous * (period - 1) + value
            ) / period
    return smoothed


def analyze_prices(
    prices: pd.DataFrame,
    fast_period: int = 20,
    slow_period: int = 50,
    rsi_period: int = 14,
    atr_period: int = 14,
) -> pd.DataFrame:
    """Return a copy with indicators, rule outcomes, and a {-1, 0, 1} signal."""
    if fast_period <= 0 or slow_period <= 0 or rsi_period <= 0 or atr_period <= 0:
        raise ValueError("Indicator periods must be positive.")
    if fast_period >= slow_period:
        raise ValueError("Fast EMA period must be lower than Slow EMA period.")
    if "Close" not in prices.columns:
        raise ValueError("Price data must include a Close column.")
    if prices.empty:
        raise ValueError("Price data must not be empty.")

    result = prices.copy()
    close = pd.to_numeric(result["Close"], errors="coerce")
    if close.isna().any():
        raise ValueError("Close prices must contain only numeric, non-missing values.")
    result["EMA_Fast"] = close.ewm(span=fast_period, adjust=False).mean()
    result["EMA_Slow"] = close.ewm(span=slow_period, adjust=False).mean()

    change = close.diff()
    average_gain = _wilder_average(change.clip(lower=0), rsi_period)
    average_loss = _wilder_average(-change.clip(upper=0), rsi_period)
    relative_strength = average_gain / average_loss
    rsi = 100 - 100 / (1 + relative_strength)
    rsi = rsi.mask((average_loss == 0) & (average_gain > 0), 100)
    rsi = rsi.mask((average_gain == 0) & (average_loss > 0), 0)
    rsi = rsi.mask((average_gain == 0) & (average_loss == 0), 50)
    result["RSI"] = rsi

    if {"High", "Low"}.issubset(result.columns):
        high = pd.to_numeric(result["High"], errors="coerce")
        low = pd.to_numeric(result["Low"], errors="coerce")
        if high.isna().any() or low.isna().any():
            raise ValueError("High and Low prices must contain numeric, non-missing values.")
        if (high < low).any():
            raise ValueError("High prices must not be below Low prices.")
        previous_close = close.shift(1)
        true_range = pd.concat(
            [
                high - low,
                (high - previous_close).abs(),
                (low - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        result["ATR"] = _wilder_average(true_range, atr_period)
        result["Resistance"] = (
            high.rolling(MARKET_STRUCTURE_LOOKBACK, min_periods=MARKET_STRUCTURE_LOOKBACK)
            .max()
            .shift(1)
        )
        result["Support"] = (
            low.rolling(MARKET_STRUCTURE_LOOKBACK, min_periods=MARKET_STRUCTURE_LOOKBACK)
            .min()
            .shift(1)
        )
        result["Market_Structure"] = "Insufficient range history"
        range_ready = result["Resistance"].notna() & result["Support"].notna()
        result.loc[range_ready, "Market_Structure"] = "Inside prior range"
        result.loc[
            range_ready & (close > result["Resistance"]), "Market_Structure"
        ] = "Upside range breakout"
        result.loc[
            range_ready & (close < result["Support"]), "Market_Structure"
        ] = "Downside range breakout"
    else:
        result["ATR"] = float("nan")
        result["Resistance"] = float("nan")
        result["Support"] = float("nan")
        result["Market_Structure"] = "Unavailable (OHLC data required)"

    result["Bullish_EMA_Pass"] = result["EMA_Fast"] > result["EMA_Slow"]
    result["Bullish_RSI_Pass"] = result["RSI"] >= 50
    result["Bearish_EMA_Pass"] = result["EMA_Fast"] < result["EMA_Slow"]
    result["Bearish_RSI_Pass"] = result["RSI"] <= 50
    bullish = result["Bullish_EMA_Pass"] & result["Bullish_RSI_Pass"]
    bearish = result["Bearish_EMA_Pass"] & result["Bearish_RSI_Pass"]
    result["Signal"] = 0
    result.loc[bullish, "Signal"] = 1
    result.loc[bearish, "Signal"] = -1
    return result


def latest_analysis(analysis: pd.DataFrame) -> Dict[str, Any]:
    """Explain each rule for the latest bar and return its signal and values."""
    if analysis.empty:
        raise ValueError("Analysis data must not be empty.")
    latest = analysis.iloc[-1]
    if pd.isna(latest["RSI"]):
        raise ValueError("Insufficient price history to warm up the RSI.")
    atr = float(latest["ATR"]) if pd.notna(latest["ATR"]) else None
    close = float(latest["Close"])
    signal = int(latest["Signal"])
    return {
        "signal": signal,
        "close": close,
        "ema_fast": float(latest["EMA_Fast"]),
        "ema_slow": float(latest["EMA_Slow"]),
        "rsi": float(latest["RSI"]),
        "atr": atr,
        "timestamp": str(analysis.index[-1]),
        "market_structure": str(latest["Market_Structure"]),
        "support": float(latest["Support"]) if pd.notna(latest["Support"]) else None,
        "resistance": (
            float(latest["Resistance"]) if pd.notna(latest["Resistance"]) else None
        ),
        "indicators": {
            "ema_fast": float(latest["EMA_Fast"]),
            "ema_slow": float(latest["EMA_Slow"]),
            "rsi": float(latest["RSI"]),
            "atr": atr,
        },
        "scenario": build_setup_scenario(
            close, atr if atr is not None else float("nan"), signal
        ),
        "rules": [
            {
                "description": "Fast EMA is above slow EMA (bullish trend).",
                "passed": bool(latest["Bullish_EMA_Pass"]),
            },
            {
                "description": "RSI is at least 50 (bullish momentum).",
                "passed": bool(latest["Bullish_RSI_Pass"]),
            },
            {
                "description": "Fast EMA is below slow EMA (bearish trend).",
                "passed": bool(latest["Bearish_EMA_Pass"]),
            },
            {
                "description": "RSI is at most 50 (bearish momentum).",
                "passed": bool(latest["Bearish_RSI_Pass"]),
            },
        ],
    }


def build_setup_scenario(
    close: float,
    atr: float,
    signal: int,
    stop_atr_multiple: float = 1.5,
    entry_zone_atr_multiple: float = 0.25,
) -> Optional[Dict[str, float]]:
    """Build educational ATR scenarios; these are not executable order advice."""
    if signal not in (-1, 1) or not math.isfinite(close) or not math.isfinite(atr):
        return None
    if atr <= 0 or stop_atr_multiple <= 0 or entry_zone_atr_multiple < 0:
        return None

    direction = float(signal)
    stop_distance = atr * stop_atr_multiple
    entry_zone_distance = atr * entry_zone_atr_multiple
    return {
        "reference_entry": close,
        "entry_zone_low": close - entry_zone_distance,
        "entry_zone_high": close + entry_zone_distance,
        "invalidation": close - direction * stop_distance,
        "target_1": close + direction * stop_distance,
        "target_2": close + direction * stop_distance * 2,
        "risk_to_reward_1": 1.0,
        "risk_to_reward_2": 2.0,
        "risk_per_unit": stop_distance,
    }


def backtest(
    analysis: pd.DataFrame, starting_balance: float, cost_bps: float
) -> pd.DataFrame:
    """Simulate next-bar returns after each signal, with a simple turnover cost."""
    if analysis.empty:
        raise ValueError("Analysis data must not be empty.")
    if starting_balance <= 0:
        raise ValueError("Starting balance must be positive.")
    if cost_bps < 0:
        raise ValueError("Transaction cost cannot be negative.")
    if not {"Close", "Signal"}.issubset(analysis.columns):
        raise ValueError("Analysis data must include Close and Signal columns.")

    close = pd.to_numeric(analysis["Close"], errors="coerce")
    if close.isna().any():
        raise ValueError("Close prices must be numeric and non-missing.")
    if (close <= 0).any():
        raise ValueError(
            "Percentage-return backtesting requires strictly positive prices; "
            "this history crosses or reaches zero."
        )
    signal = pd.to_numeric(analysis["Signal"], errors="coerce")
    if signal.isna().any() or not signal.isin([-1, 0, 1]).all():
        raise ValueError("Signal values must be -1, 0, or 1.")
    position = signal.shift(1).fillna(0)
    position_change = position.diff().abs().fillna(0)
    market_return = close.pct_change().fillna(0)
    strategy_return = position * market_return - position_change * cost_bps / 10_000
    return pd.DataFrame(
        {
            "position": position,
            "market_return": market_return,
            "position_change": position_change,
            "strategy_return": strategy_return,
            "equity": starting_balance * (1 + strategy_return).cumprod(),
            "buy_hold_equity": starting_balance * (1 + market_return).cumprod(),
        },
        index=analysis.index,
    )


def backtest_summary(
    simulation: pd.DataFrame, starting_balance: float
) -> Dict[str, float]:
    if simulation.empty or starting_balance <= 0:
        raise ValueError("A non-empty simulation and positive balance are required.")
    equity = simulation["equity"]
    drawdown = equity / equity.cummax() - 1
    changes = simulation["position"].diff().abs().fillna(0)
    return {
        "strategy_return_pct": (equity.iloc[-1] / starting_balance - 1) * 100,
        "benchmark_return_pct": (
            simulation["buy_hold_equity"].iloc[-1] / starting_balance - 1
        )
        * 100,
        "maximum_drawdown_pct": drawdown.min() * 100,
        "bars_exposed": float((simulation["position"] != 0).sum()),
        "position_changes": float(changes.sum()),
    }


def latest_bar_timestamp(prices: pd.DataFrame) -> Optional[pd.Timestamp]:
    if prices.empty:
        return None
    return pd.Timestamp(prices.index[-1])


def market_data_freshness(
    timestamp: Union[datetime, pd.Timestamp],
    interval: str,
    now: Optional[Union[datetime, pd.Timestamp]] = None,
) -> str:
    """Classify a bar timestamp using conservative interval-specific limits."""
    if interval not in FRESHNESS_LIMITS:
        raise ValueError(f"Unsupported timeframe for freshness checks: {interval}")
    bar_time = pd.Timestamp(timestamp)
    current_time = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    if bar_time.tzinfo is None:
        bar_time = bar_time.tz_localize("UTC")
    else:
        bar_time = bar_time.tz_convert("UTC")
    if current_time.tzinfo is None:
        current_time = current_time.tz_localize("UTC")
    else:
        current_time = current_time.tz_convert("UTC")
    age = current_time - bar_time
    if age < pd.Timedelta(0):
        return "Invalid (future timestamp)"
    return "Fresh" if age <= FRESHNESS_LIMITS[interval] else "Stale"
