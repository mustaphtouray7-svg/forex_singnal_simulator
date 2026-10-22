import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest


class AppSmokeTests(unittest.TestCase):
    def test_dashboard_remains_available_when_provider_has_no_data(self) -> None:
        app_path = Path(__file__).parents[1] / "app.py"
        with tempfile.TemporaryDirectory() as directory:
            database_path = str(Path(directory) / "dashboard.sqlite3")
            with patch.dict(
                os.environ,
                {
                    "FOREX_SIGNAL_DB_PATH": database_path,
                    "FOREX_SIGNAL_ACCESS_CODE": "test-only-access-code",
                },
            ), patch("market_data.fetch_prices", return_value=pd.DataFrame()):
                app = AppTest.from_file(str(app_path))
                app.session_state["access_granted"] = True
                app.run(timeout=30)

        self.assertEqual(len(app.exception), 0)
        self.assertTrue(
            any("Market analysis is unavailable" in item.value for item in app.info)
        )


if __name__ == "__main__":
    unittest.main()
