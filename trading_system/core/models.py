"""
Database models using SQLAlchemy with TimescaleDB hypertable support.
"""
from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, Float, Index, Integer,
    String, Text, Enum, ForeignKey, UniqueConstraint, JSON
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()


class TimeframeEnum(str, PyEnum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"


class SignalDirection(str, PyEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class FVGStatus(str, PyEnum):
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FULLY_FILLED = "FULLY_FILLED"


class LiquidityType(str, PyEnum):
    BUY_SIDE = "BUY_SIDE"
    SELL_SIDE = "SELL_SIDE"


# ─── Tick Data ───────────────────────────────────────────────────────────────

class Tick(Base):
    __tablename__ = "ticks"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    symbol = Column(String(50), nullable=False, index=True)
    token = Column(String(20), nullable=False)
    timestamp = Column(DateTime, nullable=False)
    ltp = Column(Float, nullable=False)
    volume = Column(BigInteger, default=0)
    open_interest = Column(BigInteger, default=0)
    best_bid = Column(Float)
    best_ask = Column(Float)
    bid_qty = Column(BigInteger)
    ask_qty = Column(BigInteger)
    
    __table_args__ = (
        Index("ix_ticks_symbol_timestamp", "symbol", "timestamp"),
    )


# ─── OHLCV Candle Data ──────────────────────────────────────────────────────

class Candle(Base):
    __tablename__ = "candles"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    symbol = Column(String(50), nullable=False)
    token = Column(String(20), nullable=False)
    timeframe = Column(String(5), nullable=False)  # 1m, 5m, 15m
    timestamp = Column(DateTime, nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(BigInteger, nullable=False, default=0)
    
    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "timestamp", name="uq_candle"),
        Index("ix_candles_symbol_tf_ts", "symbol", "timeframe", "timestamp"),
    )


# ─── Fair Value Gaps ─────────────────────────────────────────────────────────

class FairValueGap(Base):
    __tablename__ = "fair_value_gaps"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    symbol = Column(String(50), nullable=False, index=True)
    timeframe = Column(String(5), nullable=False)
    direction = Column(String(10), nullable=False)  # BULLISH / BEARISH
    status = Column(String(20), nullable=False, default="OPEN")
    
    gap_high = Column(Float, nullable=False)
    gap_low = Column(Float, nullable=False)
    gap_size_percent = Column(Float, nullable=False)
    
    formation_time = Column(DateTime, nullable=False)
    fill_time = Column(DateTime)
    
    volume_at_formation = Column(BigInteger)
    quality_score = Column(Float)  # 0-100
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        Index("ix_fvg_symbol_status", "symbol", "status"),
    )


# ─── Liquidity Zones ────────────────────────────────────────────────────────

class LiquidityZone(Base):
    __tablename__ = "liquidity_zones"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    symbol = Column(String(50), nullable=False, index=True)
    timeframe = Column(String(5), nullable=False)
    zone_type = Column(String(20), nullable=False)  # BUY_SIDE / SELL_SIDE
    
    price_level = Column(Float, nullable=False)
    touch_count = Column(Integer, default=1)
    
    swept = Column(Boolean, default=False)
    sweep_time = Column(DateTime)
    sweep_strength = Column(Float)  # 0-100
    
    formed_at = Column(DateTime, nullable=False)
    invalidated_at = Column(DateTime)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        Index("ix_liq_symbol_type", "symbol", "zone_type", "swept"),
    )


# ─── Volume Profile ─────────────────────────────────────────────────────────

class VolumeProfile(Base):
    __tablename__ = "volume_profiles"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    symbol = Column(String(50), nullable=False, index=True)
    timeframe = Column(String(5), nullable=False)
    session_date = Column(DateTime, nullable=False)
    
    poc_price = Column(Float, nullable=False)
    value_area_high = Column(Float, nullable=False)
    value_area_low = Column(Float, nullable=False)
    
    # JSON array of {price, volume} for HVN and LVN
    high_volume_nodes = Column(JSON)
    low_volume_nodes = Column(JSON)
    
    total_volume = Column(BigInteger)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "session_date", name="uq_vp"),
    )


# ─── Anchored VWAP ──────────────────────────────────────────────────────────

class AnchoredVWAP(Base):
    __tablename__ = "anchored_vwaps"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    symbol = Column(String(50), nullable=False, index=True)
    anchor_type = Column(String(30), nullable=False)  # prev_day_high, prev_day_low, opening_range, swing_high, swing_low
    anchor_time = Column(DateTime, nullable=False)
    anchor_price = Column(Float, nullable=False)
    current_vwap = Column(Float, nullable=False)
    
    last_updated = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        Index("ix_avwap_symbol_type", "symbol", "anchor_type"),
    )


# ─── Trading Signals ────────────────────────────────────────────────────────

class Signal(Base):
    __tablename__ = "signals"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    symbol = Column(String(50), nullable=False, index=True)
    direction = Column(String(10), nullable=False)  # LONG / SHORT
    
    entry_zone_low = Column(Float, nullable=False)
    entry_zone_high = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=False)
    target_1 = Column(Float, nullable=False)
    target_2 = Column(Float)
    
    confidence_score = Column(Float, nullable=False)
    
    # Component scores
    liquidity_sweep_score = Column(Float, default=0)
    fvg_score = Column(Float, default=0)
    vwap_score = Column(Float, default=0)
    volume_profile_score = Column(Float, default=0)
    order_flow_score = Column(Float, default=0)
    
    # Context
    reasoning = Column(Text)
    timeframe = Column(String(5))
    
    # Status tracking
    notified = Column(Boolean, default=False)
    notification_time = Column(DateTime)
    
    # Outcome (for backtesting)
    outcome = Column(String(20))  # WIN / LOSS / BREAKEVEN / ACTIVE
    outcome_rr = Column(Float)  # Achieved R:R ratio
    exit_price = Column(Float)
    exit_time = Column(DateTime)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        Index("ix_signals_symbol_time", "symbol", "created_at"),
        Index("ix_signals_score", "confidence_score"),
    )


# ─── Market Structure ────────────────────────────────────────────────────────

class MarketStructure(Base):
    __tablename__ = "market_structure"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    symbol = Column(String(50), nullable=False, index=True)
    timeframe = Column(String(5), nullable=False)
    
    structure_type = Column(String(20), nullable=False)  # HH, HL, LH, LL, BOS, CHOCH
    price = Column(Float, nullable=False)
    timestamp = Column(DateTime, nullable=False)
    
    trend = Column(String(10))  # BULLISH / BEARISH / RANGING
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        Index("ix_ms_symbol_tf_ts", "symbol", "timeframe", "timestamp"),
    )
