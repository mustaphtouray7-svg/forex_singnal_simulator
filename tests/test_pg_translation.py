import unittest
from unittest.mock import patch

import storage


class _FakeCursor:
    def __init__(self, connection, sql):
        self.connection = connection
        self.sql = sql
        self.description = None
        self.rowcount = 1

    def execute(self, sql, parameters=()):
        self.connection.statements.append(sql)

    def fetchone(self):
        return {"id": 7}


class _FakeConnection:
    def __init__(self):
        self.statements = []

    def cursor(self):
        return _FakeCursor(self, None)

    def execute(self, sql, parameters=()):
        self.statements.append(sql)
        return _FakeCursor(self, sql)


class PostgreSQLTranslationTests(unittest.TestCase):
    def setUp(self):
        self.raw = _FakeConnection()
        self.connection = storage._PgConnection(self.raw)

    def test_insert_or_ignore_with_leading_newlines_and_returning_id(self):
        self.connection.execute(
            "\n\n    INSERT OR IGNORE INTO paper_account (id) VALUES (?)",
            (1,),
        )
        sql = self.raw.statements[-1]
        self.assertIn("INSERT INTO paper_account", sql)
        self.assertNotIn("OR IGNORE", sql.upper())
        self.assertIn("ON CONFLICT DO NOTHING", sql)
        self.assertIn("RETURNING id", sql)

    def test_real_columns_become_double_precision(self):
        self.connection.executescript(
            "CREATE TABLE sample (price REAL NOT NULL, note TEXT, pnl REAL);"
        )
        sql = self.raw.statements[-1]
        self.assertIn("price DOUBLE PRECISION", sql)
        self.assertIn("pnl DOUBLE PRECISION", sql)
        self.assertNotIn(" REAL", sql)

    def test_postgres_init_runs_once_per_process(self):
        with patch.dict("os.environ", {"FOREX_SIGNAL_DATABASE_URL": "postgresql://test"}):
            with patch.object(storage, "_init_db_impl") as initialise:
                original = storage._PG_SCHEMA_INITIALIZED
                storage._PG_SCHEMA_INITIALIZED = False
                try:
                    storage.init_db()
                    storage.init_db()
                    self.assertEqual(initialise.call_count, 1)
                finally:
                    storage._PG_SCHEMA_INITIALIZED = original


if __name__ == "__main__":
    unittest.main()
