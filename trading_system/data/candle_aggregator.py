"""
Candle aggregator - builds OHLCV candles from real-time ticks.
"""
import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional, Callable, Awaitable

from trading_system.core.schemas import TickData, CandleData

logger = logging.getLogger(__name__)


class CandleAggregator:
    """Aggregates ticks into OHLCV candles for multiple timeframes."""
    
    def __init__(self, timeframes: list[int] = None):
        """
        Args:
            timeframes: List of timeframe intervals in minutes [1, 5, 15]
        """
        self._timeframes = timeframes or [1, 5, 15]
        
        # {symbol: {timeframe_minutes: {open, high, low, close, volume, start_time}}}
        self._building: dict[str, dict[int, dict]] = defaultdict(dict)
        
        # Callback for completed candles
        self._on_candle: Optional[Callable[[CandleData], Awaitable[None]]] = None
    
    def set_candle_callback(self, callback: Callable[[CandleData], Awaitable[None]]):
        """Set callback for when a candle completes."""
        self._on_candle = callback
    
    async def process_tick(self, tick: TickData):
        """Process incoming tick and update building candles."""
        symbol = tick.symbol
        
        for tf_minutes in self._timeframes:
            candle_start = self._get_candle_start(tick.timestamp, tf_minutes)
            
            if symbol not in self._building or tf_minutes not in self._building[symbol]:
                # Start new candle
                self._building[symbol][tf_minutes] = {
                    "open": tick.ltp,
                    "high": tick.ltp,
                    "low": tick.ltp,
                    "close": tick.ltp,
                    "volume": tick.volume,
                    "start_time": candle_start,
                    "last_volume": tick.volume,
                }
            else:
                current = self._building[symbol][tf_minutes]
                
                # Check if we've moved to a new candle period
                if candle_start > current["start_time"]:
                    # Emit completed candle
                    completed = CandleData(
                        symbol=symbol,
                        token=tick.token,
                        timeframe=f"{tf_minutes}m",
                        timestamp=current["start_time"],
                        open=current["open"],
                        high=current["high"],
                        low=current["low"],
                        close=current["close"],
                        volume=current["volume"] - current.get("initial_volume", 0),
                    )
                    
                    if self._on_candle:
                        await self._on_candle(completed)
                    
                    # Start new candle
                    self._building[symbol][tf_minutes] = {
                        "open": tick.ltp,
                        "high": tick.ltp,
                        "low": tick.ltp,
                        "close": tick.ltp,
                        "volume": tick.volume,
                        "start_time": candle_start,
                        "last_volume": tick.volume,
                        "initial_volume": tick.volume,
                    }
                else:
                    # Update current candle
                    current["high"] = max(current["high"], tick.ltp)
                    current["low"] = min(current["low"], tick.ltp)
                    current["close"] = tick.ltp
                    current["volume"] = tick.volume
    
    def _get_candle_start(self, timestamp: datetime, tf_minutes: int) -> datetime:
        """Calculate the start time of the candle period."""
        # Align to market open (9:15 IST)
        market_open_minutes = 9 * 60 + 15
        current_minutes = timestamp.hour * 60 + timestamp.minute
        
        minutes_since_open = current_minutes - market_open_minutes
        candle_index = minutes_since_open // tf_minutes
        candle_start_minutes = market_open_minutes + (candle_index * tf_minutes)
        
        start_hour = candle_start_minutes // 60
        start_minute = candle_start_minutes % 60
        
        return timestamp.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
    
    def get_current_candle(self, symbol: str, tf_minutes: int) -> Optional[dict]:
        """Get the currently building candle."""
        if symbol in self._building and tf_minutes in self._building[symbol]:
            return self._building[symbol][tf_minutes].copy()
        return None
    
    def reset(self):
        """Reset all building candles (e.g., at market open)."""
        self._building.clear()
