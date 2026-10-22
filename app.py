import logging
import os
from pathlib import Path
from typing import Any, Dict

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import storage
from instrument_registry import add_instrument, load_instruments
from market_analysis import (
    analyze_prices,
    backtest,
    backtest_summary,
    latest_analysis,
    latest_bar_timestamp,
    market_data_freshness,
)
from market_data import fetch_prices, provider_data_source
from notifications import maybe_send_alert_notification
from paper_trading import revalidate_alert
from settings import DEFAULT_SETTINGS_PATH, load_settings, save_settings


logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("forex_signal_lab")
ROOT = Path(__file__).resolve().parent
TIMEFRAMES = {
    "15m": ("15-minute", ["5d", "1mo"]),
    "30m": ("30-minute", ["5d", "1mo"]),
    "60m": ("Hourly", ["5d", "1mo", "3mo"]),
    "1d": ("Daily", ["6mo", "1y", "2y", "5y", "max"]),
    "1wk": ("Weekly", ["2y", "5y", "10y", "max"]),
}

st.set_page_config(page_title="Forex Signal Lab", page_icon="📊", layout="wide")
st.title("Forex Signal Lab")
st.caption(
    "Multi-market analysis and simulated trading only. No broker connection "
    "or real-money order execution."
)

try:
    instruments = load_instruments(ROOT / "config" / "instruments.json")
    settings = load_settings(DEFAULT_SETTINGS_PATH)
    storage.init_db(initial_balance=float(settings["initial_paper_balance"]))
except (OSError, ValueError) as exc:
    LOGGER.exception("Could not initialize the application")
    st.error(f"Configuration or local database initialization failed: {exc}")
    st.stop()

instruments_by_symbol = {
    instrument["provider_symbol"]: instrument for instrument in instruments
}
instrument_names = {
    instrument["provider_symbol"]: instrument["name"] for instrument in instruments
}


@st.cache_data(ttl=900, show_spinner=False)
def cached_prices(
    provider: str, symbol: str, timeframe: str, period: str
) -> pd.DataFrame:
    return fetch_prices(provider, symbol, timeframe, period)


with st.sidebar:
    st.header("Market selection")
    selected_name = st.selectbox("Instrument", [item["name"] for item in instruments])
    selected_instrument = next(
        item for item in instruments if item["name"] == selected_name
    )
    timeframe = st.selectbox("Timeframe", list(TIMEFRAMES), index=3)
    timeframe_label, supported_periods = TIMEFRAMES[timeframe]
    period = st.selectbox(
        "Historical lookback", supported_periods, index=supported_periods.index(
            "2y" if timeframe == "1d" else "1mo"
            if timeframe in ("15m", "30m", "60m")
            else "5y"
        )
    )
    st.caption(
        f"Configured indicators: EMA {settings['fast_ema']}/{settings['slow_ema']}, "
        f"RSI {settings['rsi_period']}, ATR {settings['atr_period']}."
    )

fast_period = int(settings["fast_ema"])
slow_period = int(settings["slow_ema"])
rsi_period = int(settings["rsi_period"])
atr_period = int(settings["atr_period"])
minimum_history = max(slow_period, rsi_period, atr_period) + 5

data_error = None
try:
    with st.spinner(
        f"Loading {selected_instrument['data_source']} data for "
        f"{selected_instrument['provider_symbol']}..."
    ):
        prices = cached_prices(
            selected_instrument["provider"],
            selected_instrument["provider_symbol"],
            timeframe,
            period,
        )
except Exception as exc:
    LOGGER.exception(
        "Price request failed for %s",
        selected_instrument["provider_symbol"],
    )
    storage.log_system_event(
        "ERROR",
        "dashboard",
        f"{type(exc).__name__}: {exc}",
        selected_instrument["provider_symbol"],
    )
    data_error = (
        f"Provider request failed for {selected_instrument['provider_symbol']} "
        f"({selected_instrument['data_source']}): {exc}"
    )
    prices = pd.DataFrame()

bar_timestamp = latest_bar_timestamp(prices) if not prices.empty else None
freshness = (
    market_data_freshness(bar_timestamp, timeframe)
    if bar_timestamp is not None
    else "Unavailable"
)
metadata_columns = st.columns(5)
metadata_columns[0].metric("Instrument type", selected_instrument["instrument_type"])
metadata_columns[1].metric("Provider symbol", selected_instrument["provider_symbol"])
metadata_columns[2].metric("Data source", selected_instrument["data_source"])
metadata_columns[3].metric(
    "Latest provider bar", str(bar_timestamp) if bar_timestamp is not None else "—"
)
metadata_columns[4].metric(
    "Availability / freshness",
    ("Available" if bar_timestamp is not None else "Unavailable") + " / " + freshness,
)

