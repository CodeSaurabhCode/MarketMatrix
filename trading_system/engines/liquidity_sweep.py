"""
Liquidity Sweep Detector

Detects:
- Equal highs/lows (liquidity pools)
- Stop hunts above resistance / below support
- Candle close rejection confirmation
- Buy-side and sell-side liquidity zones
"""
import logging
from datetime import datetime
from typing import Optional

import numpy as np

from trading_system.config.settings import settings
from trading_system.core.schemas import (
    CandleData, LiquidityZoneData, LiquiditySweepEvent, Direction
)

logger = logging.getLogger(__name__)


class LiquiditySweepDetector:
    """Detects liquidity sweeps and manages liquidity zones."""
    
    def __init__(self):
        self._tolerance = settings.signal.equal_level_tolerance
        self._min_wick_ratio = settings.signal.sweep_min_wick_ratio
        self._lookback = settings.signal.lookback_periods
    
    def detect_equal_levels(
        self, candles: list[CandleData], min_touches: int = 3
    ) -> tuple[list[LiquidityZoneData], list[LiquidityZoneData]]:
        """
        Detect equal highs (buy-side liquidity) and equal lows (sell-side liquidity).
        
        Returns:
            Tuple of (buy_side_zones, sell_side_zones)
        """
        if len(candles) < self._lookback:
            return [], []
        
        highs = np.array([c.high for c in candles[-self._lookback:]])
        lows = np.array([c.low for c in candles[-self._lookback:]])
        
        buy_side_zones = self._find_equal_levels(
            candles[-self._lookback:], highs, "BUY_SIDE", min_touches
        )
        sell_side_zones = self._find_equal_levels(
            candles[-self._lookback:], lows, "SELL_SIDE", min_touches
        )
        
        return buy_side_zones, sell_side_zones
    
    def _find_equal_levels(
        self,
        candles: list[CandleData],
        prices: np.ndarray,
        zone_type: str,
        min_touches: int,
    ) -> list[LiquidityZoneData]:
        """Find price levels that have been tested multiple times."""
        zones = []
        used_indices = set()
        
        for i in range(len(prices)):
            if i in used_indices:
                continue
            
            level = prices[i]
            tolerance = level * self._tolerance
            
            # Find all prices within tolerance of this level
            matches = np.where(np.abs(prices - level) <= tolerance)[0]
            
            if len(matches) >= min_touches:
                avg_price = float(np.mean(prices[matches]))
                
                zones.append(LiquidityZoneData(
                    symbol=candles[0].symbol,
                    timeframe=candles[0].timeframe,
                    zone_type=zone_type,
                    price_level=avg_price,
                    touch_count=len(matches),
                    formed_at=candles[matches[0]].timestamp,
                ))
                
                used_indices.update(matches.tolist())
        
        return zones
    
    def detect_sweep(
        self,
        candles: list[CandleData],
        liquidity_zones: list[LiquidityZoneData],
    ) -> list[LiquiditySweepEvent]:
        """
        Detect if the latest candle(s) swept any liquidity zone.
        
        A sweep occurs when price exceeds the level but closes back below/above it
        (indicating rejection and potential reversal).
        """
        if len(candles) < 2:
            return []
        
        sweeps = []
        latest = candles[-1]
        
        for zone in liquidity_zones:
            if zone.zone_type == "BUY_SIDE":
                # Buy-side liquidity is above price (equal highs)
                # Sweep = price goes above the level but closes below it
                if latest.high > zone.price_level and latest.close < zone.price_level:
                    sweep = self._validate_sweep(latest, zone, "BUY_SIDE")
                    if sweep:
                        sweeps.append(sweep)
            
            elif zone.zone_type == "SELL_SIDE":
                # Sell-side liquidity is below price (equal lows)
                # Sweep = price goes below the level but closes above it
                if latest.low < zone.price_level and latest.close > zone.price_level:
                    sweep = self._validate_sweep(latest, zone, "SELL_SIDE")
                    if sweep:
                        sweeps.append(sweep)
        
        return sweeps
    
    def _validate_sweep(
        self,
        candle: CandleData,
        zone: LiquidityZoneData,
        zone_type: str,
    ) -> Optional[LiquiditySweepEvent]:
        """Validate sweep quality using wick analysis."""
        candle_range = candle.high - candle.low
        if candle_range == 0:
            return None
        
        if zone_type == "BUY_SIDE":
            # Upper wick should be significant (stop hunt above)
            wick = candle.high - max(candle.open, candle.close)
            sweep_price = candle.high
            rejection = candle.close < zone.price_level
        else:
            # Lower wick should be significant (stop hunt below)
            wick = min(candle.open, candle.close) - candle.low
            sweep_price = candle.low
            rejection = candle.close > zone.price_level
        
        wick_ratio = wick / candle_range
        
        if wick_ratio < self._min_wick_ratio * 0.5:  # Relaxed for detection, scored later
            return None
        
        # Calculate sweep strength (0-100)
        strength = self._calculate_sweep_strength(
            wick_ratio=wick_ratio,
            touch_count=zone.touch_count,
            rejection_confirmed=rejection,
            penetration_depth=abs(sweep_price - zone.price_level),
            zone_price=zone.price_level,
        )
        
        return LiquiditySweepEvent(
            symbol=candle.symbol,
            timeframe=candle.timeframe,
            zone_type=zone_type,
            price_level=zone.price_level,
            sweep_price=sweep_price,
            sweep_time=candle.timestamp,
            sweep_strength=strength,
            candle_close=candle.close,
            rejection_confirmed=rejection,
        )
    
    def _calculate_sweep_strength(
        self,
        wick_ratio: float,
        touch_count: int,
        rejection_confirmed: bool,
        penetration_depth: float,
        zone_price: float,
    ) -> float:
        """
        Calculate sweep strength score (0-100).
        
        Factors:
        - Wick ratio (larger wick = stronger rejection)
        - Number of touches at level (more touches = more liquidity)
        - Whether price closed back inside (rejection confirmation)
        - Depth of penetration beyond the level
        """
        score = 0.0
        
        # Wick ratio contribution (0-30)
        score += min(wick_ratio / self._min_wick_ratio, 1.0) * 30
        
        # Touch count contribution (0-25)
        touch_score = min(touch_count / 5, 1.0) * 25
        score += touch_score
        
        # Rejection confirmation (0-25)
        if rejection_confirmed:
            score += 25
        
        # Penetration depth (0-20) - deeper sweep = more stops taken
        penetration_pct = (penetration_depth / zone_price) * 100
        penetration_score = min(penetration_pct / 0.5, 1.0) * 20  # Max at 0.5% penetration
        score += penetration_score
        
        return min(score, 100.0)
    
    def detect_stop_hunt(
        self,
        candles: list[CandleData],
        support_level: float,
        resistance_level: float,
    ) -> Optional[LiquiditySweepEvent]:
        """
        Detect stop hunt pattern:
        Price briefly breaks S/R level then reverses sharply.
        """
        if len(candles) < 3:
            return None
        
        latest = candles[-1]
        
        # Check for stop hunt above resistance
        if latest.high > resistance_level and latest.close < resistance_level:
            wick = latest.high - max(latest.open, latest.close)
            body = abs(latest.close - latest.open)
            
            if body > 0 and wick / body > 1.5:  # Wick is 1.5x body
                return LiquiditySweepEvent(
                    symbol=latest.symbol,
                    timeframe=latest.timeframe,
                    zone_type="BUY_SIDE",
                    price_level=resistance_level,
                    sweep_price=latest.high,
                    sweep_time=latest.timestamp,
                    sweep_strength=75.0,
                    candle_close=latest.close,
                    rejection_confirmed=True,
                )
        
        # Check for stop hunt below support
        if latest.low < support_level and latest.close > support_level:
            wick = min(latest.open, latest.close) - latest.low
            body = abs(latest.close - latest.open)
            
            if body > 0 and wick / body > 1.5:
                return LiquiditySweepEvent(
                    symbol=latest.symbol,
                    timeframe=latest.timeframe,
                    zone_type="SELL_SIDE",
                    price_level=support_level,
                    sweep_price=latest.low,
                    sweep_time=latest.timestamp,
                    sweep_strength=75.0,
                    candle_close=latest.close,
                    rejection_confirmed=True,
                )
        
        return None
