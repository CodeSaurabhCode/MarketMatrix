"""
Pydantic schemas for data transfer objects.
"""
from datetime import datetime
from typing import Optional
from enum import Enum

from pydantic import BaseModel, Field


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class FVGDirection(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


class FVGStatusEnum(str, Enum):
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FULLY_FILLED = "FULLY_FILLED"


class Timeframe(str, Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"


class StructureType(str, Enum):
    HH = "HH"  # Higher High
    HL = "HL"  # Higher Low
    LH = "LH"  # Lower High
    LL = "LL"  # Lower Low
    BOS = "BOS"  # Break of Structure
    CHOCH = "CHOCH"  # Change of Character


class Trend(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    RANGING = "RANGING"


# ─── Tick Schema ─────────────────────────────────────────────────────────────

class TickData(BaseModel):
    symbol: str
    token: str
    timestamp: datetime
    ltp: float
    volume: int = 0
    open_interest: int = 0
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    bid_qty: Optional[int] = None
    ask_qty: Optional[int] = None


# ─── Candle Schema ───────────────────────────────────────────────────────────

class CandleData(BaseModel):
    symbol: str
    token: str
    timeframe: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0


# ─── Signal Schema ───────────────────────────────────────────────────────────

class SignalCreate(BaseModel):
    symbol: str
    direction: Direction
    entry_zone_low: float
    entry_zone_high: float
    stop_loss: float
    target_1: float
    target_2: Optional[float] = None
    confidence_score: float
    liquidity_sweep_score: float = 0
    fvg_score: float = 0
    vwap_score: float = 0
    volume_profile_score: float = 0
    order_flow_score: float = 0
    reasoning: Optional[str] = None
    timeframe: Optional[str] = None


class SignalResponse(SignalCreate):
    id: int
    notified: bool
    created_at: datetime
    outcome: Optional[str] = None
    
    class Config:
        from_attributes = True


# ─── FVG Schema ──────────────────────────────────────────────────────────────

class FVGData(BaseModel):
    symbol: str
    timeframe: str
    direction: FVGDirection
    gap_high: float
    gap_low: float
    gap_size_percent: float
    formation_time: datetime
    volume_at_formation: Optional[int] = None
    quality_score: float = 0


# ─── Liquidity Zone Schema ───────────────────────────────────────────────────

class LiquidityZoneData(BaseModel):
    symbol: str
    timeframe: str
    zone_type: str  # BUY_SIDE / SELL_SIDE
    price_level: float
    touch_count: int = 1
    formed_at: datetime


class LiquiditySweepEvent(BaseModel):
    symbol: str
    timeframe: str
    zone_type: str
    price_level: float
    sweep_price: float
    sweep_time: datetime
    sweep_strength: float  # 0-100
    candle_close: float
    rejection_confirmed: bool


# ─── Volume Profile Schema ───────────────────────────────────────────────────

class VolumeProfileData(BaseModel):
    symbol: str
    timeframe: str
    session_date: datetime
    poc_price: float
    value_area_high: float
    value_area_low: float
    high_volume_nodes: list[dict] = []
    low_volume_nodes: list[dict] = []
    total_volume: int = 0


# ─── Anchored VWAP Schema ───────────────────────────────────────────────────

class AnchoredVWAPData(BaseModel):
    symbol: str
    anchor_type: str
    anchor_time: datetime
    anchor_price: float
    current_vwap: float


class VWAPSignal(BaseModel):
    symbol: str
    anchor_type: str
    signal_type: str  # RECLAIM, REJECTION, CROSS_WITH_VOLUME
    vwap_price: float
    current_price: float
    confidence: float


# ─── Order Flow Schema ───────────────────────────────────────────────────────

class OrderFlowSnapshot(BaseModel):
    symbol: str
    timestamp: datetime
    volume_delta: float  # Positive = buying, Negative = selling
    aggressive_buy_volume: int = 0
    aggressive_sell_volume: int = 0
    relative_volume: float = 1.0  # Ratio vs average
    large_trade_detected: bool = False
    cumulative_delta: float = 0


# ─── Market Structure Schema ────────────────────────────────────────────────

class MarketStructurePoint(BaseModel):
    symbol: str
    timeframe: str
    structure_type: StructureType
    price: float
    timestamp: datetime
    trend: Trend


# ─── Composite Signal Context ───────────────────────────────────────────────

class SignalContext(BaseModel):
    """Full context for signal generation decision."""
    symbol: str
    timeframe: str
    current_price: float
    timestamp: datetime
    
    # Detection results
    liquidity_sweep: Optional[LiquiditySweepEvent] = None
    nearest_fvg: Optional[FVGData] = None
    vwap_signals: list[VWAPSignal] = []
    volume_profile: Optional[VolumeProfileData] = None
    order_flow: Optional[OrderFlowSnapshot] = None
    market_structure: Optional[MarketStructurePoint] = None
    
    # ATR for position sizing
    atr: float = 0