analysis_frame = pd.DataFrame()
summary = None
if data_error:
    st.error(data_error)
elif prices.empty:
    st.error(
        f"No price bars are available for {selected_instrument['provider_symbol']} "
        f"at {timeframe}. The provider may not support this symbol/timeframe."
    )
elif len(prices) < minimum_history:
    st.error(
        f"Only {len(prices)} bars were returned; at least {minimum_history} are "
        "needed for the selected indicators."
    )
else:
    try:
        analysis_frame = analyze_prices(
            prices,
            fast_period,
            slow_period,
            rsi_period,
            atr_period,
        )
        summary = latest_analysis(analysis_frame)
    except ValueError as exc:
        LOGGER.exception("Analysis failed for %s", selected_name)
        data_error = f"Unable to analyze this provider response: {exc}"
        st.error(data_error)

effective_signal = (
    summary["signal"] if summary is not None and freshness == "Fresh" else 0
)
analysis_available = summary is not None
if summary is not None:
    recorded_summary = dict(summary)
    recorded_summary["signal"] = effective_signal
    if effective_signal == 0:
        recorded_summary["scenario"] = None
    try:
        record_result = storage.record_analysis(
            selected_instrument, timeframe, recorded_summary, freshness
        )
        if record_result["alert_created"]:
            maybe_send_alert_notification(record_result["alert_id"])
    except Exception as exc:
        LOGGER.exception("Could not persist analysis")
        st.error(f"Analysis is visible but could not be saved locally: {exc}")
else:
    analysis_frame = pd.DataFrame(
        columns=[
            "Open",
            "High",
            "Low",
            "Close",
            "EMA_Fast",
            "EMA_Slow",
            "RSI",
            "ATR",
            "Support",
            "Resistance",
            "Market_Structure",
            "Signal",
        ]
    )
    summary = {
        "signal": 0,
        "close": float("nan"),
        "rsi": float("nan"),
        "atr": float("nan"),
        "timestamp": "Unavailable",
        "market_structure": "Unavailable",
        "support": None,
        "resistance": None,
        "scenario": None,
        "rules": [],
    }

if summary is not None and freshness != "Fresh":
    st.warning(
        f"Provider data is {freshness.lower()}; the displayed rule result is "
        "historical only and no actionable alert or paper trade is enabled."
    )

result_label = {
    1: "Bullish setup",
    -1: "Bearish setup",
    0: "No trade — unclear conditions",
}[effective_signal]

overview, markets, alerts_tab, paper_tab, backtest_tab, settings_tab = st.tabs(
    ["Overview", "Markets & analysis", "Alerts & approvals", "Paper trading", "Backtest", "Settings & system"]
)

with overview:
    if not analysis_available:
        st.info("Market analysis is unavailable for this selection. Other pages remain usable.")
    else:
        headline = st.columns(4)
        headline[0].metric("Current rule result", result_label)
        headline[1].metric("Latest recorded close", f"{summary['close']:.5f}")
        atr_label = f"{summary['atr']:.5f}" if summary["atr"] is not None else "N/A"
        headline[2].metric("RSI / ATR", f"{summary['rsi']:.1f} / {atr_label}")
        headline[3].metric("Market structure", summary["market_structure"])

    account = storage.get_paper_account(
        initial_balance=float(settings["initial_paper_balance"])
    )
    active_alerts = storage.get_alerts()
    open_positions = storage.get_paper_trades(status="open")
    summary_columns = st.columns(3)
    summary_columns[0].metric("Paper balance", f"${account['balance']:,.2f}")
    summary_columns[1].metric("Open paper positions", len(open_positions))
    summary_columns[2].metric(
        "Pending approvals",
        sum(alert["status"] == "pending" for alert in active_alerts),
    )

    st.subheader("Configured watchlist")
    history = storage.get_analysis_history(limit=500)
    latest_by_symbol: Dict[str, Dict[str, Any]] = {}
    for row in history:
        if row["provider_symbol"] in settings["watchlist"]:
            latest_by_symbol.setdefault(row["provider_symbol"], row)
    watchlist_rows = []
    for symbol in settings["watchlist"]:
        instrument = instruments_by_symbol.get(symbol)
        latest_row = latest_by_symbol.get(symbol)
        watchlist_rows.append(
            {
                "Instrument": instrument["name"] if instrument else "Unconfigured symbol",
                "Provider symbol": symbol,
                "Timeframe": latest_row["timeframe"] if latest_row else "—",
                "Signal": (
                    {1: "Bullish", -1: "Bearish", 0: "No trade"}[latest_row["signal"]]
                    if latest_row
                    else "Not scanned yet"
                ),
                "Last bar": latest_row["bar_timestamp"] if latest_row else "—",
                "Freshness": latest_row["freshness"] if latest_row else "—",
            }
        )
    st.dataframe(pd.DataFrame(watchlist_rows), hide_index=True, use_container_width=True)
    st.caption(
        "Watchlist timestamps come from saved analyses. Rows may be historical "
        "until the monitor runs or each market is opened in the dashboard."
    )
    st.info(
        "Educational only: rule-based setups and simulated results are not "
        "recommendations and do not predict or guarantee future performance."
    )

