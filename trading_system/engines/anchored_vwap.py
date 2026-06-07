"""
Anchored VWAP Engine

Anchors VWAP from:
- Previous day high/low
- Opening range
- Major swing high/low

Detects:
- VWAP reclaim
- VWAP rejection
- VWAP cross with volume expansion
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

import numpy as np

from trading_system.config.settings import settings
from trading_system.core.schemas import CandleData, AnchoredVWAPData, VWAPSignal

logger = logging.getLogger(__name__)


class AnchoredVWAPEngine:
    """Calculates and monitors Anchored VWAPs from significant price levels."""
    
    def __init__(self):
        self._proximity_pct = settings.signal.vwap_proximity_percent
        self._volume_expansion_threshold = settings.signal.volume_spike_threshold
        
        # Active VWAPs per symbol: {symbol: [AnchoredVWAPData]}
        self._active_vwaps: dict[str, list[AnchoredVWAPData]] = {}
    
    def calculate_vwap_from_anchor(
        self,
        candles: list[CandleData],
        anchor_time: datetime,
        anchor_price: float,
        anchor_type: str,
        symbol: str,
    ) -> Optional[AnchoredVWAPData]:
        """
        Calculate VWAP anchored from a specific time/price point.
        
        VWAP = Σ(Typical Price × Volume) / Σ(Volume)
        Typical Price = (H + L + C) / 3
        """
        # Filter candles from anchor time onward
        relevant_candles = [c for c in candles if c.timestamp >= anchor_time]
        
        if not relevant_candles:
            return None
        
        cumulative_tp_volume = 0.0
        cumulative_volume = 0
        
        for candle in relevant_candles:
            typical_price = (candle.high + candle.low + candle.close) / 3
            cumulative_tp_volume += typical_price * candle.volume
            cumulative_volume += candle.volume
        
        if cumulative_volume == 0:
            return None
        
        vwap = cumulative_tp_volume / cumulative_volume
        
        vwap_data = AnchoredVWAPData(
            symbol=symbol,
            anchor_type=anchor_type,
            anchor_time=anchor_time,
            anchor_price=anchor_price,
            current_vwap=vwap,
        )
        
        return vwap_data
    
    def calculate_all_anchors(
        self,
        candles: list[CandleData],
        prev_day_candles: list[CandleData],
        symbol: str,
    ) -> list[AnchoredVWAPData]:
        """Calculate all anchored VWAPs for a symbol."""
        vwaps = []
        
        if not candles:
            return vwaps
        
        # 1. Previous day high anchor
        if prev_day_candles:
            prev_high = max(c.high for c in prev_day_candles)
            prev_high_candle = max(prev_day_candles, key=lambda c: c.high)
            
            vwap = self.calculate_vwap_from_anchor(
                candles, prev_high_candle.timestamp, prev_high,
                "prev_day_high", symbol
            )
            if vwap:
                vwaps.append(vwap)
        
        # 2. Previous day low anchor
        if prev_day_candles:
            prev_low = min(c.low for c in prev_day_candles)
            prev_low_candle = min(prev_day_candles, key=lambda c: c.low)
            
            vwap = self.calculate_vwap_from_anchor(
                candles, prev_low_candle.timestamp, prev_low,
                "prev_day_low", symbol
            )
            if vwap:
                vwaps.append(vwap)
        
        # 3. Opening range anchor (first 15 min)
        today_candles = self._get_today_candles(candles)
        if today_candles:
            opening_candles = today_candles[:15]  # First 15 1-minute candles
            if opening_candles:
                or_high = max(c.high for c in opening_candles)
                or_low = min(c.low for c in opening_candles)
                or_mid = (or_high + or_low) / 2
                
                vwap = self.calculate_vwap_from_anchor(
                    today_candles, today_candles[0].timestamp, or_mid,
                    "opening_range", symbol
                )
                if vwap:
                    vwaps.append(vwap)
        
        # 4. Major swing high anchor
        swing_high = self._find_swing_high(candles)
        if swing_high:
            vwap = self.calculate_vwap_from_anchor(
                candles, swing_high["timestamp"], swing_high["price"],
                "swing_high", symbol
            )
            if vwap:
                vwaps.append(vwap)
        
        # 5. Major swing low anchor
        swing_low = self._find_swing_low(candles)
        if swing_low:
            vwap = self.calculate_vwap_from_anchor(
                candles, swing_low["timestamp"], swing_low["price"],
                "swing_low", symbol
            )
            if vwap:
                vwaps.append(vwap)
        
        self._active_vwaps[symbol] = vwaps
        return vwaps
    
    def detect_vwap_signals(
        self,
        candles: list[CandleData],
        vwaps: list[AnchoredVWAPData],
    ) -> list[VWAPSignal]:
        """Detect VWAP-based trade signals."""
        if len(candles) < 3:
            return []
        
        signals = []
        current = candles[-1]
        prev = candles[-2]
        prev2 = candles[-3]
        
        avg_volume = np.mean([c.volume for c in candles[-20:]]) if len(candles) >= 20 else candles[-1].volume
        
        for vwap in vwaps:
            vwap_price = vwap.current_vwap
            proximity_band = vwap_price * (self._proximity_pct / 100)
            
            # 1. VWAP Reclaim
            reclaim = self._detect_reclaim(prev, current, vwap_price)
            if reclaim:
                confidence = self._calculate_vwap_confidence(
                    current, vwap_price, avg_volume, "RECLAIM"
                )
                signals.append(VWAPSignal(
                    symbol=current.symbol,
                    anchor_type=vwap.anchor_type,
                    signal_type="RECLAIM",
                    vwap_price=vwap_price,
                    current_price=current.close,
                    confidence=confidence,
                ))
            
            # 2. VWAP Rejection
            rejection = self._detect_rejection(current, vwap_price, proximity_band)
            if rejection:
                confidence = self._calculate_vwap_confidence(
                    current, vwap_price, avg_volume, "REJECTION"
                )
                signals.append(VWAPSignal(
                    symbol=current.symbol,
                    anchor_type=vwap.anchor_type,
                    signal_type="REJECTION",
                    vwap_price=vwap_price,
                    current_price=current.close,
                    confidence=confidence,
                ))
            
            # 3. VWAP Cross with Volume Expansion
            cross = self._detect_volume_cross(prev, current, vwap_price, avg_volume)
            if cross:
                confidence = self._calculate_vwap_confidence(
                    current, vwap_price, avg_volume, "CROSS_WITH_VOLUME"
                )
                signals.append(VWAPSignal(
                    symbol=current.symbol,
                    anchor_type=vwap.anchor_type,
                    signal_type="CROSS_WITH_VOLUME",
                    vwap_price=vwap_price,
                    current_price=current.close,
                    confidence=confidence,
                ))
        
        return signals
    
    def _detect_reclaim(
        self, prev: CandleData, current: CandleData, vwap_price: float
    ) -> bool:
        """Price was below VWAP, now closes above it with conviction."""
        return (
            prev.close < vwap_price and
            current.close > vwap_price and
            current.close > current.open  # Bullish candle
        )
    
    def _detect_rejection(
        self, current: CandleData, vwap_price: float, proximity_band: float
    ) -> bool:
        """Price touches VWAP and gets rejected (wick into VWAP)."""
        candle_range = current.high - current.low
        if candle_range == 0:
            return False
        
        # Rejection from above (bearish)
        if current.low <= vwap_price + proximity_band and current.close > vwap_price:
            lower_wick = min(current.open, current.close) - current.low
            if lower_wick / candle_range > 0.5:
                return True
        
        # Rejection from below (bearish)
        if current.high >= vwap_price - proximity_band and current.close < vwap_price:
            upper_wick = current.high - max(current.open, current.close)
            if upper_wick / candle_range > 0.5:
                return True
        
        return False
    
    def _detect_volume_cross(
        self,
        prev: CandleData,
        current: CandleData,
        vwap_price: float,
        avg_volume: float,
    ) -> bool:
        """VWAP cross accompanied by above-average volume."""
        crossed = (
            (prev.close < vwap_price and current.close > vwap_price) or
            (prev.close > vwap_price and current.close < vwap_price)
        )
        
        volume_expanded = current.volume > avg_volume * self._volume_expansion_threshold
        
        return crossed and volume_expanded
    
    def _calculate_vwap_confidence(
        self,
        candle: CandleData,
        vwap_price: float,
        avg_volume: float,
        signal_type: str,
    ) -> float:
        """Calculate confidence score for VWAP signal (0-100)."""
        score = 50.0  # Base score
        
        # Volume confirmation
        if avg_volume > 0:
            vol_ratio = candle.volume / avg_volume
            score += min(vol_ratio * 10, 20)
        
        # Distance from VWAP (closer = more relevant)
        distance_pct = abs(candle.close - vwap_price) / vwap_price * 100
        if distance_pct < 0.1:
            score += 15
        elif distance_pct < 0.3:
            score += 10
        
        # Candle strength
        body = abs(candle.close - candle.open)
        range_ = candle.high - candle.low
        if range_ > 0:
            body_ratio = body / range_
            score += body_ratio * 15
        
        return min(score, 100.0)
    
    def _find_swing_high(self, candles: list[CandleData], lookback: int = 20) -> Optional[dict]:
        """Find the most recent significant swing high."""
        if len(candles) < 5:
            return None
        
        recent = candles[-lookback:] if len(candles) >= lookback else candles
        
        for i in range(2, len(recent) - 2):
            if (recent[i].high > recent[i-1].high and
                recent[i].high > recent[i-2].high and
                recent[i].high > recent[i+1].high and
                recent[i].high > recent[i+2].high):
                return {"price": recent[i].high, "timestamp": recent[i].timestamp}
        
        return None
    
    def _find_swing_low(self, candles: list[CandleData], lookback: int = 20) -> Optional[dict]:
        """Find the most recent significant swing low."""
        if len(candles) < 5:
            return None
        
        recent = candles[-lookback:] if len(candles) >= lookback else candles
        
        for i in range(2, len(recent) - 2):
            if (recent[i].low < recent[i-1].low and
                recent[i].low < recent[i-2].low and
                recent[i].low < recent[i+1].low and
                recent[i].low < recent[i+2].low):
                return {"price": recent[i].low, "timestamp": recent[i].timestamp}
        
        return None
    
    def _get_today_candles(self, candles: list[CandleData]) -> list[CandleData]:
        """Filter candles for today only."""
        today = datetime.now().date()
        return [c for c in candles if c.timestamp.date() == today]
    
    def get_active_vwaps(self, symbol: str) -> list[AnchoredVWAPData]:
        """Get currently active VWAPs for a symbol."""
        return self._active_vwaps.get(symbol, [])
