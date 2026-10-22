import pandas as pd
from typing import Any, Dict

from market_analysis import (
    analyze_prices,
    latest_analysis,
    latest_bar_timestamp,
    market_data_freshness,
)


def revalidate_alert(
    alert: Dict[str, Any],
    prices: pd.DataFrame,
    fast_period: int,
    slow_period: int,
    rsi_period: int,
    atr_period: int,
) -> Dict[str, Any]:
    """Require a fresh provider bar and the same directional rules before paper entry."""
    if prices.empty:
        raise ValueError("The provider returned no fresh price bars.")
    analysis = analyze_prices(
        prices, fast_period, slow_period, rsi_period, atr_period
    )
    summary = latest_analysis(analysis)
    timestamp = latest_bar_timestamp(prices)
    freshness = market_data_freshness(timestamp, alert["timeframe"])
    if freshness != "Fresh":
        raise ValueError(
            f"Paper trade blocked: refreshed provider data is {freshness.lower()}."
        )
    if summary["signal"] != int(alert["signal"]):
        raise ValueError(
            "Paper trade blocked: the fresh rule result no longer matches this alert."
        )
    if summary["signal"] == 0 or summary["scenario"] is None:
        raise ValueError("Paper trade blocked: no valid fresh risk scenario is available.")
    summary["freshness"] = freshness
    return summary