with markets:
    if not analysis_available:
        st.info("No usable provider data is available to draw or analyze for this selection.")
    left, right = st.columns([3, 1])
    with left:
        chart = go.Figure()
        if {"Open", "High", "Low", "Close"}.issubset(analysis_frame.columns):
            chart.add_trace(
                go.Candlestick(
                    x=analysis_frame.index,
                    open=analysis_frame["Open"],
                    high=analysis_frame["High"],
                    low=analysis_frame["Low"],
                    close=analysis_frame["Close"],
                    name="Candlesticks",
                )
            )
        else:
            chart.add_trace(
                go.Scatter(
                    x=analysis_frame.index,
                    y=analysis_frame["Close"],
                    name="Close",
                    mode="lines",
                )
            )
        chart.add_trace(
            go.Scatter(
                x=analysis_frame.index,
                y=analysis_frame["EMA_Fast"],
                name=f"EMA {fast_period}",
            )
        )
        chart.add_trace(
            go.Scatter(
                x=analysis_frame.index,
                y=analysis_frame["EMA_Slow"],
                name=f"EMA {slow_period}",
            )
        )
        chart.add_trace(
            go.Scatter(
                x=analysis_frame.index,
                y=analysis_frame["Resistance"],
                name="Prior range resistance",
                line={"dash": "dot", "color": "#ef553b"},
            )
        )
        chart.add_trace(
            go.Scatter(
                x=analysis_frame.index,
                y=analysis_frame["Support"],
                name="Prior range support",
                line={"dash": "dot", "color": "#00cc96"},
            )
        )
        chart.update_layout(
            height=500,
            xaxis_title="Provider timestamp",
            yaxis_title="Price",
            xaxis_rangeslider_visible=False,
        )
        st.plotly_chart(chart, use_container_width=True)

        rsi_chart = go.Figure()
        rsi_chart.add_trace(
            go.Scatter(x=analysis_frame.index, y=analysis_frame["RSI"], name="RSI")
        )
        rsi_chart.add_hline(y=50, line_dash="dash", line_color="gray")
        rsi_chart.update_layout(height=220, yaxis_range=[0, 100], yaxis_title="RSI")
        st.plotly_chart(rsi_chart, use_container_width=True)

    with right:
        st.subheader("Latest analysis")
        st.write(f"**Result:** {result_label}")
        st.write(f"**Structure:** {summary['market_structure']}")
        st.write(f"**Support:** {summary['support'] or 'Not established'}")
        st.write(f"**Resistance:** {summary['resistance'] or 'Not established'}")
        st.write(f"**Timestamp:** {summary['timestamp']}")
        st.markdown("**Rule checks**")
        for rule in summary["rules"]:
            st.write(f"{'PASS' if rule['passed'] else 'FAIL'} · {rule['description']}")
        scenario = summary["scenario"] if freshness == "Fresh" else None
        if scenario:
            st.markdown("**Illustrative ATR scenario (not an order)**")
            st.write(
                f"Entry area: {scenario['entry_zone_low']:.5f} – "
                f"{scenario['entry_zone_high']:.5f}"
            )
            st.write(f"Reference entry: {scenario['reference_entry']:.5f}")
            st.write(f"Invalidation: {scenario['invalidation']:.5f}")
            st.write(
                f"Targets: {scenario['target_1']:.5f} (1R), "
                f"{scenario['target_2']:.5f} (2R)"
            )
            account_balance = float(account["balance"])
            example_risk = account_balance * float(settings["risk_per_trade_pct"]) / 100
            example_units = example_risk / scenario["risk_per_unit"]
            st.write(
                f"Illustrative size at configured risk: {example_units:.4f} "
                "price-units"
            )
            st.caption(
                "Scenario levels use the current close, 0.25 ATR entry band, and "
                "1.5 ATR invalidation; sizing divides simulated balance risk by "
                "price distance. Contract multipliers, margin, pip values, and "
                "broker execution are not modeled."
            )
        else:
            st.caption("No actionable ATR scenario is available for this result.")

    recent = analysis_frame[
        [
            "Close",
            "EMA_Fast",
            "EMA_Slow",
            "RSI",
            "ATR",
            "Support",
            "Resistance",
            "Market_Structure",
            "Signal",
        ]
    ].tail(30).copy()
    recent["Signal"] = recent["Signal"].map(
        {1: "Bullish", -1: "Bearish", 0: "No trade"}
    )
    st.dataframe(recent.sort_index(ascending=False), use_container_width=True)

