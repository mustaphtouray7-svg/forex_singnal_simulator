import json
import os
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import storage


WEBHOOK_ENV = "FOREX_SIGNAL_WEBHOOK_URL"


def send_webhook(url: str, alert: Dict[str, Any]) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("The notification webhook must be an HTTPS URL.")
    message = (
        f"{alert['instrument_name']} {alert['timeframe']}: "
        f"{'Bullish' if alert['signal'] == 1 else 'Bearish'} setup at "
        f"{alert['close']} (bar {alert['bar_timestamp']}, "
        f"data {alert['freshness']})."
    )
    payload = {
        "text": message,
        "content": message,
        "alert": {
            "instrument": alert["instrument_name"],
            "provider_symbol": alert["provider_symbol"],
            "timeframe": alert["timeframe"],
            "signal": alert["signal"],
            "timestamp": alert["bar_timestamp"],
            "freshness": alert["freshness"],
            "rules": alert["rules"],
        },
    }
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            if response.status < 200 or response.status >= 300:
                raise ValueError(
                    f"Notification endpoint returned HTTP {response.status}."
                )
    except HTTPError as exc:
        raise ValueError(f"Notification endpoint returned HTTP {exc.code}.") from exc
    except URLError as exc:
        raise ValueError(
            f"Notification endpoint could not be reached ({type(exc.reason).__name__})."
        ) from exc


def maybe_send_alert_notification(
    alert_id: Optional[int], database_path: Optional[str] = None
) -> bool:
    if alert_id is None:
        return False
    webhook_url = os.environ.get(WEBHOOK_ENV)
    if not webhook_url:
        return False
    alert = storage.claim_notification(alert_id, database_path)
    if alert is None:
        return False
    alert["rules"] = json.loads(alert.pop("rules_json"))
    try:
        send_webhook(webhook_url, alert)
    except (ValueError, OSError) as exc:
        storage.finish_notification(
            alert_id, False, f"{type(exc).__name__}: {exc}", database_path
        )
        storage.log_system_event(
            "ERROR", "notifications", f"Webhook delivery failed: {exc}",
            alert["provider_symbol"], database_path
        )
        return False
    storage.finish_notification(alert_id, True, path=database_path)
    storage.log_system_event(
        "INFO",
        "notifications",
        f"Alert {alert_id} delivered to configured webhook.",
        alert["provider_symbol"],
        database_path,
    )
    return True
