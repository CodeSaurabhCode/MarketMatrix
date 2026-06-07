"""
FastAPI application - Monitoring Dashboard and API.
"""
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc

from trading_system.config.settings import settings
from trading_system.core.database import init_db, close_db, get_db
from trading_system.core.cache import cache
from trading_system.core.models import (
    Signal, FairValueGap, LiquidityZone, AnchoredVWAP, VolumeProfile
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup/shutdown."""
    await init_db()
    await cache.connect()
    logger.info("Application started")
    yield
    await cache.disconnect()
    await close_db()
    logger.info("Application shutdown")


app = FastAPI(
    title="Trading Signal Detection System",
    description="Smart Money Concepts signal detection for Indian equities",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Health Check ────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


# ─── Signals API ─────────────────────────────────────────────────────────────

@app.get("/api/signals")
async def get_signals(
    symbol: str = Query(None),
    direction: str = Query(None),
    min_score: float = Query(80.0),
    limit: int = Query(50),
    db: AsyncSession = Depends(get_db),
):
    """Get recent trade signals."""
    query = select(Signal).where(Signal.confidence_score >= min_score)
    
    if symbol:
        query = query.where(Signal.symbol == symbol)
    if direction:
        query = query.where(Signal.direction == direction)
    
    query = query.order_by(desc(Signal.created_at)).limit(limit)
    result = await db.execute(query)
    signals = result.scalars().all()
    
    return [
        {
            "id": s.id,
            "symbol": s.symbol,
            "direction": s.direction,
            "entry_zone": f"{s.entry_zone_low:.2f} - {s.entry_zone_high:.2f}",
            "stop_loss": s.stop_loss,
            "target_1": s.target_1,
            "target_2": s.target_2,
            "confidence_score": s.confidence_score,
            "reasoning": s.reasoning,
            "timeframe": s.timeframe,
            "outcome": s.outcome,
            "created_at": s.created_at.isoformat(),
        }
        for s in signals
    ]


@app.get("/api/signals/active")
async def get_active_signals(db: AsyncSession = Depends(get_db)):
    """Get currently active (unresolved) signals."""
    query = (
        select(Signal)
        .where(Signal.outcome == "ACTIVE")
        .order_by(desc(Signal.confidence_score))
    )
    result = await db.execute(query)
    signals = result.scalars().all()
    
    return [
        {
            "id": s.id,
            "symbol": s.symbol,
            "direction": s.direction,
            "entry_zone": f"{s.entry_zone_low:.2f} - {s.entry_zone_high:.2f}",
            "stop_loss": s.stop_loss,
            "target_1": s.target_1,
            "confidence_score": s.confidence_score,
            "timeframe": s.timeframe,
            "created_at": s.created_at.isoformat(),
        }
        for s in signals
    ]


# ─── FVG API ────────────────────────────────────────────────────────────────

@app.get("/api/fvgs")
async def get_fvgs(
    symbol: str = Query(None),
    status: str = Query("OPEN"),
    db: AsyncSession = Depends(get_db),
):
    """Get Fair Value Gaps."""
    query = select(FairValueGap)
    
    if symbol:
        query = query.where(FairValueGap.symbol == symbol)
    if status:
        query = query.where(FairValueGap.status == status)
    
    query = query.order_by(desc(FairValueGap.created_at)).limit(100)
    result = await db.execute(query)
    fvgs = result.scalars().all()
    
    return [
        {
            "id": f.id,
            "symbol": f.symbol,
            "timeframe": f.timeframe,
            "direction": f.direction,
            "status": f.status,
            "gap_high": f.gap_high,
            "gap_low": f.gap_low,
            "gap_size_percent": f.gap_size_percent,
            "quality_score": f.quality_score,
            "formation_time": f.formation_time.isoformat(),
        }
        for f in fvgs
    ]


# ─── Liquidity Zones API ────────────────────────────────────────────────────

@app.get("/api/liquidity-zones")
async def get_liquidity_zones(
    symbol: str = Query(None),
    swept: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """Get liquidity zones."""
    query = select(LiquidityZone).where(LiquidityZone.swept == swept)
    
    if symbol:
        query = query.where(LiquidityZone.symbol == symbol)
    
    query = query.order_by(desc(LiquidityZone.created_at)).limit(100)
    result = await db.execute(query)
    zones = result.scalars().all()
    
    return [
        {
            "id": z.id,
            "symbol": z.symbol,
            "timeframe": z.timeframe,
            "zone_type": z.zone_type,
            "price_level": z.price_level,
            "touch_count": z.touch_count,
            "swept": z.swept,
            "sweep_strength": z.sweep_strength,
            "formed_at": z.formed_at.isoformat(),
        }
        for z in zones
    ]


# ─── VWAP API ───────────────────────────────────────────────────────────────

@app.get("/api/vwaps")
async def get_vwaps(
    symbol: str = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Get anchored VWAP levels."""
    query = select(AnchoredVWAP)
    
    if symbol:
        query = query.where(AnchoredVWAP.symbol == symbol)
    
    query = query.order_by(desc(AnchoredVWAP.last_updated)).limit(50)
    result = await db.execute(query)
    vwaps = result.scalars().all()
    
    return [
        {
            "id": v.id,
            "symbol": v.symbol,
            "anchor_type": v.anchor_type,
            "anchor_time": v.anchor_time.isoformat(),
            "anchor_price": v.anchor_price,
            "current_vwap": v.current_vwap,
            "last_updated": v.last_updated.isoformat(),
        }
        for v in vwaps
    ]


# ─── Dashboard API ──────────────────────────────────────────────────────────

@app.get("/api/dashboard")
async def get_dashboard(db: AsyncSession = Depends(get_db)):
    """Get monitoring dashboard data."""
    
    # Top setups by confidence
    top_signals = await db.execute(
        select(Signal)
        .where(Signal.outcome == "ACTIVE")
        .order_by(desc(Signal.confidence_score))
        .limit(10)
    )
    
    # Active FVG count
    active_fvg_count = await db.execute(
        select(func.count(FairValueGap.id))
        .where(FairValueGap.status == "OPEN")
    )
    
    # Active liquidity zones
    active_liq_count = await db.execute(
        select(func.count(LiquidityZone.id))
        .where(LiquidityZone.swept == False)
    )
    
    # Today's signal count
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_signals = await db.execute(
        select(func.count(Signal.id))
        .where(Signal.created_at >= today)
    )
    
    return {
        "top_setups": [
            {
                "symbol": s.symbol,
                "direction": s.direction,
                "confidence": s.confidence_score,
                "entry": f"{s.entry_zone_low:.2f} - {s.entry_zone_high:.2f}",
            }
            for s in top_signals.scalars().all()
        ],
        "active_fvgs": active_fvg_count.scalar() or 0,
        "active_liquidity_zones": active_liq_count.scalar() or 0,
        "today_signals": today_signals.scalar() or 0,
        "timestamp": datetime.now().isoformat(),
    }


# ─── Stats API ───────────────────────────────────────────────────────────────

@app.get("/api/stats")
async def get_stats(db: AsyncSession = Depends(get_db)):
    """Get overall system statistics."""
    
    total_signals = await db.execute(select(func.count(Signal.id)))
    wins = await db.execute(
        select(func.count(Signal.id)).where(Signal.outcome == "WIN")
    )
    losses = await db.execute(
        select(func.count(Signal.id)).where(Signal.outcome == "LOSS")
    )
    
    total = total_signals.scalar() or 0
    win_count = wins.scalar() or 0
    loss_count = losses.scalar() or 0
    
    win_rate = (win_count / (win_count + loss_count) * 100) if (win_count + loss_count) > 0 else 0
    
    avg_rr = await db.execute(
        select(func.avg(Signal.outcome_rr))
        .where(Signal.outcome == "WIN")
    )
    
    return {
        "total_signals": total,
        "wins": win_count,
        "losses": loss_count,
        "win_rate": round(win_rate, 1),
        "avg_rr": round(avg_rr.scalar() or 0, 2),
    }