with alerts_tab:
    st.subheader("Persisted signal alerts")
    alerts = storage.get_alerts(limit=200)
    if not alerts:
        st.info("No actionable signal alerts are stored yet.")
    else:
        alert_labels = {
            alert["alert_id"]: (
                f"#{alert['alert_id']} · {alert['instrument_name']} "
                f"{alert['timeframe']} · "
                f"{'Bullish' if alert['signal'] == 1 else 'Bearish'} · "
                f"{alert['bar_timestamp']} · {alert['status']}"
            )
            for alert in alerts
        }
        chosen_alert_id = st.selectbox(
            "Open an alert",
            [alert["alert_id"] for alert in alerts],
            format_func=lambda alert_id: alert_labels[alert_id],
        )
        chosen_alert = next(
            alert for alert in alerts if alert["alert_id"] == chosen_alert_id
        )
        details, actions = st.columns([2, 1])
        with details:
            st.write(f"**Instrument:** {chosen_alert['instrument_name']}")
            st.write(f"**Provider:** {chosen_alert['data_source']} · {chosen_alert['provider_symbol']}")
            st.write(f"**Timeframe:** {chosen_alert['timeframe']}")
            st.write(f"**Generated:** {chosen_alert['created_at']}")
            st.write(f"**Data bar / freshness at creation:** {chosen_alert['bar_timestamp']} · {chosen_alert['freshness']}")
            st.write(
                f"**Signal / close / RSI / ATR:** "
                f"{'Bullish' if chosen_alert['signal'] == 1 else 'Bearish'} · "
                f"{chosen_alert['close']:.5f} · {chosen_alert['rsi']:.2f} · "
                f"{chosen_alert['atr'] if chosen_alert['atr'] is not None else 'N/A'}"
            )
            st.write(f"**Market structure:** {chosen_alert['market_structure']}")
            st.write(
                f"**EMA fast / slow:** "
                f"{chosen_alert['indicators'].get('ema_fast', 'N/A')} / "
                f"{chosen_alert['indicators'].get('ema_slow', 'N/A')}"
            )
            st.markdown("**Rule-by-rule explanation**")
            for rule in chosen_alert["rules"]:
                st.write(f"{'PASS' if rule['passed'] else 'FAIL'} · {rule['description']}")
            if chosen_alert["scenario"]:
                st.json(chosen_alert["scenario"])
        with actions:
            st.write(f"**Status:** {chosen_alert['status']}")
            if st.button(
                "Approve analysis",
                key=f"approve-{chosen_alert_id}",
                disabled=chosen_alert["status"] != "pending",
            ):
                storage.update_alert_status(chosen_alert_id, "approved")
                st.rerun()
            if st.button(
                "Reject alert",
                key=f"reject-{chosen_alert_id}",
                disabled=chosen_alert["status"] not in ("pending", "approved"),
            ):
                storage.update_alert_status(chosen_alert_id, "rejected")
                st.rerun()

        can_trade = chosen_alert["status"] in ("pending", "approved")
        with st.form(f"paper-trade-{chosen_alert_id}"):
            confirmed_trade = st.checkbox(
                "I understand this creates a simulated position only and confirm this paper trade.",
                disabled=not can_trade,
            )
            submit_trade = st.form_submit_button(
                "Recheck data and confirm paper trade",
                disabled=not can_trade,
                type="primary",
            )
        if submit_trade:
            if not confirmed_trade:
                st.error("Check the confirmation box before creating a simulated position.")
            else:
                try:
                    fresh_prices = fetch_prices(
                        chosen_alert["provider"],
                        chosen_alert["provider_symbol"],
                        chosen_alert["timeframe"],
                        TIMEFRAMES[chosen_alert["timeframe"]][1][-1],
                    )
                    try:
                        fresh_summary = revalidate_alert(
                            chosen_alert,
                            fresh_prices,
                            fast_period,
                            slow_period,
                            rsi_period,
                            atr_period,
                        )
                    except ValueError as exc:
                        if "Paper trade blocked:" in str(exc):
                            storage.update_alert_status(chosen_alert_id, "stale")
                        raise
                    trade = storage.open_paper_trade(
                        chosen_alert_id,
                        fresh_summary["signal"],
                        fresh_summary["close"],
                        fresh_summary["scenario"],
                        float(settings["risk_per_trade_pct"]),
                        float(settings["max_total_open_risk_pct"]),
                        int(settings["max_open_positions"]),
                        float(settings["estimated_transaction_cost_bps"]),
                    )
                    st.success(
                        f"Paper position #{trade['trade_id']} opened with "
                        f"{trade['quantity']:.4f} simulated units. "
                        f"Estimated risk: ${trade['risk_amount']:.2f}."
                    )
                    st.rerun()
                except Exception as exc:
                    LOGGER.exception("Paper trade action failed for alert %s", chosen_alert_id)
                    storage.log_system_event(
                        "WARNING",
                        "paper-trading",
                        f"Alert {chosen_alert_id}: {type(exc).__name__}: {exc}",
                        chosen_alert["provider_symbol"],
                    )
                    st.error(str(exc))

    st.subheader("Recent analysis history, including no-trade")
    history = storage.get_analysis_history(limit=100)
    if history:
        history_frame = pd.DataFrame(history)
        history_frame["signal"] = history_frame["signal"].map(
            {1: "Bullish", -1: "Bearish", 0: "No trade"}
        )
        st.dataframe(
            history_frame[
                [
                    "instrument_name",
                    "provider_symbol",
                    "timeframe",
                    "bar_timestamp",
                    "signal",
                    "freshness",
                    "close",
                ]
            ],
            hide_index=True,
            use_container_width=True,
        )

