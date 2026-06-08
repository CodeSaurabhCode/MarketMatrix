"""
Fair Value Gap (FVG) engine.

Detects bullish/bearish imbalances, tracks open/partial/full fill state, and
scores each FVG for institutional-quality confluence.
"""
import logging
from typing import Optional

import numpy as np

from trading_system.config.settings import settings
from trading_system.core.schemas import (
    CandleData,
    FVGData,
    FVGDirection,
    FVGStatusEnum,
    LiquiditySweepEvent,
    MarketStructurePoint,
    StructureType,
    Trend,
)

logger = logging.getLogger(__name__)


class FairValueGapEngine:
    """Detects and tracks Fair Value Gaps."""

    def __init__(self):
        self._min_gap_pct = settings.signal.fvg_min_gap_percent
        self._active_fvgs: dict[str, list[FVGData]] = {}
        self._known_fvgs: set[str] = set()

    def detect_fvg(
        self,
        candles: list[CandleData],
        market_structure: Optional[MarketStructurePoint] = None,
        liquidity_context: Optional[LiquiditySweepEvent] = None,
    ) -> list[FVGData]:
        """Detect new FVGs from the latest three-candle sequence."""
        if len(candles) < 3:
            return []

        fvg = self._detect_at_index(
            candles,
            len(candles) - 1,
            market_structure=market_structure,
            liquidity_context=liquidity_context,
        )
        if not fvg:
            return []

        key = self._fvg_key(fvg)
        if key in self._known_fvgs:
            return []

        self._known_fvgs.add(key)
        self._active_fvgs.setdefault(self._active_key(fvg.symbol, fvg.timeframe), []).append(fvg)
        return [fvg]

    def scan_full_history(
        self,
        candles: list[CandleData],
        market_structure_points: Optional[list[MarketStructurePoint]] = None,
        liquidity_sweeps: Optional[list[LiquiditySweepEvent]] = None,
    ) -> list[FVGData]:
        """Scan full candle history for FVGs and lifecycle state."""
        all_fvgs = []
        market_structure_points = market_structure_points or []
        liquidity_sweeps = liquidity_sweeps or []

        for i in range(2, len(candles)):
            ms = self._nearest_structure_context(candles[i].timestamp, market_structure_points)
            sweep = self._nearest_liquidity_context(candles[i].timestamp, liquidity_sweeps)
            fvg = self._detect_at_index(candles, i, market_structure=ms, liquidity_context=sweep)
            if fvg:
                all_fvgs.append(fvg)

        return self._update_fill_status(all_fvgs, candles)

    def update_fvgs(self, candles: list[CandleData]) -> list[FVGData]:
        """
        Update active FVG fill state.

        Returns FVGs that received their first retest on the latest candle.
        """
        if not candles:
            return []

        key = self._active_key(candles[-1].symbol, candles[-1].timeframe)
        latest = candles[-1]
        active = self._active_fvgs.get(key, [])
        retested = []
        still_active = []

        for fvg in active:
            previous_fill = fvg.fill_percent
            self._apply_fill(fvg, latest)
            if previous_fill == 0 and fvg.fill_percent > 0 and fvg.first_retest_time == latest.timestamp:
                retested.append(fvg)
            if fvg.status != FVGStatusEnum.FULLY_FILLED:
                still_active.append(fvg)

        self._active_fvgs[key] = still_active
        return retested

    def _detect_at_index(
        self,
        candles: list[CandleData],
        index: int,
        market_structure: Optional[MarketStructurePoint] = None,
        liquidity_context: Optional[LiquiditySweepEvent] = None,
    ) -> Optional[FVGData]:
        c1 = candles[index - 2]
        c2 = candles[index - 1]
        c3 = candles[index]

        if c3.low > c1.high and c2.close > c2.open:
            gap_low = c1.high
            gap_high = c3.low
            direction = FVGDirection.BULLISH
        elif c3.high < c1.low and c2.close < c2.open:
            gap_low = c3.high
            gap_high = c1.low
            direction = FVGDirection.BEARISH
        else:
            return None

        gap_size_pct = ((gap_high - gap_low) / gap_low) * 100 if gap_low else 0
        if gap_size_pct < self._min_gap_pct:
            return None

        fvg = FVGData(
            symbol=c1.symbol,
            timeframe=c1.timeframe,
            direction=direction,
            gap_high=float(gap_high),
            gap_low=float(gap_low),
            gap_size_percent=float(gap_size_pct),
            formation_time=c2.timestamp,
            volume_at_formation=c2.volume,
            quality_score=0,
        )

        self._score_fvg(
            fvg=fvg,
            candles=candles[: index + 1],
            formation_index=index - 1,
            market_structure=market_structure,
            liquidity_context=liquidity_context,
        )
        return fvg

    def _score_fvg(
        self,
        fvg: FVGData,
        candles: list[CandleData],
        formation_index: int,
        market_structure: Optional[MarketStructurePoint],
        liquidity_context: Optional[LiquiditySweepEvent],
    ) -> None:
        impulse = candles[formation_index]
        atr = self._atr(candles[: formation_index + 1])
        avg_volume = np.mean([c.volume for c in candles[-20:]]) if candles else 0

        gap_score = min(fvg.gap_size_percent / 0.5, 1.0) * 20
        displacement_score = self._impulse_displacement_score(impulse, atr, fvg.direction)
        volume_score = 0
        if avg_volume > 0 and fvg.volume_at_formation:
            volume_score = min((fvg.volume_at_formation / avg_volume) / 2.0, 1.0) * 15

        trend_score = self._trend_alignment_score(fvg, candles, market_structure)
        liquidity_score = self._liquidity_context_score(fvg, liquidity_context)
        structure_score = self._structure_context_score(fvg, market_structure)

        fvg.displacement_score = displacement_score
        fvg.trend_alignment_score = trend_score
        fvg.liquidity_context_score = liquidity_score
        fvg.structure_context_score = structure_score
        fvg.quality_score = float(
            min(
                gap_score
                + displacement_score * 0.25
                + volume_score
                + trend_score
                + liquidity_score
                + structure_score,
                100,
            )
        )
        fvg.score_breakdown = {
            "gap_size": round(gap_score, 2),
            "displacement": round(displacement_score * 0.25, 2),
            "volume": round(volume_score, 2),
            "trend_alignment": round(trend_score, 2),
            "liquidity_context": round(liquidity_score, 2),
            "market_structure_context": round(structure_score, 2),
            "formation_index": formation_index,
        }

    def _impulse_displacement_score(
        self, candle: CandleData, atr: float, direction: FVGDirection
    ) -> float:
        candle_range = candle.high - candle.low
        if candle_range <= 0:
            return 0

        body = abs(candle.close - candle.open)
        body_ratio = body / candle_range
        range_ratio = candle_range / atr if atr > 0 else 1

        if direction == FVGDirection.BULLISH:
            close_location = (candle.close - candle.low) / candle_range
        else:
            close_location = (candle.high - candle.close) / candle_range

        return float(
            min(
                min(body_ratio / 0.55, 1.0) * 40
                + min(range_ratio / 1.2, 1.0) * 35
                + min(close_location, 1.0) * 25,
                100,
            )
        )

    def _trend_alignment_score(
        self,
        fvg: FVGData,
        candles: list[CandleData],
        market_structure: Optional[MarketStructurePoint],
    ) -> float:
        if market_structure:
            if fvg.direction == FVGDirection.BULLISH and market_structure.trend == Trend.BULLISH:
                return 15
            if fvg.direction == FVGDirection.BEARISH and market_structure.trend == Trend.BEARISH:
                return 15
            if market_structure.trend == Trend.RANGING:
                return 6
            return 0

        trend = self._determine_trend(candles[-20:] if len(candles) >= 20 else candles)
        if fvg.direction == FVGDirection.BULLISH and trend == Trend.BULLISH:
            return 12
        if fvg.direction == FVGDirection.BEARISH and trend == Trend.BEARISH:
            return 12
        return 6 if trend == Trend.RANGING else 0

    def _liquidity_context_score(
        self, fvg: FVGData, liquidity_context: Optional[LiquiditySweepEvent]
    ) -> float:
        if not liquidity_context:
            return 0
        bullish_match = (
            fvg.direction == FVGDirection.BULLISH and liquidity_context.zone_type == "SELL_SIDE"
        )
        bearish_match = (
            fvg.direction == FVGDirection.BEARISH and liquidity_context.zone_type == "BUY_SIDE"
        )
        if bullish_match or bearish_match:
            return min(liquidity_context.sweep_strength / 100, 1.0) * 15
        return 0

    def _structure_context_score(
        self, fvg: FVGData, market_structure: Optional[MarketStructurePoint]
    ) -> float:
        if not market_structure:
            return 0
        if market_structure.structure_type in {StructureType.BOS, StructureType.CHOCH, StructureType.MSS}:
            aligned = (
                fvg.direction == FVGDirection.BULLISH and market_structure.trend == Trend.BULLISH
            ) or (
                fvg.direction == FVGDirection.BEARISH and market_structure.trend == Trend.BEARISH
            )
            if aligned:
                return min(10 + market_structure.displacement_score * 0.05, 15)
        return 0

    def _apply_fill(self, fvg: FVGData, candle: CandleData) -> None:
        if candle.timestamp <= fvg.formation_time:
            return

        fill_percent = self._fill_percent(fvg, candle)
        if fill_percent <= fvg.fill_percent:
            return

        fvg.fill_percent = fill_percent
        if fvg.first_retest_time is None and fill_percent > 0:
            fvg.first_retest_time = candle.timestamp

        if fill_percent >= 100:
            fvg.status = FVGStatusEnum.FULLY_FILLED
            fvg.filled_at = candle.timestamp
        elif fill_percent > 0:
            fvg.status = FVGStatusEnum.PARTIALLY_FILLED

    def _fill_percent(self, fvg: FVGData, candle: CandleData) -> float:
        gap_size = fvg.gap_high - fvg.gap_low
        if gap_size <= 0:
            return 100

        if fvg.direction == FVGDirection.BULLISH:
            if candle.low >= fvg.gap_high:
                return 0
            if candle.low <= fvg.gap_low:
                return 100
            return float(((fvg.gap_high - candle.low) / gap_size) * 100)

        if candle.high <= fvg.gap_low:
            return 0
        if candle.high >= fvg.gap_high:
            return 100
        return float(((candle.high - fvg.gap_low) / gap_size) * 100)

    def _update_fill_status(
        self, fvgs: list[FVGData], candles: list[CandleData]
    ) -> list[FVGData]:
        for fvg in fvgs:
            for candle in candles:
                self._apply_fill(fvg, candle)
                if fvg.status == FVGStatusEnum.FULLY_FILLED:
                    break
        return fvgs

    def _nearest_structure_context(
        self,
        timestamp,
        points: list[MarketStructurePoint],
    ) -> Optional[MarketStructurePoint]:
        previous = [p for p in points if p.timestamp <= timestamp]
        return previous[-1] if previous else None

    def _nearest_liquidity_context(
        self,
        timestamp,
        sweeps: list[LiquiditySweepEvent],
    ) -> Optional[LiquiditySweepEvent]:
        previous = [s for s in sweeps if s.sweep_time <= timestamp]
        return previous[-1] if previous else None

    def _determine_trend(self, candles: list[CandleData]) -> Trend:
        if len(candles) < 5:
            return Trend.RANGING

        closes = [c.close for c in candles]
        mid = len(closes) // 2
        first_half_avg = np.mean(closes[:mid])
        second_half_avg = np.mean(closes[mid:])
        if first_half_avg == 0:
            return Trend.RANGING

        change_pct = (second_half_avg - first_half_avg) / first_half_avg * 100
        if change_pct > 0.2:
            return Trend.BULLISH
        if change_pct < -0.2:
            return Trend.BEARISH
        return Trend.RANGING

    def _atr(self, candles: list[CandleData], period: int = 14) -> float:
        if not candles:
            return 0
        true_ranges = []
        for i, candle in enumerate(candles):
            if i == 0:
                true_ranges.append(candle.high - candle.low)
            else:
                prev_close = candles[i - 1].close
                true_ranges.append(
                    max(
                        candle.high - candle.low,
                        abs(candle.high - prev_close),
                        abs(candle.low - prev_close),
                    )
                )
        return float(np.mean(true_ranges[-period:]))

    def _active_key(self, symbol: str, timeframe: str) -> str:
        return f"{symbol}:{timeframe}"

    def _fvg_key(self, fvg: FVGData) -> str:
        return (
            f"{fvg.symbol}:{fvg.timeframe}:{fvg.direction.value}:"
            f"{fvg.formation_time.isoformat()}:{fvg.gap_low:.6f}:{fvg.gap_high:.6f}"
        )

    def get_nearest_fvg(
        self, symbol: str, price: float, direction: Optional[FVGDirection] = None
    ) -> Optional[FVGData]:
        candidates = []
        for key, fvgs in self._active_fvgs.items():
            if not key.startswith(f"{symbol}:"):
                continue
            candidates.extend(fvgs)

        if direction:
            candidates = [f for f in candidates if f.direction == direction]
        candidates = [f for f in candidates if f.status != FVGStatusEnum.FULLY_FILLED]
        if not candidates:
            return None

        return min(candidates, key=lambda fvg: abs(price - fvg.midpoint))

    def get_active_fvgs(self, symbol: str) -> list[FVGData]:
        active = []
        for key, fvgs in self._active_fvgs.items():
            if key.startswith(f"{symbol}:"):
                active.extend([f for f in fvgs if f.status != FVGStatusEnum.FULLY_FILLED])
        return active


class FairValueGapDetector(FairValueGapEngine):
    """Backward-compatible name used by the existing orchestrator and tests."""
