# Forex Signal Lab

Forex Signal Lab is a local Streamlit dashboard for multi-market historical
analysis, persisted signal alerts, and **paper trading only**. It does not
connect to a broker or place real-money orders.

## Run locally on Windows

From the project folder in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Before starting the app locally, set a private access code in that
PowerShell window (choose your own value and do not commit it):

```powershell
$env:FOREX_SIGNAL_ACCESS_CODE = 'choose-a-private-code'
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Open `http://127.0.0.1:8501` and enter the code. Stop the app with `Ctrl+C`.
The app stores its SQLite database in `data/forex_signal_lab.sqlite3`; that
runtime data is git-ignored.

## Hosted free deployment

The Render deployment uses the `FOREX_SIGNAL_ACCESS_CODE` environment variable
for a shared access-code gate. Keep the code private; this is a shared gate,
not separate user accounts or role-based access control. The Settings & system
page provides a manual SQLite backup download. Download backups regularly and
store them privately.

Render's free web service can sleep when idle. Its local filesystem is
ephemeral, so saved alerts, paper trades, and settings may be lost when the
service restarts or redeploys. The current hosted app does not have a durable
database or a separate always-on monitoring worker. The in-dashboard scanner
is best-effort only while the web process is awake; it is not guaranteed to run
24/7. No paid resources are configured. These limits cannot be removed reliably
without a separate durable storage/compute arrangement.

Run the full test suite:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The tests use synthetic price frames and temporary SQLite databases. They do not
place orders or depend on live provider responses.

## Markets and provider coverage

`config/instruments.json` is the editable instrument registry. It currently
lists Yahoo Finance candidate symbols across:

- Major and minor forex pairs and selected exotic pairs
- Gold and silver futures
- Major US, European, and Asian stock indices
- Crude oil and natural gas futures
- Selected cryptocurrencies

The list is **not an exhaustive Yahoo Finance catalog**. Yahoo symbol and
timeframe coverage is not uniform or guaranteed. Add a provider-supported
symbol with the Settings page's **Add a provider candidate** form or by adding
an entry with `name`, `instrument_type`, `provider`, `provider_symbol`, and
`data_source` to the JSON registry. Only the `yfinance`
provider adapter is implemented; any other provider value is reported as
unsupported until an adapter is explicitly added. A symbol's configured
presence does not mean the provider serves it—the dashboard and monitor report
empty responses and errors as unavailable.

The UI identifies the configured provider symbol, instrument type, source,
latest provider bar timestamp, and freshness. Yahoo Finance via `yfinance` is a
free public-data option here, but `yfinance` is an unofficial client. Data can
be delayed, incomplete, corrected, rate-limited, or unavailable; intraday
lookback is provider-limited. Check Yahoo's current terms and permitted use.
No paid data service or subscription is configured. Commercial use or
redistribution may have separate restrictions or costs.

Timeframes include 15-minute, 30-minute, hourly, daily, and weekly bars. The
selected lookback varies by interval. A failed or short history is shown as
unavailable; no prices are fabricated to fill gaps.

## Analysis

- Candlestick chart with fast/slow EMA overlays and prior-range support and
  resistance.
- RSI and Wilder-smoothed ATR with independently configurable periods.
- Market structure is classified as an upside range breakout, downside range
  breakout, inside the prior range, or insufficient/unavailable history. The
  range uses the preceding 20 bars.
- A bullish setup requires fast EMA above slow EMA **and** RSI >= 50. A bearish
  setup requires fast EMA below slow EMA **and** RSI <= 50. The alert shows
  pass/fail for each rule. Otherwise the result is no trade.
- For a directional setup with valid ATR, the dashboard shows an illustrative
  reference entry at the latest close, a +/- 0.25 ATR entry band, a 1.5 ATR
  invalidation, and 1R/2R targets. These are transparent scenario formulas,
  not recommendations or predictions.
- A position-sizing example divides configured paper-account risk by the
  reference entry-to-invalidation distance. It is expressed in simplified
  price-units; contract multipliers, margin, pip values, financing, and actual
  broker execution are not modeled.