with paper_tab:
    st.subheader("Simulated account")
    account = storage.get_paper_account(
        initial_balance=float(settings["initial_paper_balance"])
    )
    account_metrics = st.columns(3)
    account_metrics[0].metric("Virtual cash balance", f"${account['balance']:,.2f}")
    account_metrics[1].metric("Initial virtual balance", f"${account['initial_balance']:,.2f}")
    account_metrics[2].metric(
        "Per-trade risk cap", f"{settings['risk_per_trade_pct']:.2f}%"
    )
    st.caption(
        "Virtual balance is not connected to a financial account. Quantity is "
        "expressed in unleveraged price-units; contract size, margin, pip value, "
        "funding, and broker execution are intentionally not modeled."
    )

    open_trades = storage.get_paper_trades(status="open")
    st.markdown("### Open simulated positions")
    if not open_trades:
        st.info("No paper positions are open.")
    else:
        if st.button("Refresh estimated open-position P/L"):
            marks = []
            for trade in open_trades:
                instrument = instruments_by_symbol.get(trade["provider_symbol"])
                if instrument is None:
                    marks.append(
                        {
                            "Position": trade["id"],
                            "Instrument": trade["instrument_name"],
                            "Mark": "Unavailable (not in registry)",
                            "Estimated net P/L": "N/A",
                        }
                    )
                    continue
                try:
                    mark_prices = cached_prices(
                        instrument["provider"],
                        trade["provider_symbol"],
                        trade["timeframe"],
                        TIMEFRAMES[trade["timeframe"]][1][-1],
                    )
                    if mark_prices.empty:
                        raise ValueError("Provider returned no bars.")
                    mark_time = latest_bar_timestamp(mark_prices)
                    mark_freshness = market_data_freshness(
                        mark_time, trade["timeframe"]
                    )
                    if mark_freshness != "Fresh":
                        marks.append(
                            {
                                "Position": trade["id"],
                                "Instrument": trade["instrument_name"],
                                "Mark": mark_freshness,
                                "Estimated net P/L": "N/A",
                            }
                        )
                        continue
                    marked = storage.mark_paper_trade(
                        trade,
                        float(mark_prices["Close"].iloc[-1]),
                        float(settings["estimated_transaction_cost_bps"]),
                    )
                    marks.append(
                        {
                            "Position": trade["id"],
                            "Instrument": trade["instrument_name"],
                            "Mark": f"{marked['market_price']:.5f}",
                            "Estimated net P/L": (
                                f"${marked['estimated_net_unrealized_pnl']:,.2f}"
                            ),
                        }
                    )
                except Exception as exc:
                    LOGGER.exception("Could not mark paper position %s", trade["id"])
                    storage.log_system_event(
                        "WARNING",
                        "paper-trading",
                        f"Position {trade['id']} mark failed: {type(exc).__name__}: {exc}",
                        trade["provider_symbol"],
                    )
                    marks.append(
                        {
                            "Position": trade["id"],
                            "Instrument": trade["instrument_name"],
                            "Mark": "Unavailable",
                            "Estimated net P/L": "N/A",
                        }
                    )
            st.dataframe(pd.DataFrame(marks), hide_index=True, use_container_width=True)
        st.dataframe(
            pd.DataFrame(open_trades)[
                [
                    "id",
                    "instrument_name",
                    "provider_symbol",
                    "timeframe",
                    "signal",
                    "quantity",
                    "entry_price",
                    "stop_price",
                    "target_1",
                    "target_2",
                    "opened_at",
                ]
            ],
            hide_index=True,
            use_container_width=True,
        )
        selected_trade_id = st.selectbox(
            "Position to close",
            [trade["id"] for trade in open_trades],
            format_func=lambda trade_id: next(
                f"#{trade_id} · {trade['instrument_name']} · "
                f"{'Long' if trade['signal'] == 1 else 'Short'}"
                for trade in open_trades
                if trade["id"] == trade_id
            ),
        )
        selected_trade = next(
            trade for trade in open_trades if trade["id"] == selected_trade_id
        )
        with st.form(f"paper-close-{selected_trade_id}"):
            close_confirmed = st.checkbox(
                "I confirm closing this simulated position at the refreshed provider price."
            )
            close_position = st.form_submit_button(
                "Refresh data and confirm paper close", type="secondary"
            )
        if close_position:
            if not close_confirmed:
                st.error("Check the confirmation box to close the paper position.")
            else:
                try:
                    close_instrument = instruments_by_symbol[
                        selected_trade["provider_symbol"]
                    ]
                    close_prices = fetch_prices(
                        close_instrument["provider"],
                        selected_trade["provider_symbol"],
                        selected_trade["timeframe"],
                        TIMEFRAMES[selected_trade["timeframe"]][1][-1],
                    )
                    if close_prices.empty:
                        raise ValueError("The provider returned no fresh price bars.")
                    close_timestamp = latest_bar_timestamp(close_prices)
                    close_freshness = market_data_freshness(
                        close_timestamp, selected_trade["timeframe"]
                    )
                    if close_freshness != "Fresh":
                        raise ValueError(
                            f"Paper close blocked: refreshed data is {close_freshness.lower()}."
                        )
                    close_result = storage.close_paper_trade(
                        selected_trade_id,
                        float(close_prices["Close"].iloc[-1]),
                        float(settings["estimated_transaction_cost_bps"]),
                    )
                    st.success(
                        f"Paper position closed. Net simulated P/L: "
                        f"${close_result['net_pnl']:,.2f}."
                    )
                    st.rerun()
                except Exception as exc:
                    LOGGER.exception("Paper close failed for position %s", selected_trade_id)
                    storage.log_system_event(
                        "WARNING",
                        "paper-trading",
                        f"Position {selected_trade_id}: {type(exc).__name__}: {exc}",
                        selected_trade["provider_symbol"],
                    )
                    st.error(str(exc))

    st.markdown("### Trade history")
    all_trades = storage.get_paper_trades(limit=200)
    if all_trades:
        trade_frame = pd.DataFrame(all_trades)
        trade_frame["side"] = trade_frame["signal"].map({1: "Long", -1: "Short"})
        st.dataframe(
            trade_frame[
                [
                    "id",
                    "instrument_name",
                    "side",
                    "quantity",
                    "entry_price",
                    "exit_price",
                    "status",
                    "gross_pnl",
                    "net_pnl",
                    "opened_at",
                    "closed_at",
                ]
            ],
            hide_index=True,
            use_container_width=True,
        )

