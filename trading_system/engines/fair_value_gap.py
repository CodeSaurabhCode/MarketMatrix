"""
Fair Value Gap (FVG) Detector

Identifies:
- Bullish FVGs (gap between candle 1 high and candle 3 low)
- Bearish FVGs (gap between candle 1 low and candle 3 high)
- FVG fill tracking (open, partially filled, fully filled)
- First retest detection
- Quality scoring
"""
import logging
from datetime import datetime
from typing import Optional

import numpy as np

from trading_system.config.settings import settings
from trading_system.core.schemas import CandleData, FVGData, FVGDirection, FVGStatusEnum, Trend

logger = logging.getLogger(__name__)


class FairValueGapDetector:
    """Detects and tracks Fair Value Gaps."""
    
    def __init__(self):
        self._min_gap_pct = settings.signal.fvg_min_gap_percent
        
        # Active FVGs: {symbol: [FVGData]}
        self._active_fvgs: dict[str, list[FVGData]] = {}
    
    def detect_fvg(self, candles: list[CandleData]) -> list[FVGData]:
        """
        Detect new Fair Value Gaps in the candle series.
        
        Bullish FVG: Candle 3 low > Candle 1 high (gap up)
        Bearish FVG: Candle 3 high < Candle 1 low (gap down)
        
        Requires at least 3 candles.
        """
        if len(candles) < 3:
            return []
        
        new_fvgs = []
        symbol = candles[0].symbol
        timeframe = candles[0].timeframe
        
        # Check last 3 candles for new FVG
        c1 = candles[-3]
        c2 = candles[-2]  # The impulse candle
        c3 = candles[-1]
        
        # Bullish FVG: Gap between candle 1 high and candle 3 low
        if c3.low > c1.high:
            gap_high = c3.low
            gap_low = c1.high
            gap_size_pct = ((gap_high - gap_low) / gap_low) * 100
            
            if gap_size_pct >= self._min_gap_pct:
                fvg = FVGData(
                    symbol=symbol,
                    timeframe=timeframe,
                    direction=FVGDirection.BULLISH,
                    gap_high=gap_high,
                    gap_low=gap_low,
                    gap_size_percent=gap_size_pct,
                    formation_time=c2.timestamp,
                    volume_at_formation=c2.volume,
                    quality_score=0,  # Calculated below
                )
                fvg.quality_score = self._calculate_quality(fvg, candles)
                new_fvgs.append(fvg)
        
        # Bearish FVG: Gap between candle 3 high and candle 1 low
        if c3.high < c1.low:
            gap_high = c1.low
            gap_low = c3.high
            gap_size_pct = ((gap_high - gap_low) / gap_low) * 100
            
            if gap_size_pct >= self._min_gap_pct:
                fvg = FVGData(
                    symbol=symbol,
                    timeframe=timeframe,
                    direction=FVGDirection.BEARISH,
                    gap_high=gap_high,
                    gap_low=gap_low,
                    gap_size_percent=gap_size_pct,
                    formation_time=c2.timestamp,
                    volume_at_formation=c2.volume,
                    quality_score=0,
                )
                fvg.quality_score = self._calculate_quality(fvg, candles)
                new_fvgs.append(fvg)
        
        # Add to active tracking
        if symbol not in self._active_fvgs:
            self._active_fvgs[symbol] = []
        self._active_fvgs[symbol].extend(new_fvgs)
        
        return new_fvgs
    
    def scan_full_history(self, candles: list[CandleData]) -> list[FVGData]:
        """Scan full candle history for all FVGs."""
        all_fvgs = []
        
        for i in range(2, len(candles)):
            c1 = candles[i - 2]
            c2 = candles[i - 1]
            c3 = candles[i]
            
            symbol = c1.symbol
            timeframe = c1.timeframe
            
            # Bullish FVG
            if c3.low > c1.high:
                gap_size_pct = ((c3.low - c1.high) / c1.high) * 100
                if gap_size_pct >= self._min_gap_pct:
                    fvg = FVGData(
                        symbol=symbol,
                        timeframe=timeframe,
                        direction=FVGDirection.BULLISH,
                        gap_high=c3.low,
                        gap_low=c1.high,
                        gap_size_percent=gap_size_pct,
                        formation_time=c2.timestamp,
                        volume_at_formation=c2.volume,
                        quality_score=0,
                    )
                    all_fvgs.append(fvg)
            
            # Bearish FVG
            if c3.high < c1.low:
                gap_size_pct = ((c1.low - c3.high) / c3.high) * 100
                if gap_size_pct >= self._min_gap_pct:
                    fvg = FVGData(
                        symbol=symbol,
                        timeframe=timeframe,
                        direction=FVGDirection.BEARISH,
                        gap_high=c1.low,
                        gap_low=c3.high,
                        gap_size_percent=gap_size_pct,
                        formation_time=c2.timestamp,
                        volume_at_formation=c2.volume,
                        quality_score=0,
                    )
                    all_fvgs.append(fvg)
        
        # Calculate quality and track fill status
        for fvg in all_fvgs:
            fvg.quality_score = self._calculate_quality(fvg, candles)
        
        # Update fill status based on subsequent price action
        all_fvgs = self._update_fill_status(all_fvgs, candles)
        
        return all_fvgs
    
    def update_fvgs(self, candles: list[CandleData]) -> list[FVGData]:
        """
        Update fill status of active FVGs with latest price.
        Returns FVGs that just got their first retest.
        """
        if not candles:
            return []
        
        symbol = candles[0].symbol
        latest = candles[-1]
        retested_fvgs = []
        
        active = self._active_fvgs.get(symbol, [])
        still_active = []
        
        for fvg in active:
            status = self._check_fill(fvg, latest)
            
            if status == "FIRST_RETEST":
                retested_fvgs.append(fvg)
                still_active.append(fvg)
            elif status == "OPEN":
                still_active.append(fvg)
            elif status == "PARTIALLY_FILLED":
                still_active.append(fvg)
            # FULLY_FILLED => remove from active
        
        self._active_fvgs[symbol] = still_active
        return retested_fvgs
    
    def _check_fill(self, fvg: FVGData, candle: CandleData) -> str:
        """Check if a candle fills an FVG."""
        if fvg.direction == FVGDirection.BULLISH:
            # Bullish FVG: gap between gap_low and gap_high
            # Filled when price drops into the gap
            if candle.low <= fvg.gap_low:
                return "FULLY_FILLED"
            elif candle.low <= fvg.gap_high:
                # Price entered the gap zone
                midpoint = (fvg.gap_high + fvg.gap_low) / 2
                if candle.low <= midpoint:
                    return "PARTIALLY_FILLED"
                return "FIRST_RETEST"
        
        elif fvg.direction == FVGDirection.BEARISH:
            # Bearish FVG: gap between gap_low and gap_high
            # Filled when price rises into the gap
            if candle.high >= fvg.gap_high:
                return "FULLY_FILLED"
            elif candle.high >= fvg.gap_low:
                midpoint = (fvg.gap_high + fvg.gap_low) / 2
                if candle.high >= midpoint:
                    return "PARTIALLY_FILLED"
                return "FIRST_RETEST"
        
        return "OPEN"
    
    def _calculate_quality(self, fvg: FVGData, candles: list[CandleData]) -> float:
        """
        Calculate FVG quality score (0-100).
        
        Factors:
        - Gap size (larger = higher quality for institutional moves)
        - Volume at formation (higher = more conviction)
        - Trend alignment
        - Impulse candle body ratio
        """
        score = 0.0
        
        # 1. Gap size contribution (0-25)
        gap_score = min(fvg.gap_size_percent / 0.5, 1.0) * 25
        score += gap_score
        
        # 2. Volume contribution (0-25)
        if candles and fvg.volume_at_formation:
            avg_volume = np.mean([c.volume for c in candles[-20:]]) if len(candles) >= 20 else np.mean([c.volume for c in candles])
            if avg_volume > 0:
                vol_ratio = fvg.volume_at_formation / avg_volume
                vol_score = min(vol_ratio / 2.0, 1.0) * 25
                score += vol_score
        
        # 3. Trend alignment (0-25)
        trend = self._determine_trend(candles[-20:] if len(candles) >= 20 else candles)
        if (fvg.direction == FVGDirection.BULLISH and trend == Trend.BULLISH) or \
           (fvg.direction == FVGDirection.BEARISH and trend == Trend.BEARISH):
            score += 25
        elif trend == Trend.RANGING:
            score += 12
        
        # 4. Impulse candle body ratio (0-25)
        # Find the impulse candle (c2 in the 3-candle pattern)
        formation_candles = [c for c in candles if c.timestamp == fvg.formation_time]
        if formation_candles:
            impulse = formation_candles[0]
            body = abs(impulse.close - impulse.open)
            range_ = impulse.high - impulse.low
            if range_ > 0:
                body_ratio = body / range_
                score += body_ratio * 25
        
        return min(score, 100.0)
    
    def _determine_trend(self, candles: list[CandleData]) -> Trend:
        """Simple trend determination using closes."""
        if len(candles) < 5:
            return Trend.RANGING
        
        closes = [c.close for c in candles]
        
        # Compare first half vs second half average
        mid = len(closes) // 2
        first_half_avg = np.mean(closes[:mid])
        second_half_avg = np.mean(closes[mid:])
        
        change_pct = (second_half_avg - first_half_avg) / first_half_avg * 100
        
        if change_pct > 0.2:
            return Trend.BULLISH
        elif change_pct < -0.2:
            return Trend.BEARISH
        return Trend.RANGING
    
    def _update_fill_status(
        self, fvgs: list[FVGData], candles: list[CandleData]
    ) -> list[FVGData]:
        """Update fill status for historical FVG analysis."""
        for fvg in fvgs:
            # Check all candles after formation
            post_formation = [c for c in candles if c.timestamp > fvg.formation_time]
            for candle in post_formation:
                status = self._check_fill(fvg, candle)
                if status == "FULLY_FILLED":
                    break
        
        return fvgs
    
    def get_nearest_fvg(
        self, symbol: str, price: float, direction: Optional[FVGDirection] = None
    ) -> Optional[FVGData]:
        """Get the nearest unfilled FVG to current price."""
        active = self._active_fvgs.get(symbol, [])
        
        if direction:
            active = [f for f in active if f.direction == direction]
        
        if not active:
            return None
        
        # Sort by distance to current price
        def distance(fvg: FVGData) -> float:
            mid = (fvg.gap_high + fvg.gap_low) / 2
            return abs(price - mid)
        
        return min(active, key=distance)
    
    def get_active_fvgs(self, symbol: str) -> list[FVGData]:
        """Get all active (unfilled) FVGs for a symbol."""
        return self._active_fvgs.get(symbol, [])
