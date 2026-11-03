from typing import Any, Dict

import pandas as pd
import yfinance as yf


DATA_SOURCE_BY_PROVIDER = {"yfinance": "Yahoo Finance"}

# Yahoo Finance quotes these USD-base pairs as the reciprocal (foreign currency
# per USD?); the =X symbols below are USD per one unit of the named currency.
# Convert the full OHLC candle back to the instrument's displayed base/quote
# convention before indicators or returns are calculated.
INVERT_YAHOO_SYMBOLS = {"JPY=X", "CHF=X", "CAD=X"}


def fetch_prices(
    provider: str, provider_symbol: str, interval: str, period: str
) -> pd.DataFrame:
    """Fetch historical OHLCV data and normalize inverse Yahoo FX quotes."""
    if provider not in DATA_SOURCE_BY_PROVIDER:
        raise ValueError(f"Unsupported data provider configured: {provider}")
    if not provider_symbol.strip():
        raise ValueError("Provider symbol must not be empty.")

    downloaded: Any = yf.download(
        provider_symbol,
        period=period,
        interval=interval,
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    if downloaded is None or downloaded.empty:
        return pd.DataFrame()
    if isinstance(downloaded.columns, pd.MultiIndex):
        downloaded.columns = downloaded.columns.get_level_values(0)
    downloaded.columns = [str(column).title() for column in downloaded.columns]
    columns = [
        column
        for column in ("Open", "High", "Low", "Close", "Volume")
        if column in downloaded.columns
    ]
    if "Close" not in columns:
        return pd.DataFrame()
    result = downloaded[columns].copy()
    result = result.dropna(subset=["Close"])
    result = result.sort_index()
    result = result.loc[~result.index.duplicated(keep="last")]

    if provider_symbol in INVERT_YAHOO_SYMBOLS:
        # Inverting a candle swaps the extrema: 1/old Low is the new High.
        for column in ("Open", "Close"):
            if column in result:
                result[column] = 1.0 / result[column].where(result[column] != 0)
        if "High" in result and "Low" in result:
            old_high = result["High"].copy()
            old_low = result["Low"].copy()
            result["High"] = 1.0 / old_low.where(old_low != 0)
            result["Low"] = 1.0 / old_high.where(old_high != 0)

    return result


def provider_data_source(provider: str) -> str:
    try:
        return DATA_SOURCE_BY_PROVIDER[provider]
    except KeyError as exc:
        raise ValueError(f"Unsupported data provider configured: {provider}") from exc