with backtest_tab:
    st.subheader("Historical rule-based simulation")
    if not analysis_available:
        st.info("Backtesting is unavailable until sufficient provider price history is returned.")
    else:
        simulation_cost = st.number_input(
            "Estimated transaction cost per position change (basis points)",
            min_value=0.0,
            max_value=100.0,
            value=float(settings["estimated_transaction_cost_bps"]),
            step=0.5,
        )
        initial_balance = float(settings["initial_paper_balance"])
        try:
            simulation = backtest(analysis_frame, initial_balance, simulation_cost)
        except ValueError as exc:
            st.warning(f"Backtest unavailable for this price history: {exc}")
            simulation = pd.DataFrame()
        if not simulation.empty:
            simulation_stats = backtest_summary(simulation, initial_balance)
        else:
            simulation_stats = None
        if simulation_stats is not None:
            backtest_metrics = st.columns(4)
            backtest_metrics[0].metric(
                "Strategy return", f"{simulation_stats['strategy_return_pct']:.2f}%"
            )
            backtest_metrics[1].metric(
                "Buy-and-hold benchmark", f"{simulation_stats['benchmark_return_pct']:.2f}%"
            )
            backtest_metrics[2].metric(
                "Maximum drawdown", f"{simulation_stats['maximum_drawdown_pct']:.2f}%"
            )
            backtest_metrics[3].metric(
                "Position changes", f"{simulation_stats['position_changes']:.0f}"
            )
            equity_chart = go.Figure()
            equity_chart.add_trace(
                go.Scatter(
                    x=simulation.index,
                    y=simulation["equity"],
                    name="Rule-based simulation",
                )
            )
            equity_chart.add_trace(
                go.Scatter(
                    x=simulation.index,
                    y=simulation["buy_hold_equity"],
                    name="Buy-and-hold benchmark",
                )
            )
            equity_chart.update_layout(
                height=420,
                xaxis_title="Provider timestamp",
                yaxis_title="Simulated account value",
            )
            st.plotly_chart(equity_chart, use_container_width=True)
            export = analysis_frame.join(
                simulation[["position", "strategy_return", "equity", "buy_hold_equity"]]
            )
            st.download_button(
                "Download backtest CSV",
                export.reset_index().to_csv(index=False).encode("utf-8"),
                file_name=f"backtest_{selected_instrument['provider_symbol']}_{timeframe}.csv",
                mime="text/csv",
            )
            st.warning(
                "Simplified historical simulation, not a forecast. It uses the prior "
                "bar's signal and charges the chosen basis-point estimate on position "
                "changes. Variable spreads, commission schedules, slippage, swaps, gaps, "
                "liquidity, taxes, and execution failures are not fully modeled."
            )

