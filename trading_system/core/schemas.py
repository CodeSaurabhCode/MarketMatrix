"""
Pydantic schemas for data transfer objects.
"""
from datetime import datetime
from enum import Enum
from typing import Optional

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
    HH = "HH"
    HL = "HL"
    LH = "LH"
    LL = "LL"
    BOS = "BOS"
    CHOCH = "CHOCH"
    MSS = "MSS"
    DISPLACEMENT = "DISPLACEMENT"


class StructureScope(str, Enum):
    INTERNAL = "INTERNAL"
    EXTERNAL = "EXTERNAL"
    EXECUTION = "EXECUTION"


class Trend(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    RANGING = "RANGING"


class LiquidityPoolType(str, Enum):
    EQUAL_HIGH = "EQUAL_HIGH"
    EQUAL_LOW = "EQUAL_LOW"
    TRIPLE_TOP = "TRIPLE_TOP"
    TRIPLE_BOTTOM = "TRIPLE_BOTTOM"
    SESSION_HIGH = "SESSION_HIGH"
    SESSION_LOW = "SESSION_LOW"
    PREVIOUS_DAY_HIGH = "PREVIOUS_DAY_HIGH"
    PREVIOUS_DAY_LOW = "PREVIOUS_DAY_LOW"
    WEEKLY_HIGH = "WEEKLY_HIGH"
    WEEKLY_LOW = "WEEKLY_LOW"


class LiquiditySide(str, Enum):
    BUY_SIDE = "BUY_SIDE"
    SELL_SIDE = "SELL_SIDE"


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


class SwingPointData(BaseModel):
    symbol: str
    timeframe: str
    scope: StructureScope
    kind: str
    price: float
    timestamp: datetime
    index: int
    label: Optional[StructureType] = None
    strength: float = 0


class MarketStructurePoint(BaseModel):
    symbol: str
    timeframe: str
    structure_type: StructureType
    price: float
    timestamp: datetime
    trend: Trend
    scope: StructureScope = StructureScope.EXTERNAL
    direction: Optional[Direction] = None
    broken_level: Optional[float] = None
    break_price: Optional[float] = None
    protected_high: Optional[float] = None
    protected_low: Optional[float] = None
    displacement_score: float = 0
    strength_score: float = 0
    source_index: Optional[int] = None
    broken_index: Optional[int] = None
    metadata: dict = Field(default_factory=dict)


class SignalCreate(BaseModel):
    symbol: str
    direction: Direction
    entry_zone_low: float
    entry_zone_high: float
    stop_loss: float
    target_1: float
    target_2: Optional[float] = None
    confidence_score: float
    market_structure_score: float = 0
    liquidity_sweep_score: float = 0
    fvg_score: float = 0
    vwap_score: float = 0
    volume_profile_score: float = 0
    order_flow_score: float = 0
    volume_confirmation_score: float = 0
    reasoning: Optional[str] = None
    timeframe: Optional[str] = None
    explainability: dict = Field(default_factory=dict)


class SignalResponse(SignalCreate):
    id: int
    notified: bool
    created_at: datetime
    outcome: Optional[str] = None

    class Config:
        from_attributes = True


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
    status: FVGStatusEnum = FVGStatusEnum.OPEN
    fill_percent: float = 0
    first_retest_time: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    displacement_score: float = 0
    trend_alignment_score: float = 0
    liquidity_context_score: float = 0
    structure_context_score: float = 0
    score_breakdown: dict = Field(default_factory=dict)

    @property
    def midpoint(self) -> float:
        return (self.gap_high + self.gap_low) / 2


class LiquidityZoneData(BaseModel):
    symbol: str
    timeframe: str
    zone_type: str
    price_level: float
    touch_count: int = 1
    formed_at: datetime
    pool_id: Optional[str] = None
    liquidity_type: Optional[str] = None
    upper_bound: Optional[float] = None
    lower_bound: Optional[float] = None
    strength_score: float = 0
    age_candles: int = 0
    swept: bool = False


class LiquidityPoolData(BaseModel):
    pool_id: str
    symbol: str
    timeframe: str
    zone_type: str
    liquidity_type: LiquidityPoolType
    price_level: float
    upper_bound: float
    lower_bound: float
    touch_count: int
    formed_at: datetime
    last_touched_at: datetime
    age_candles: int
    strength_score: float
    swept: bool = False
    swept_at: Optional[datetime] = None
    source_indices: list[int] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class LiquiditySweepEvent(BaseModel):
    symbol: str
    timeframe: str
    zone_type: str
    price_level: float
    sweep_price: float
    sweep_time: datetime
    sweep_strength: float
    candle_close: float
    rejection_confirmed: bool
    pool_id: Optional[str] = None
    liquidity_type: Optional[str] = None
    pool_strength_score: float = 0
    pool_age_candles: int = 0
    displacement_score: float = 0
    rejection_score: float = 0
    breach_depth_percent: float = 0
    volume_score: float = 0
    reasons: list[str] = Field(default_factory=list)


class VolumeProfileData(BaseModel):
    symbol: str
    timeframe: str
    session_date: datetime
    poc_price: float
    value_area_high: float
    value_area_low: float
    high_volume_nodes: list[dict] = Field(default_factory=list)
    low_volume_nodes: list[dict] = Field(default_factory=list)
    total_volume: int = 0


class AnchoredVWAPData(BaseModel):
    symbol: str
    anchor_type: str
    anchor_time: datetime
    anchor_price: float
    current_vwap: float


class VWAPSignal(BaseModel):
    symbol: str
    anchor_type: str
    signal_type: str
    vwap_price: float
    current_price: float
    confidence: float


class OrderFlowSnapshot(BaseModel):
    symbol: str
    timestamp: datetime
    volume_delta: float
    aggressive_buy_volume: int = 0
    aggressive_sell_volume: int = 0
    relative_volume: float = 1.0
    large_trade_detected: bool = False
    cumulative_delta: float = 0


class SignalContext(BaseModel):
    """Full context for signal generation decision."""

    symbol: str
    timeframe: str
    current_price: float
    timestamp: datetime
    liquidity_sweep: Optional[LiquiditySweepEvent] = None
    nearest_fvg: Optional[FVGData] = None
    vwap_signals: list[VWAPSignal] = Field(default_factory=list)
    volume_profile: Optional[VolumeProfileData] = None
    order_flow: Optional[OrderFlowSnapshot] = None
    market_structure: Optional[MarketStructurePoint] = None
    higher_timeframe_structure: Optional[MarketStructurePoint] = None
    execution_structure: Optional[MarketStructurePoint] = None
    atr: float = 0
