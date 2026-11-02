-- Convert existing PostgreSQL REAL columns to 64-bit DOUBLE PRECISION.
-- This preserves existing values while avoiding 32-bit rounding in prices and P/L.
BEGIN;

ALTER TABLE analysis_records
    ALTER COLUMN close TYPE DOUBLE PRECISION USING close::DOUBLE PRECISION,
    ALTER COLUMN rsi TYPE DOUBLE PRECISION USING rsi::DOUBLE PRECISION,
    ALTER COLUMN atr TYPE DOUBLE PRECISION USING atr::DOUBLE PRECISION,
    ALTER COLUMN support TYPE DOUBLE PRECISION USING support::DOUBLE PRECISION,
    ALTER COLUMN resistance TYPE DOUBLE PRECISION USING resistance::DOUBLE PRECISION;

ALTER TABLE paper_account
    ALTER COLUMN initial_balance TYPE DOUBLE PRECISION USING initial_balance::DOUBLE PRECISION,
    ALTER COLUMN balance TYPE DOUBLE PRECISION USING balance::DOUBLE PRECISION;

ALTER TABLE paper_trades
    ALTER COLUMN quantity TYPE DOUBLE PRECISION USING quantity::DOUBLE PRECISION,
    ALTER COLUMN entry_price TYPE DOUBLE PRECISION USING entry_price::DOUBLE PRECISION,
    ALTER COLUMN stop_price TYPE DOUBLE PRECISION USING stop_price::DOUBLE PRECISION,
    ALTER COLUMN target_1 TYPE DOUBLE PRECISION USING target_1::DOUBLE PRECISION,
    ALTER COLUMN target_2 TYPE DOUBLE PRECISION USING target_2::DOUBLE PRECISION,
    ALTER COLUMN entry_cost TYPE DOUBLE PRECISION USING entry_cost::DOUBLE PRECISION,
    ALTER COLUMN exit_price TYPE DOUBLE PRECISION USING exit_price::DOUBLE PRECISION,
    ALTER COLUMN exit_cost TYPE DOUBLE PRECISION USING exit_cost::DOUBLE PRECISION,
    ALTER COLUMN gross_pnl TYPE DOUBLE PRECISION USING gross_pnl::DOUBLE PRECISION,
    ALTER COLUMN net_pnl TYPE DOUBLE PRECISION USING net_pnl::DOUBLE PRECISION;

COMMIT;

-- Verify resulting column types:
-- SELECT table_name, column_name, data_type
-- FROM information_schema.columns
-- WHERE table_schema = current_schema()
--   AND table_name IN ('analysis_records', 'paper_account', 'paper_trades')
--   AND column_name IN (
--       'close', 'rsi', 'atr', 'support', 'resistance',
--       'initial_balance', 'balance', 'quantity', 'entry_price',
--       'stop_price', 'target_1', 'target_2', 'entry_cost',
--       'exit_price', 'exit_cost', 'gross_pnl', 'net_pnl'
--   )
-- ORDER BY table_name, ordinal_position;