Signals do not guarantee profits or predict future prices. Nothing in the app
is financial advice.

## Backtesting limitations

Backtests use the previous bar's signal for the next bar's return to reduce
look-ahead bias. They show simulated equity, maximum drawdown, position changes,
and a buy-and-hold benchmark. The configurable basis-point cost is a simplified
estimate charged on position changes; the selected provider does not supply
reliable per-user spreads or commission schedules for this app. The simulation
does not fully model variable spreads, commissions, slippage, swaps, gaps,
liquidity, taxes, delisted instruments, corrected data, or order failures.
Historical returns are not forecasts.

## Alerts and paper trading

Analysis records, actionable alerts, decisions, notification status, paper
positions, and system events are persisted in local SQLite. The Alerts page
shows the provider bar timestamp, freshness, rule explanations, indicator
values, market structure, and ATR scenario. Approve and Reject update only the
analysis decision; neither executes a trade.

Paper Trade requires:

1. A fresh uncached provider request.
2. The refreshed signal to still match the alert.
3. An explicit confirmation checkbox and submit action.

The database enforces one paper position per alert. The simulated account caps
per-trade and total open risk, number of positions, and estimated transaction
costs. Closing a simulated position also requires fresh provider data and a
separate confirmation. Open P/L marks are only estimates and are refreshed on
request; realized simulated P/L is recorded at paper close. The app cannot
deposit, withdraw, or trade real funds.

## Recurring monitor

Open a **second** PowerShell window in the project folder and run:

```powershell
.\.venv\Scripts\python.exe monitor.py
```

The worker scans the configured watchlist and timeframes, applies the
`requests_per_minute` delay between provider calls, stores analyses and errors,
and retries on later scheduled cycles. It waits `monitor_interval_seconds`
after each scan finishes. Edit `config/settings.json` or use the dashboard's
Settings page to change the watchlist, timeframes, risk limits, and schedule.
The same SQLite database is used by the app and monitor.

For a single scan and exit:

```powershell
.\.venv\Scripts\python.exe monitor.py --once
```

The monitor must remain running in its own process for recurring scans.
Closing the terminal, signing out, losing connectivity, or shutting down the
computer stops it. Windows Task Scheduler or a separately managed always-on
host can be configured by the operator, but this project has not been deployed
or verified as a 24/7 service. Rotating logs are written to
`logs/monitor.log`; recent events also appear in the Settings & system page.

## Optional external notifications

In-app alerts work without any external account. To enable a generic HTTPS JSON
webhook, configure the endpoint in the **monitor process environment**. For
example, in the PowerShell window used to launch the monitor:

```powershell
$env:FOREX_SIGNAL_WEBHOOK_URL = 'https://your-configured-service.example/webhook'
.\.venv\Scripts\python.exe monitor.py
```

The receiver must accept a JSON POST containing `text`, `content`, and `alert`
fields. Use the notification provider's own secure environment/secret
management for any secret-bearing URL. Do not paste secrets into source code,
this chat, or committed files. Delivery failures are logged and retried with
backoff; duplicate delivery claims are guarded in SQLite. No notification
provider is configured by default, and some providers may charge for usage.

Optional database override:

```powershell
$env:FOREX_SIGNAL_DB_PATH = 'C:\path\you\control\forex_signal_lab.sqlite3'
```

## Architecture

- `app.py` — responsive Streamlit overview, market analysis, alerts, paper
  trading, backtest, settings, and system-status views.
- `config/instruments.json` and `config/settings.json` — provider registry and
  non-secret application settings.
- `market_data.py` — provider adapter and OHLCV normalization.
- `market_analysis.py` — indicators, rules, scenarios, freshness, and backtest.
- `storage.py` — SQLite migrations, alert history, paper account, positions,
  notification claims, and system events.
- `monitor.py` — rate-limited recurring scanner; must be run separately.
- `notifications.py` — optional HTTPS JSON webhook using an environment
  variable.
- `paper_trading.py` — fresh-data and same-signal revalidation before entry.
- `tests/` — automated unit and app smoke tests.

No live broker, account passwords, deposits, withdrawals, or broker-setting
changes are part of this project.
