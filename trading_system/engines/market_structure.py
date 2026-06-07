"""
Market Structure Engine

Detects:
- Higher Highs (HH), Higher Lows (HL) - Bullish structure
- Lower Highs (LH), Lower Lows (LL) - Bearish structure
- Break of Structure (BOS) - Continuation
- Change of Character (CHoCH) - Potential reversal
- Swing points identification
"""
import logging
from datetime import datetime
from typing import Optional

import numpy as np

from trading_system.core.schemas import CandleData, MarketStructurePoint, StructureType, Trend

logger = logging.getLogger(__name__)


class MarketStructureEngine:
    """Analyzes market structure for trend and key level identification."""
    
    def __init__(self, swing_lookback: int = 5):
        """
        Args:
            swing_lookback: Number of candles on each side to confirm a swing point
        """
        self._swing_lookback = swing_lookback
        
        # State per symbol/timeframe
        self._swing_highs: dict[str, list[dict]] = {}
        self._swing_lows: dict[str, list[dict]] = {}
        self._structure_points: dict[str, list[MarketStructurePoint]] = {}
        self._current_trend: dict[str, Trend] = {}
    
    def analyze(
        self, candles: list[CandleData], symbol: str, timeframe: str
    ) -> list[MarketStructurePoint]:
        """
        Full market structure analysis.
        Returns new structure points detected.
        """
        key = f"{symbol}:{timeframe}"
        
        # Find swing points
        swing_highs = self._find_swing_highs(candles)
        swing_lows = self._find_swing_lows(candles)
        
        self._swing_highs[key] = swing_highs
        self._swing_lows[key] = swing_lows
        
        # Determine structure
        structure_points = self._determine_structure(
            swing_highs, swing_lows, symbol, timeframe
        )
        
        self._structure_points[key] = structure_points
        
        # Determine current trend
        self._current_trend[key] = self._get_trend(structure_points)
        
        return structure_points
    
    def _find_swing_highs(self, candles: list[CandleData]) -> list[dict]:
        """Find confirmed swing highs."""
        swings = []
        n = self._swing_lookback
        
        for i in range(n, len(candles) - n):
            is_swing = True
            for j in range(1, n + 1):
                if candles[i].high <= candles[i - j].high or candles[i].high <= candles[i + j].high:
                    is_swing = False
                    break
            
            if is_swing:
                swings.append({
                    "price": candles[i].high,
                    "timestamp": candles[i].timestamp,
                    "index": i,
                })
        
        return swings
    
    def _find_swing_lows(self, candles: list[CandleData]) -> list[dict]:
        """Find confirmed swing lows."""
        swings = []
        n = self._swing_lookback
        
        for i in range(n, len(candles) - n):
            is_swing = True
            for j in range(1, n + 1):
                if candles[i].low >= candles[i - j].low or candles[i].low >= candles[i + j].low:
                    is_swing = False
                    break
            
            if is_swing:
                swings.append({
                    "price": candles[i].low,
                    "timestamp": candles[i].timestamp,
                    "index": i,
                })
        
        return swings
    
    def _determine_structure(
        self,
        swing_highs: list[dict],
        swing_lows: list[dict],
        symbol: str,
        timeframe: str,
    ) -> list[MarketStructurePoint]:
        """Determine HH/HL/LH/LL/BOS/CHoCH structure."""
        points = []
        
        # Need at least 2 swing highs and 2 swing lows
        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return points
        
        # Analyze consecutive swing highs
        for i in range(1, len(swing_highs)):
            prev = swing_highs[i - 1]
            curr = swing_highs[i]
            
            if curr["price"] > prev["price"]:
                structure_type = StructureType.HH
            else:
                structure_type = StructureType.LH
            
            points.append(MarketStructurePoint(
                symbol=symbol,
                timeframe=timeframe,
                structure_type=structure_type,
                price=curr["price"],
                timestamp=curr["timestamp"],
                trend=Trend.BULLISH if structure_type == StructureType.HH else Trend.BEARISH,
            ))
        
        # Analyze consecutive swing lows
        for i in range(1, len(swing_lows)):
            prev = swing_lows[i - 1]
            curr = swing_lows[i]
            
            if curr["price"] > prev["price"]:
                structure_type = StructureType.HL
            else:
                structure_type = StructureType.LL
            
            points.append(MarketStructurePoint(
                symbol=symbol,
                timeframe=timeframe,
                structure_type=structure_type,
                price=curr["price"],
                timestamp=curr["timestamp"],
                trend=Trend.BULLISH if structure_type == StructureType.HL else Trend.BEARISH,
            ))
        
        # Detect BOS and CHoCH
        bos_choch = self._detect_bos_choch(swing_highs, swing_lows, symbol, timeframe)
        points.extend(bos_choch)
        
        # Sort by timestamp
        points.sort(key=lambda p: p.timestamp)
        
        return points
    
    def _detect_bos_choch(
        self,
        swing_highs: list[dict],
        swing_lows: list[dict],
        symbol: str,
        timeframe: str,
    ) -> list[MarketStructurePoint]:
        """
        Detect Break of Structure (BOS) and Change of Character (CHoCH).
        
        BOS: Continuation of existing structure
        - Bullish BOS: Price breaks above most recent swing high in uptrend
        - Bearish BOS: Price breaks below most recent swing low in downtrend
        
        CHoCH: First sign of trend change
        - Bullish CHoCH: Price breaks above swing high after series of LH/LL
        - Bearish CHoCH: Price breaks below swing low after series of HH/HL
        """
        points = []
        
        if len(swing_highs) < 3 or len(swing_lows) < 3:
            return points
        
        # Check recent structure for CHoCH
        recent_highs = swing_highs[-3:]
        recent_lows = swing_lows[-3:]
        
        # Bearish CHoCH: was making HH/HL, now broke below a swing low
        if (recent_highs[-2]["price"] > recent_highs[-3]["price"] and  # Was making HH
                recent_lows[-1]["price"] < recent_lows[-2]["price"]):  # Now made LL
            points.append(MarketStructurePoint(
                symbol=symbol,
                timeframe=timeframe,
                structure_type=StructureType.CHOCH,
                price=recent_lows[-1]["price"],
                timestamp=recent_lows[-1]["timestamp"],
                trend=Trend.BEARISH,
            ))
        
        # Bullish CHoCH: was making LH/LL, now broke above a swing high
        if (recent_lows[-2]["price"] < recent_lows[-3]["price"] and  # Was making LL
                recent_highs[-1]["price"] > recent_highs[-2]["price"]):  # Now made HH
            points.append(MarketStructurePoint(
                symbol=symbol,
                timeframe=timeframe,
                structure_type=StructureType.CHOCH,
                price=recent_highs[-1]["price"],
                timestamp=recent_highs[-1]["timestamp"],
                trend=Trend.BULLISH,
            ))
        
        return points
    
    def _get_trend(self, structure_points: list[MarketStructurePoint]) -> Trend:
        """Determine current trend from structure points."""
        if not structure_points:
            return Trend.RANGING
        
        # Look at last few structure points
        recent = structure_points[-4:]
        
        bullish_count = sum(1 for p in recent if p.trend == Trend.BULLISH)
        bearish_count = sum(1 for p in recent if p.trend == Trend.BEARISH)
        
        if bullish_count > bearish_count:
            return Trend.BULLISH
        elif bearish_count > bullish_count:
            return Trend.BEARISH
        return Trend.RANGING
    
    def get_trend(self, symbol: str, timeframe: str) -> Trend:
        """Get current market structure trend."""
        key = f"{symbol}:{timeframe}"
        return self._current_trend.get(key, Trend.RANGING)
    
    def get_swing_highs(self, symbol: str, timeframe: str) -> list[dict]:
        """Get identified swing highs."""
        key = f"{symbol}:{timeframe}"
        return self._swing_highs.get(key, [])
    
    def get_swing_lows(self, symbol: str, timeframe: str) -> list[dict]:
        """Get identified swing lows."""
        key = f"{symbol}:{timeframe}"
        return self._swing_lows.get(key, [])
    
    def get_latest_structure(self, symbol: str, timeframe: str) -> Optional[MarketStructurePoint]:
        """Get the most recent structure point."""
        key = f"{symbol}:{timeframe}"
        points = self._structure_points.get(key, [])
        return points[-1] if points else None
