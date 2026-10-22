import unittest

from notifications import send_webhook


class NotificationTests(unittest.TestCase):
    def test_notification_rejects_non_https_url_without_network_access(self) -> None:
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            send_webhook(
                "http://example.invalid/webhook",
                {
                    "instrument_name": "EUR/USD",
                    "timeframe": "1d",
                    "signal": 1,
                    "close": 1.0,
                    "bar_timestamp": "2025-01-01",
                    "freshness": "Fresh",
                    "provider_symbol": "EURUSD=X",
                    "rules": [],
                },
            )


if __name__ == "__main__":
    unittest.main()
