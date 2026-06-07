# Trading Signal Detection System

Production-grade Python system for detecting high-quality trade setups in Indian equities using Smart Money Concepts and institutional order flow analysis.

## Architecture

```
trading_system/
├── config/          # Settings, environment variables
├── core/            # Database models, schemas, cache
├── data/            # SmartAPI client, symbol manager, candle aggregation
├── engines/         # Detection engines (the brain)
│   ├── liquidity_sweep.py    # Equal highs/lows, stop hunts
│   ├── anchored_vwap.py      # Multi-anchor VWAP with signal detection
│   ├── fair_value_gap.py     # FVG detection and tracking
│   ├── volume_profile.py     # POC, VAH, VAL, HVN, LVN
│   ├── order_flow.py         # Volume delta, RVOL, large trades
│   ├── market_structure.py   # HH/HL/LH/LL, BOS, CHoCH
│   └── signal_aggregator.py  # Combines all engines → signal
├── notifications/   # Telegram alerts
├── api/             # FastAPI monitoring dashboard
├── backtesting/     # Historical replay engine
└── utils/           # Helpers, logging
```

## Quick Start

### 1. Prerequisites

- Python 3.11+
- PostgreSQL 16 with TimescaleDB extension
- Redis 7+
- Angel One SmartAPI account
- Telegram Bot (for notifications)

### 2. Setup

```bash
# Clone and setup
cd Gameplan
python -m venv venv
venv\Scripts\activate  # Windows

# Install dependencies
pip install -r trading_system/requirements.txt

# Configure environment
copy .env.example .env
# Edit .env with your credentials
```

### 3. Database Setup

```bash
# Create PostgreSQL database
createdb trading_signals

# Run migrations (creates tables + TimescaleDB hypertables)
python -c "
import asyncio
from trading_system.core.database import async_engine, init_db
from trading_system.core.migrations import run_migration

async def setup():
    await init_db()
    await run_migration(async_engine)

asyncio.run(setup())
"
```

### 4. Run the System

```bash
# Start the signal detection engine
python -m trading_system.main

# Start the API server (separate terminal)
uvicorn trading_system.api.app:app --host 0.0.0.0 --port 8000 --reload
```

### 5. Docker Deployment

```bash
docker-compose up -d
```

## Configuration

All settings are controlled via environment variables (see `.env.example`):

| Variable | Description | Default |
|----------|-------------|---------|
| `ANGEL_API_KEY` | Angel One API key | - |
| `ANGEL_CLIENT_CODE` | Trading account code | - |
| `ANGEL_PASSWORD` | Account password | - |
| `ANGEL_TOTP_SECRET` | TOTP secret for 2FA | - |
| `DB_HOST` | PostgreSQL host | localhost |
| `REDIS_HOST` | Redis host | localhost |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token | - |
| `TELEGRAM_CHAT_ID` | Telegram chat/group ID | - |
| `SIGNAL_MIN_CONFIDENCE_SCORE` | Min score for notification | 80.0 |

## Signal Detection Logic

### High Confidence LONG
1. ✅ Sell-side liquidity sweep detected (price swept below equal lows)
2. ✅ Bullish FVG nearby (unfilled gap below current price)
3. ✅ Price above Anchored VWAP (reclaim or holding above)
4. ✅ Volume profile support at HVN or POC
5. ✅ Positive volume delta (buying pressure)

### High Confidence SHORT
1. ✅ Buy-side liquidity sweep detected (price swept above equal highs)
2. ✅ Bearish FVG nearby (unfilled gap above current price)
3. ✅ Price below Anchored VWAP (rejection or trading below)
4. ✅ Volume profile resistance at HVN or POC
5. ✅ Negative volume delta (selling pressure)

### Scoring Weights
- Liquidity Sweep: 25%
- Fair Value Gap: 20%
- Anchored VWAP: 20%
- Volume Profile: 20%
- Order Flow: 15%

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check |
| `GET /api/signals` | Recent signals (filterable) |
| `GET /api/signals/active` | Currently active signals |
| `GET /api/fvgs` | Fair Value Gaps |
| `GET /api/liquidity-zones` | Liquidity zones |
| `GET /api/vwaps` | Anchored VWAP levels |
| `GET /api/dashboard` | Dashboard summary |
| `GET /api/stats` | Win rate, R:R stats |

## Backtesting

```python
from trading_system.backtesting.engine import BacktestEngine
from trading_system.data.smartapi_client import AngelOneClient

# Load historical data
client = AngelOneClient()
await client.authenticate()
candles = await client.backfill_symbol("RELIANCE-EQ", "2885", "NSE", "15m", days=90)

# Run backtest
engine = BacktestEngine()
result = engine.run_backtest("RELIANCE-EQ", candles, "15m")

print(result.summary())
# {'total_signals': 45, 'wins': 28, 'losses': 17, 'win_rate': 62.2, 'avg_rr': 2.1, ...}
```

## Testing

```bash
pytest tests/ -v
pytest tests/ --cov=trading_system --cov-report=html
```

## Additional Concepts Included (Beyond Request)

1. **Market Structure Engine** - HH/HL/LH/LL, BOS, CHoCH detection for trend context
2. **Signal Deduplication** - Redis-based cooldown to prevent spam
3. **ATR-based position sizing** - Dynamic SL/TP based on volatility
4. **Cumulative Volume Delta** - Session-level delta tracking
5. **Delta Divergence Detection** - Price/volume divergence signals
6. **Opening Range VWAP** - First 15-min range as additional anchor
7. **TimescaleDB Continuous Aggregates** - Auto-generated 5m/15m candles from 1m
8. **Data Compression** - Automatic compression of old data
9. **Rate Limiting** - Telegram message rate control
10. **Graceful Shutdown** - Signal handling for clean exit

## Monitoring

The system logs all activity to `logs/{date}/trading_system.log` and exposes a dashboard via the FastAPI `/api/dashboard` endpoint.

## Disclaimer

This system is for educational and research purposes. It generates notifications only - no automated order execution. Always do your own analysis before trading.
