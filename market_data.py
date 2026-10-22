from typing import Any, Dict

import pandas as pd
import yfinance as yf


DATA_SOURCE_BY_PROVIDER = {"yfinance": "Yahoo Finance"}


def fetch_prices(
    provider: str, provider_symbol: str, interval: str, period: str
) -> pd.DataFrame:
    """Fetch historical OHLCV data for one configured provider symbol."""
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
    return result.loc[~result.index.duplicated(keep="last")]


def provider_data_source(provider: str) -> str:
    try:
        return DATA_SOURCE_BY_PROVIDER[provider]
    except KeyError as exc:
        raise ValueError(f"Unsupported data provider configured: {provider}") from exc
