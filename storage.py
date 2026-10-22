import json
import math
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Union


ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE_PATH = ROOT / "data" / "forex_signal_lab.sqlite3"
ALERT_STATUSES = {"pending", "approved", "rejected", "paper_traded", "stale"}


def _database_path(path: Optional[Union[str, Path]] = None) -> Path:
    configured = path or os.environ.get("FOREX_SIGNAL_DB_PATH") or DEFAULT_DATABASE_PATH
    return Path(configured)


def get_database_path() -> Path:
    return _database_path()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _connect(path: Optional[Union[str, Path]] = None) -> Iterator[sqlite3.Connection]:
    database_path = _database_path(path)
    if str(database_path) != ":memory:":
        database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(database_path), timeout=10)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        if str(database_path) != ":memory:":
            connection.execute("PRAGMA journal_mode = WAL")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db(
    path: Optional[Union[str, Path]] = None, initial_balance: float = 10_000.0
) -> None:
    if initial_balance <= 0:
        raise ValueError("Initial paper balance must be positive.")
    with _connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS analysis_records (
                id INTEGER PRIMARY KEY,
                instrument_name TEXT NOT NULL,
                provider TEXT NOT NULL,
                provider_symbol TEXT NOT NULL,
                data_source TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                bar_timestamp TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                signal INTEGER NOT NULL CHECK (signal IN (-1, 0, 1)),
                close REAL NOT NULL,
                rsi REAL,
                atr REAL,
                freshness TEXT NOT NULL,
                market_structure TEXT NOT NULL,
                support REAL,
                resistance REAL,
                indicators_json TEXT NOT NULL DEFAULT '{}',
                rules_json TEXT NOT NULL,
                scenario_json TEXT,
                UNIQUE (provider_symbol, timeframe, bar_timestamp)
            );
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY,
                analysis_id INTEGER NOT NULL UNIQUE REFERENCES analysis_records(id),
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'rejected', 'paper_traded', 'stale')),
                created_at TEXT NOT NULL,
                decided_at TEXT,
                notification_status TEXT NOT NULL DEFAULT 'pending',
                notification_attempts INTEGER NOT NULL DEFAULT 0,
                notification_next_attempt_at TEXT,
                notification_last_error TEXT
            );
            CREATE TABLE IF NOT EXISTS system_events (
                id INTEGER PRIMARY KEY,
                created_at TEXT NOT NULL,
                level TEXT NOT NULL,
                component TEXT NOT NULL,
                provider_symbol TEXT,
                message TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paper_account (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                initial_balance REAL NOT NULL,
                balance REAL NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paper_trades (
                id INTEGER PRIMARY KEY,
                alert_id INTEGER NOT NULL UNIQUE REFERENCES alerts(id),
                instrument_name TEXT NOT NULL,
                provider_symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                signal INTEGER NOT NULL CHECK (signal IN (-1, 1)),
                quantity REAL NOT NULL CHECK (quantity > 0),
                entry_price REAL NOT NULL,
                stop_price REAL NOT NULL,
                target_1 REAL NOT NULL,
                target_2 REAL NOT NULL,
                entry_cost REAL NOT NULL,
                opened_at TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('open', 'closed')),
                exit_price REAL,
                exit_cost REAL,
                gross_pnl REAL,
                net_pnl REAL,
                closed_at TEXT,
                close_reason TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_analysis_time
                ON analysis_records(recorded_at DESC);
            CREATE INDEX IF NOT EXISTS idx_alert_status
                ON alerts(status, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_trades_status
                ON paper_trades(status, opened_at DESC);
            """
        )
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(analysis_records)")
        }
        if "indicators_json" not in columns:
            connection.execute(
                "ALTER TABLE analysis_records ADD COLUMN indicators_json TEXT NOT NULL DEFAULT '{}'"
            )
        connection.execute(
            """
            INSERT OR IGNORE INTO paper_account
                (id, initial_balance, balance, updated_at)
            VALUES (1, ?, ?, ?)
            """,
            (initial_balance, initial_balance, _utc_now()),
        )


def record_analysis(
    instrument: Dict[str, str],
    timeframe: str,
    summary: Dict[str, Any],
    freshness: str,
    path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    if summary["signal"] not in (-1, 0, 1):
        raise ValueError("Analysis signal must be -1, 0, or 1.")
    init_db(path)
    now = _utc_now()
    with _connect(path) as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO analysis_records (
                instrument_name, provider, provider_symbol, data_source, timeframe,
                bar_timestamp, recorded_at, signal, close, rsi, atr, freshness,
                market_structure, support, resistance, rules_json, scenario_json,
                indicators_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                instrument["name"],
                instrument["provider"],
                instrument["provider_symbol"],
                instrument["data_source"],
                timeframe,
                summary["timestamp"],
                now,
                summary["signal"],
                summary["close"],
                summary["rsi"],
                summary["atr"] if summary["atr"] == summary["atr"] else None,
                freshness,
                summary["market_structure"],
                summary["support"],
                summary["resistance"],
                json.dumps(summary["rules"]),
                json.dumps(summary["scenario"]) if summary["scenario"] else None,
                json.dumps(summary.get("indicators", {})),
            ),
        )
        row = connection.execute(
            """
            SELECT id FROM analysis_records
            WHERE provider_symbol = ? AND timeframe = ? AND bar_timestamp = ?
            """,
            (instrument["provider_symbol"], timeframe, summary["timestamp"]),
        ).fetchone()
        analysis_id = int(row["id"])
        alert_id = None
        created = False
        if summary["signal"] != 0 and freshness == "Fresh":
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO alerts (analysis_id, created_at)
                VALUES (?, ?)
                """,
                (analysis_id, now),
            )
            created = cursor.rowcount == 1
            alert_row = connection.execute(
                "SELECT id FROM alerts WHERE analysis_id = ?", (analysis_id,)
            ).fetchone()
            alert_id = int(alert_row["id"])
        return {
            "analysis_id": analysis_id,
            "alert_id": alert_id,
            "alert_created": created,
        }


def get_alerts(
    path: Optional[Union[str, Path]] = None, limit: int = 200
) -> List[Dict[str, Any]]:
    init_db(path)
    with _connect(path) as connection:
        rows = connection.execute(
            """
            SELECT a.id AS alert_id, a.status, a.created_at, a.decided_at,
                   a.notification_status, r.*
            FROM alerts a JOIN analysis_records r ON r.id = a.analysis_id
            ORDER BY a.created_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["indicators"] = json.loads(item.pop("indicators_json"))
        item["rules"] = json.loads(item.pop("rules_json"))
        encoded_scenario = item.pop("scenario_json")
        item["scenario"] = json.loads(encoded_scenario) if encoded_scenario else None
        result.append(item)
    return result


def get_analysis_history(
    path: Optional[Union[str, Path]] = None, limit: int = 100
) -> List[Dict[str, Any]]:
    init_db(path)
    with _connect(path) as connection:
        rows = connection.execute(
            """
            SELECT * FROM analysis_records ORDER BY recorded_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["indicators"] = json.loads(item.pop("indicators_json"))
        item["rules"] = json.loads(item.pop("rules_json"))
        encoded_scenario = item.pop("scenario_json")
        item["scenario"] = json.loads(encoded_scenario) if encoded_scenario else None
        result.append(item)
    return result


def update_alert_status(
    alert_id: int, status: str, path: Optional[Union[str, Path]] = None
) -> None:
    if status not in ALERT_STATUSES:
        raise ValueError(f"Invalid alert status: {status}")
    init_db(path)
    with _connect(path) as connection:
        cursor = connection.execute(
            "UPDATE alerts SET status = ?, decided_at = ? WHERE id = ?",
            (status, _utc_now(), alert_id),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"Alert {alert_id} does not exist.")


def claim_notification(
    alert_id: int, path: Optional[Union[str, Path]] = None
) -> Optional[Dict[str, Any]]:
    init_db(path)
    now = datetime.now(timezone.utc)
    with _connect(path) as connection:
        row = connection.execute(
            """
            SELECT a.notification_status, a.notification_next_attempt_at,
                   r.instrument_name, r.provider_symbol, r.timeframe, r.signal,
                   r.bar_timestamp, r.close, r.freshness, r.rules_json
            FROM alerts a JOIN analysis_records r ON r.id = a.analysis_id
            WHERE a.id = ?
            """,
            (alert_id,),
        ).fetchone()
        if row is None or row["notification_status"] not in ("pending", "failed"):
            if row is None or row["notification_status"] != "sending":
                return None
        next_attempt = row["notification_next_attempt_at"]
        if next_attempt and datetime.fromisoformat(next_attempt) > now:
            return None
        connection.execute(
            """
            UPDATE alerts SET notification_status = 'sending',
                notification_attempts = notification_attempts + 1,
                notification_next_attempt_at = ?
            WHERE id = ?
            """,
            ((now + timedelta(seconds=120)).isoformat(timespec="seconds"), alert_id),
        )
        return dict(row)


def finish_notification(
    alert_id: int,
    sent: bool,
    error: Optional[str] = None,
    path: Optional[Union[str, Path]] = None,
) -> None:
    init_db(path)
    with _connect(path) as connection:
        if sent:
            connection.execute(
                """
                UPDATE alerts SET notification_status = 'sent',
                    notification_next_attempt_at = NULL,
                    notification_last_error = NULL
                WHERE id = ?
                """,
                (alert_id,),
            )
        else:
            row = connection.execute(
                "SELECT notification_attempts FROM alerts WHERE id = ?", (alert_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Alert {alert_id} does not exist.")
            delay = min(60 * (2 ** max(0, int(row["notification_attempts"]) - 1)), 3600)
            next_attempt = datetime.now(timezone.utc) + timedelta(seconds=delay)
            connection.execute(
                """
                UPDATE alerts SET notification_status = 'failed',
                    notification_next_attempt_at = ?,
                    notification_last_error = ?
                WHERE id = ?
                """,
                (next_attempt.isoformat(timespec="seconds"), (error or "Unknown error")[:500], alert_id),
            )


def log_system_event(
    level: str,
    component: str,
    message: str,
    provider_symbol: Optional[str] = None,
    path: Optional[Union[str, Path]] = None,
) -> None:
    init_db(path)
    with _connect(path) as connection:
        connection.execute(
            """
            INSERT INTO system_events (created_at, level, component, provider_symbol, message)
            VALUES (?, ?, ?, ?, ?)
            """,
            (_utc_now(), level.upper(), component, provider_symbol, message[:1000]),
        )


def get_system_events(
    path: Optional[Union[str, Path]] = None, limit: int = 100
) -> List[Dict[str, Any]]:
    init_db(path)
    with _connect(path) as connection:
        rows = connection.execute(
            "SELECT * FROM system_events ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


def _validate_risk_limits(
    risk_per_trade_pct: float,
    max_total_risk_pct: float,
    max_open_positions: int,
    cost_bps: float,
) -> None:
    if not all(
        math.isfinite(value)
        for value in (risk_per_trade_pct, max_total_risk_pct, cost_bps)
    ):
        raise ValueError("Risk and cost settings must be finite numbers.")
    if not 0 < risk_per_trade_pct <= 5:
        raise ValueError("Per-trade risk must be greater than 0% and at most 5%.")
    if not risk_per_trade_pct <= max_total_risk_pct <= 10:
        raise ValueError("Total open risk must cover per-trade risk and be at most 10%.")
    if max_open_positions < 1:
        raise ValueError("Maximum open positions must be at least one.")
    if cost_bps < 0 or cost_bps > 100:
        raise ValueError("Estimated transaction cost must be between 0 and 100 basis points.")


def open_paper_trade(
    alert_id: int,
    fresh_signal: int,
    entry_price: float,
    scenario: Dict[str, float],
    risk_per_trade_pct: float,
    max_total_risk_pct: float,
    max_open_positions: int,
    cost_bps: float,
    path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    if fresh_signal not in (-1, 1):
        raise ValueError("Paper trading requires a fresh bullish or bearish signal.")
    if not math.isfinite(entry_price):
        raise ValueError("Entry price must be finite.")
    _validate_risk_limits(
        risk_per_trade_pct, max_total_risk_pct, max_open_positions, cost_bps
    )
    stop_price = float(scenario["invalidation"])
    scenario_values = (
        stop_price,
        float(scenario["target_1"]),
        float(scenario["target_2"]),
    )
    if not all(math.isfinite(value) for value in scenario_values):
        raise ValueError("Paper trade scenario prices must be finite.")
    risk_per_unit = abs(entry_price - stop_price)
    if risk_per_unit <= 0:
        raise ValueError("A positive stop distance is required for risk sizing.")
    init_db(path)
    with _connect(path) as connection:
        alert = connection.execute(
            """
            SELECT a.status, r.instrument_name, r.provider_symbol, r.timeframe, r.signal
            FROM alerts a JOIN analysis_records r ON r.id = a.analysis_id
            WHERE a.id = ?
            """,
            (alert_id,),
        ).fetchone()
        if alert is None:
            raise ValueError(f"Alert {alert_id} does not exist.")
        existing = connection.execute(
            "SELECT id FROM paper_trades WHERE alert_id = ?", (alert_id,)
        ).fetchone()
        if existing is not None:
            raise ValueError("A paper trade already exists for this alert.")
        if alert["status"] not in ("pending", "approved"):
            raise ValueError("This alert is not eligible for a new paper trade.")
        if int(alert["signal"]) != fresh_signal:
            raise ValueError("The fresh signal no longer matches this alert.")
        account = connection.execute(
            "SELECT balance FROM paper_account WHERE id = 1"
        ).fetchone()
        balance = float(account["balance"])
        open_trades = connection.execute(
            """
            SELECT quantity, entry_price, stop_price FROM paper_trades
            WHERE status = 'open'
            """
        ).fetchall()
        if len(open_trades) >= max_open_positions:
            raise ValueError("The maximum number of open paper positions has been reached.")
        existing_risk = sum(
            float(trade["quantity"])
            * abs(float(trade["entry_price"]) - float(trade["stop_price"]))
            for trade in open_trades
        )
        risk_amount = balance * risk_per_trade_pct / 100
        max_total_risk = balance * max_total_risk_pct / 100
        if existing_risk + risk_amount > max_total_risk:
            raise ValueError("Opening this position would exceed the total open-risk limit.")
        quantity = risk_amount / risk_per_unit
        entry_cost = abs(quantity * entry_price) * cost_bps / 10_000
        if entry_cost >= balance:
            raise ValueError("Estimated entry cost exceeds the simulated account balance.")
        cursor = connection.execute(
            """
            INSERT INTO paper_trades (
                alert_id, instrument_name, provider_symbol, timeframe, signal,
                quantity, entry_price, stop_price, target_1, target_2, entry_cost,
                opened_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
            """,
            (
                alert_id,
                alert["instrument_name"],
                alert["provider_symbol"],
                alert["timeframe"],
                fresh_signal,
                quantity,
                entry_price,
                stop_price,
                float(scenario["target_1"]),
                float(scenario["target_2"]),
                entry_cost,
                _utc_now(),
            ),
        )
        connection.execute(
            "UPDATE paper_account SET balance = balance - ?, updated_at = ? WHERE id = 1",
            (entry_cost, _utc_now()),
        )
        connection.execute(
            "UPDATE alerts SET status = 'paper_traded', decided_at = ? WHERE id = ?",
            (_utc_now(), alert_id),
        )
        return {
            "trade_id": int(cursor.lastrowid),
            "quantity": quantity,
            "entry_price": entry_price,
            "stop_price": stop_price,
            "risk_amount": risk_amount,
            "entry_cost": entry_cost,
        }


def close_paper_trade(
    trade_id: int,
    exit_price: float,
    cost_bps: float,
    path: Optional[Union[str, Path]] = None,
) -> Dict[str, float]:
    if not math.isfinite(exit_price):
        raise ValueError("Exit price must be finite.")
    if not math.isfinite(cost_bps) or cost_bps < 0 or cost_bps > 100:
        raise ValueError("Estimated transaction cost must be between 0 and 100 basis points.")
    init_db(path)
    with _connect(path) as connection:
        trade = connection.execute(
            "SELECT * FROM paper_trades WHERE id = ?", (trade_id,)
        ).fetchone()
        if trade is None or trade["status"] != "open":
            raise ValueError(f"Paper trade {trade_id} is not open.")
        direction = int(trade["signal"])
        quantity = float(trade["quantity"])
        entry_price = float(trade["entry_price"])
        gross_pnl = (exit_price - entry_price) * quantity * direction
        exit_cost = abs(exit_price * quantity) * cost_bps / 10_000
        net_pnl = gross_pnl - float(trade["entry_cost"]) - exit_cost
        connection.execute(
            """
            UPDATE paper_trades SET status = 'closed', exit_price = ?,
                exit_cost = ?, gross_pnl = ?, net_pnl = ?, closed_at = ?,
                close_reason = 'manual paper close'
            WHERE id = ?
            """,
            (exit_price, exit_cost, gross_pnl, net_pnl, _utc_now(), trade_id),
        )
        connection.execute(
            "UPDATE paper_account SET balance = balance + ?, updated_at = ? WHERE id = 1",
            (gross_pnl - exit_cost, _utc_now()),
        )
        return {
            "gross_pnl": gross_pnl,
            "entry_cost": float(trade["entry_cost"]),
            "exit_cost": exit_cost,
            "net_pnl": net_pnl,
        }


def mark_paper_trade(
    trade: Dict[str, Any], market_price: float, cost_bps: float
) -> Dict[str, float]:
    if trade["status"] != "open":
        raise ValueError("Only open paper trades can be marked to market.")
    if not math.isfinite(market_price):
        raise ValueError("Market price must be finite.")
    if not math.isfinite(cost_bps) or cost_bps < 0 or cost_bps > 100:
        raise ValueError("Estimated transaction cost must be between 0 and 100 basis points.")
    gross_pnl = (
        (market_price - float(trade["entry_price"]))
        * float(trade["quantity"])
        * int(trade["signal"])
    )
    estimated_exit_cost = (
        abs(market_price * float(trade["quantity"])) * cost_bps / 10_000
    )
    return {
        "market_price": market_price,
        "gross_unrealized_pnl": gross_pnl,
        "estimated_exit_cost": estimated_exit_cost,
        "estimated_net_unrealized_pnl": (
            gross_pnl - float(trade["entry_cost"]) - estimated_exit_cost
        ),
    }


def get_paper_account(
    path: Optional[Union[str, Path]] = None, initial_balance: float = 10_000.0
) -> Dict[str, Any]:
    init_db(path, initial_balance)
    with _connect(path) as connection:
        row = connection.execute("SELECT * FROM paper_account WHERE id = 1").fetchone()
    return dict(row)


def get_paper_trades(
    status: Optional[str] = None,
    path: Optional[Union[str, Path]] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    init_db(path)
    with _connect(path) as connection:
        if status is None:
            rows = connection.execute(
                "SELECT * FROM paper_trades ORDER BY opened_at DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT * FROM paper_trades WHERE status = ?
                ORDER BY opened_at DESC LIMIT ?
                """,
                (status, limit),
            ).fetchall()
    return [dict(row) for row in rows]