with settings_tab:
    st.subheader("Application settings")
    st.caption(
        "These settings are saved in config/settings.json. Provider credentials "
        "are not stored there."
    )
    all_symbols = list(instruments_by_symbol)
    with st.form("settings-form"):
        edited_fast = st.number_input(
            "Fast EMA period",
            min_value=2,
            max_value=200,
            value=int(settings["fast_ema"]),
            step=1,
        )
        edited_slow = st.number_input(
            "Slow EMA period",
            min_value=int(edited_fast) + 1,
            max_value=500,
            value=max(int(settings["slow_ema"]), int(edited_fast) + 1),
            step=1,
        )
        edited_rsi = st.number_input(
            "RSI period", min_value=2, max_value=100, value=int(settings["rsi_period"])
        )
        edited_atr = st.number_input(
            "ATR period", min_value=2, max_value=100, value=int(settings["atr_period"])
        )
        edited_cost = st.number_input(
            "Estimated transaction cost (bps)",
            min_value=0.0,
            max_value=100.0,
            value=float(settings["estimated_transaction_cost_bps"]),
            step=0.5,
        )
        edited_trade_risk = st.number_input(
            "Maximum risk per paper trade (%)",
            min_value=0.1,
            max_value=5.0,
            value=float(settings["risk_per_trade_pct"]),
            step=0.1,
        )
        edited_total_risk = st.number_input(
            "Maximum total open risk (%)",
            min_value=float(edited_trade_risk),
            max_value=10.0,
            value=max(
                float(settings["max_total_open_risk_pct"]), float(edited_trade_risk)
            ),
            step=0.1,
        )
        edited_open_positions = st.number_input(
            "Maximum open paper positions",
            min_value=1,
            max_value=20,
            value=int(settings["max_open_positions"]),
            step=1,
        )
        edited_interval = st.number_input(
            "Monitor scan interval (seconds; minimum 60)",
            min_value=60,
            max_value=86_400,
            value=int(settings["monitor_interval_seconds"]),
            step=60,
        )
        edited_rpm = st.number_input(
            "Provider request limit (requests/minute)",
            min_value=1,
            max_value=60,
            value=int(settings["requests_per_minute"]),
            step=1,
        )
        edited_timeframes = st.multiselect(
            "Monitor timeframes",
            list(TIMEFRAMES),
            default=list(settings["monitor_timeframes"]),
        )
        edited_watchlist = st.multiselect(
            "Monitor watchlist",
            all_symbols,
            default=[
                symbol for symbol in settings["watchlist"] if symbol in all_symbols
            ],
            format_func=lambda symbol: f"{instrument_names[symbol]} ({symbol})",
        )
        save = st.form_submit_button("Save settings", type="primary")
    if save:
        new_settings = dict(settings)
        new_settings.update(
            {
                "estimated_transaction_cost_bps": float(edited_cost),
                "fast_ema": int(edited_fast),
                "slow_ema": int(edited_slow),
                "rsi_period": int(edited_rsi),
                "atr_period": int(edited_atr),
                "risk_per_trade_pct": float(edited_trade_risk),
                "max_total_open_risk_pct": float(edited_total_risk),
                "max_open_positions": int(edited_open_positions),
                "monitor_interval_seconds": int(edited_interval),
                "requests_per_minute": int(edited_rpm),
                "monitor_timeframes": edited_timeframes,
                "watchlist": edited_watchlist,
            }
        )
        try:
            save_settings(new_settings, DEFAULT_SETTINGS_PATH)
            st.success("Settings saved.")
            st.rerun()
        except (OSError, ValueError) as exc:
            LOGGER.exception("Settings could not be saved")
            st.error(f"Settings were not saved: {exc}")

    st.subheader("Add a provider candidate")
    st.caption(
        "Yahoo Finance does not provide an exhaustive stable symbol catalog. "
        "Adding a symbol only configures it; provider availability is checked "
        "when data is requested."
    )
    with st.form("add-instrument-form"):
        custom_name = st.text_input("Display name")
        custom_type = st.selectbox(
            "Instrument type",
            [
                "Forex major",
                "Forex minor",
                "Forex exotic",
                "Metal",
                "Energy",
                "Stock index",
                "Cryptocurrency",
                "Other",
            ],
        )
        custom_provider = st.selectbox("Data provider", ["yfinance"])
        custom_symbol = st.text_input("Provider symbol")
        add_candidate = st.form_submit_button("Add candidate instrument")
    if add_candidate:
        try:
            data_source = provider_data_source(custom_provider)
            add_instrument(
                ROOT / "config" / "instruments.json",
                {
                    "name": custom_name.strip(),
                    "instrument_type": custom_type,
                    "provider": custom_provider,
                    "provider_symbol": custom_symbol.strip(),
                    "data_source": data_source,
                },
            )
            st.success("Candidate added. Select it from the market list after refresh.")
            st.rerun()
        except (OSError, ValueError) as exc:
            LOGGER.exception("Candidate instrument was not added")
            st.error(f"Candidate was not added: {exc}")

    st.subheader("Provider, notifications, and monitor status")
    st.write("**Market data:** Yahoo Finance via yfinance (public, unofficial client).")
    st.write(
        "**Webhook:** "
        + (
            "configured in environment"
            if os.environ.get("FOREX_SIGNAL_WEBHOOK_URL")
            else "not configured; in-app alert storage remains enabled"
        )
    )
    st.write("**Database:** " + str(storage.get_database_path()))
    events = storage.get_system_events(limit=30)
    most_recent_scan = next(
        (
            event
            for event in events
            if event["component"] == "monitor"
            and "scan completed" in event["message"].lower()
        ),
        None,
    )
    st.write(
        "**Last monitor scan:** "
        + (most_recent_scan["created_at"] if most_recent_scan else "No completed scan recorded")
    )
    st.caption(
        "For recurring scans, run the separate monitor process with "
        "`python monitor.py`. That process must remain running; Streamlit alone "
        "does not keep monitoring active. A local process is not a 24/7 hosted "
        "deployment."
    )
    st.subheader("Recent system events")
    if events:
        st.dataframe(pd.DataFrame(events), hide_index=True, use_container_width=True)
    st.markdown(
        """
**Data caveats:** Yahoo Finance coverage and intraday lookback vary by symbol;
the registry is an editable candidate list, not an exhaustive provider catalog.
Public data may be delayed, missing, corrected, rate-limited, or unavailable.
No paid service or live broker connection is configured.
"""
    )
