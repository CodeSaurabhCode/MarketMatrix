"""
SQL migration script for TimescaleDB setup.
Run this after creating the database.
"""

MIGRATION_SQL = """
-- Enable TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Convert ticks to hypertable
SELECT create_hypertable('ticks', 'timestamp', 
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- Convert candles to hypertable
SELECT create_hypertable('candles', 'timestamp',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

-- Compression policies for older data
ALTER TABLE ticks SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'symbol',
    timescaledb.compress_orderby = 'timestamp DESC'
);

SELECT add_compression_policy('ticks', INTERVAL '7 days', if_not_exists => TRUE);

ALTER TABLE candles SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'symbol, timeframe',
    timescaledb.compress_orderby = 'timestamp DESC'
);

SELECT add_compression_policy('candles', INTERVAL '30 days', if_not_exists => TRUE);

-- Retention policy: keep raw ticks for 30 days
SELECT add_retention_policy('ticks', INTERVAL '30 days', if_not_exists => TRUE);

-- Continuous aggregates for 5m and 15m from 1m data
CREATE MATERIALIZED VIEW IF NOT EXISTS candles_5m
WITH (timescaledb.continuous) AS
SELECT
    symbol,
    token,
    '5m' as timeframe,
    time_bucket('5 minutes', timestamp) AS timestamp,
    first(open, timestamp) AS open,
    max(high) AS high,
    min(low) AS low,
    last(close, timestamp) AS close,
    sum(volume) AS volume
FROM candles
WHERE timeframe = '1m'
GROUP BY symbol, token, time_bucket('5 minutes', timestamp);

CREATE MATERIALIZED VIEW IF NOT EXISTS candles_15m
WITH (timescaledb.continuous) AS
SELECT
    symbol,
    token,
    '15m' as timeframe,
    time_bucket('15 minutes', timestamp) AS timestamp,
    first(open, timestamp) AS open,
    max(high) AS high,
    min(low) AS low,
    last(close, timestamp) AS close,
    sum(volume) AS volume
FROM candles
WHERE timeframe = '1m'
GROUP BY symbol, token, time_bucket('15 minutes', timestamp);

-- Refresh policies for continuous aggregates
SELECT add_continuous_aggregate_policy('candles_5m',
    start_offset => INTERVAL '1 hour',
    end_offset => INTERVAL '5 minutes',
    schedule_interval => INTERVAL '5 minutes',
    if_not_exists => TRUE
);

SELECT add_continuous_aggregate_policy('candles_15m',
    start_offset => INTERVAL '2 hours',
    end_offset => INTERVAL '15 minutes',
    schedule_interval => INTERVAL '15 minutes',
    if_not_exists => TRUE
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS ix_signals_active ON signals (symbol, created_at DESC) WHERE outcome = 'ACTIVE';
CREATE INDEX IF NOT EXISTS ix_fvg_open ON fair_value_gaps (symbol, timeframe) WHERE status = 'OPEN';
CREATE INDEX IF NOT EXISTS ix_liq_active ON liquidity_zones (symbol, zone_type) WHERE swept = false;

-- Institutional SMC enrichment columns
ALTER TABLE fair_value_gaps ADD COLUMN IF NOT EXISTS first_retest_time TIMESTAMP;
ALTER TABLE fair_value_gaps ADD COLUMN IF NOT EXISTS fill_percent DOUBLE PRECISION DEFAULT 0;
ALTER TABLE fair_value_gaps ADD COLUMN IF NOT EXISTS displacement_score DOUBLE PRECISION DEFAULT 0;
ALTER TABLE fair_value_gaps ADD COLUMN IF NOT EXISTS trend_alignment_score DOUBLE PRECISION DEFAULT 0;
ALTER TABLE fair_value_gaps ADD COLUMN IF NOT EXISTS liquidity_context_score DOUBLE PRECISION DEFAULT 0;
ALTER TABLE fair_value_gaps ADD COLUMN IF NOT EXISTS structure_context_score DOUBLE PRECISION DEFAULT 0;
ALTER TABLE fair_value_gaps ADD COLUMN IF NOT EXISTS score_breakdown JSONB;

ALTER TABLE liquidity_zones ADD COLUMN IF NOT EXISTS liquidity_type VARCHAR(30);
ALTER TABLE liquidity_zones ADD COLUMN IF NOT EXISTS pool_id VARCHAR(120);
ALTER TABLE liquidity_zones ADD COLUMN IF NOT EXISTS upper_bound DOUBLE PRECISION;
ALTER TABLE liquidity_zones ADD COLUMN IF NOT EXISTS lower_bound DOUBLE PRECISION;
ALTER TABLE liquidity_zones ADD COLUMN IF NOT EXISTS age_candles INTEGER DEFAULT 0;
ALTER TABLE liquidity_zones ADD COLUMN IF NOT EXISTS strength_score DOUBLE PRECISION DEFAULT 0;
ALTER TABLE liquidity_zones ADD COLUMN IF NOT EXISTS source_indices JSONB;
ALTER TABLE liquidity_zones ADD COLUMN IF NOT EXISTS metadata_json JSONB;

ALTER TABLE signals ADD COLUMN IF NOT EXISTS market_structure_score DOUBLE PRECISION DEFAULT 0;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS volume_confirmation_score DOUBLE PRECISION DEFAULT 0;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS explainability JSONB;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS planned_rr DOUBLE PRECISION;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS mfe DOUBLE PRECISION;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS mae DOUBLE PRECISION;

ALTER TABLE market_structure ADD COLUMN IF NOT EXISTS scope VARCHAR(20) DEFAULT 'EXTERNAL';
ALTER TABLE market_structure ADD COLUMN IF NOT EXISTS direction VARCHAR(10);
ALTER TABLE market_structure ADD COLUMN IF NOT EXISTS broken_level DOUBLE PRECISION;
ALTER TABLE market_structure ADD COLUMN IF NOT EXISTS break_price DOUBLE PRECISION;
ALTER TABLE market_structure ADD COLUMN IF NOT EXISTS protected_high DOUBLE PRECISION;
ALTER TABLE market_structure ADD COLUMN IF NOT EXISTS protected_low DOUBLE PRECISION;
ALTER TABLE market_structure ADD COLUMN IF NOT EXISTS displacement_score DOUBLE PRECISION DEFAULT 0;
ALTER TABLE market_structure ADD COLUMN IF NOT EXISTS strength_score DOUBLE PRECISION DEFAULT 0;
ALTER TABLE market_structure ADD COLUMN IF NOT EXISTS source_index INTEGER;
ALTER TABLE market_structure ADD COLUMN IF NOT EXISTS broken_index INTEGER;
ALTER TABLE market_structure ADD COLUMN IF NOT EXISTS metadata_json JSONB;

CREATE INDEX IF NOT EXISTS ix_liq_pool_id ON liquidity_zones (pool_id);
CREATE INDEX IF NOT EXISTS ix_ms_symbol_scope_ts ON market_structure (symbol, timeframe, scope, timestamp DESC);
"""


async def run_migration(engine):
    """Execute TimescaleDB migration."""
    from sqlalchemy import text
    async with engine.begin() as conn:
        for statement in MIGRATION_SQL.split(';'):
            stmt = statement.strip()
            if stmt:
                try:
                    await conn.execute(text(stmt))
                except Exception as e:
                    print(f"Migration statement failed (may be OK): {e}")
