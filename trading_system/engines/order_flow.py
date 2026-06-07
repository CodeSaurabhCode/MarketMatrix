"""
Order Flow Proxy Engine

Since true exchange-level order flow (Level 3) is unavailable through Angel One,
this engine approximates order flow using:
- Tick aggregation
- Volume delta approximation (close vs open position within candle)
- Aggressive buying/selling estimation
- Large volume spike detection
- Relative volume calculation
- Cumulative delta tracking
"""
import logging
from collections import deque
from datetime import datetime, timedelta
from typing import Optional

import numpy as np

from trading_system.config.settings import settings
from trading_system.core.schemas import TickData, CandleData, OrderFlowSnapshot

logger = logging.getLogger(__name__)


class OrderFlowProxyEngine:
    """Approximates order flow from available tick and candle data."""
    
    def __init__(self):
        self._volume_spike_threshold = settings.signal.volume_spike_threshold
        
        # Per-symbol state
        self._tick_buffer: dict[str, deque] = {}  # Recent ticks for aggregation
        self._cumulative_delta: dict[str, float] = {}
        self._volume_history: dict[str, deque] = {}  # Rolling volume for RVOL
        self._last_snapshot: dict[str, OrderFlowSnapshot] = {}
        
        # Buffer sizes
        self._tick_buffer_size = 1000
        self._volume_history_size = 100  # Last N candles for RVOL baseline
    
    def process_tick(self, tick: TickData) -> Optional[OrderFlowSnapshot]:
        """
        Process incoming tick and update order flow estimates.
        
        Uses bid/ask to approximate aggressor side:
        - Trade at ask = aggressive buyer
        - Trade at bid = aggressive seller
        """
        symbol = tick.symbol
        
        if symbol not in self._tick_buffer:
            self._tick_buffer[symbol] = deque(maxlen=self._tick_buffer_size)
            self._cumulative_delta[symbol] = 0.0
        
        self._tick_buffer[symbol].append(tick)
        
        # Determine aggressor side
        delta = 0.0
        if tick.best_bid and tick.best_ask:
            mid = (tick.best_bid + tick.best_ask) / 2
            if tick.ltp >= mid:
                # Trade at or above mid = buyer-initiated
                delta = 1.0
            else:
                # Trade at or below mid = seller-initiated
                delta = -1.0
        
        self._cumulative_delta[symbol] += delta
        
        return None  # Snapshots generated per-candle, not per-tick
    
    def calculate_snapshot(
        self, symbol: str, candles: list[CandleData]
    ) -> OrderFlowSnapshot:
        """
        Calculate order flow snapshot for the latest candle.
        
        Volume Delta Approximation:
        - If close > open: most volume was buying pressure
        - If close < open: most volume was selling pressure
        - Proportion based on (close - low) / (high - low) for buying
        """
        if not candles:
            return self._empty_snapshot(symbol)
        
        latest = candles[-1]
        
        # Volume delta approximation
        candle_range = latest.high - latest.low
        if candle_range > 0:
            # Buying pressure: how close the close is to the high
            buy_ratio = (latest.close - latest.low) / candle_range
            sell_ratio = 1 - buy_ratio
            
            aggressive_buy = int(latest.volume * buy_ratio)
            aggressive_sell = int(latest.volume * sell_ratio)
            volume_delta = float(aggressive_buy - aggressive_sell)
        else:
            aggressive_buy = latest.volume // 2
            aggressive_sell = latest.volume // 2
            volume_delta = 0.0
        
        # Update cumulative delta
        if symbol not in self._cumulative_delta:
            self._cumulative_delta[symbol] = 0.0
        self._cumulative_delta[symbol] += volume_delta
        
        # Relative volume calculation
        relative_volume = self._calculate_relative_volume(symbol, latest.volume, candles)
        
        # Large trade detection
        large_trade = self._detect_large_trade(latest, candles)
        
        snapshot = OrderFlowSnapshot(
            symbol=symbol,
            timestamp=latest.timestamp,
            volume_delta=volume_delta,
            aggressive_buy_volume=aggressive_buy,
            aggressive_sell_volume=aggressive_sell,
            relative_volume=relative_volume,
            large_trade_detected=large_trade,
            cumulative_delta=self._cumulative_delta.get(symbol, 0),
        )
        
        self._last_snapshot[symbol] = snapshot
        return snapshot
    
    def _calculate_relative_volume(
        self, symbol: str, current_volume: int, candles: list[CandleData]
    ) -> float:
        """
        Calculate Relative Volume (RVOL).
        RVOL = Current Volume / Average Volume for same time of day.
        """
        if symbol not in self._volume_history:
            self._volume_history[symbol] = deque(maxlen=self._volume_history_size)
        
        self._volume_history[symbol].append(current_volume)
        
        if len(self._volume_history[symbol]) < 5:
            # Use candle history as fallback
            if len(candles) >= 20:
                avg_vol = np.mean([c.volume for c in candles[-20:]])
            else:
                avg_vol = np.mean([c.volume for c in candles]) if candles else 1
        else:
            avg_vol = np.mean(list(self._volume_history[symbol]))
        
        if avg_vol == 0:
            return 1.0
        
        return current_volume / avg_vol
    
    def _detect_large_trade(self, latest: CandleData, candles: list[CandleData]) -> bool:
        """Detect if current volume represents an abnormally large trade."""
        if len(candles) < 10:
            return False
        
        volumes = [c.volume for c in candles[-20:]]
        avg_volume = np.mean(volumes)
        std_volume = np.std(volumes)
        
        if std_volume == 0:
            return False
        
        # Z-score based detection
        z_score = (latest.volume - avg_volume) / std_volume
        return z_score > 2.5  # More than 2.5 standard deviations
    
    def detect_volume_spike(self, candles: list[CandleData]) -> Optional[dict]:
        """Detect significant volume spikes indicating institutional activity."""
        if len(candles) < 20:
            return None
        
        latest = candles[-1]
        avg_volume = np.mean([c.volume for c in candles[-20:-1]])
        
        if avg_volume == 0:
            return None
        
        ratio = latest.volume / avg_volume
        
        if ratio >= self._volume_spike_threshold:
            # Determine direction of the spike
            is_bullish = latest.close > latest.open
            
            return {
                "symbol": latest.symbol,
                "timestamp": latest.timestamp,
                "volume_ratio": ratio,
                "direction": "BULLISH" if is_bullish else "BEARISH",
                "volume": latest.volume,
                "avg_volume": int(avg_volume),
            }
        
        return None
    
    def get_delta_divergence(
        self, candles: list[CandleData], lookback: int = 10
    ) -> Optional[dict]:
        """
        Detect divergence between price and cumulative volume delta.
        
        - Price making new highs but delta declining = bearish divergence
        - Price making new lows but delta rising = bullish divergence
        """
        if len(candles) < lookback:
            return None
        
        recent = candles[-lookback:]
        
        # Calculate delta for each candle
        deltas = []
        for c in recent:
            range_ = c.high - c.low
            if range_ > 0:
                buy_ratio = (c.close - c.low) / range_
                delta = c.volume * (2 * buy_ratio - 1)
            else:
                delta = 0
            deltas.append(delta)
        
        cum_delta = np.cumsum(deltas)
        prices = [c.close for c in recent]
        
        # Check for divergence in the last few candles
        price_slope = np.polyfit(range(len(prices)), prices, 1)[0]
        delta_slope = np.polyfit(range(len(cum_delta)), cum_delta, 1)[0]
        
        if price_slope > 0 and delta_slope < 0:
            return {
                "type": "BEARISH_DIVERGENCE",
                "price_trend": "UP",
                "delta_trend": "DOWN",
                "strength": abs(delta_slope) / (abs(price_slope) + 1),
            }
        
        if price_slope < 0 and delta_slope > 0:
            return {
                "type": "BULLISH_DIVERGENCE",
                "price_trend": "DOWN",
                "delta_trend": "UP",
                "strength": abs(delta_slope) / (abs(price_slope) + 1),
            }
        
        return None
    
    def _empty_snapshot(self, symbol: str) -> OrderFlowSnapshot:
        return OrderFlowSnapshot(
            symbol=symbol,
            timestamp=datetime.now(),
            volume_delta=0,
            aggressive_buy_volume=0,
            aggressive_sell_volume=0,
            relative_volume=1.0,
            large_trade_detected=False,
            cumulative_delta=0,
        )
    
    def get_snapshot(self, symbol: str) -> Optional[OrderFlowSnapshot]:
        """Get last calculated snapshot."""
        return self._last_snapshot.get(symbol)
    
    def reset_session(self, symbol: str):
        """Reset cumulative delta for new trading session."""
        self._cumulative_delta[symbol] = 0.0
        if symbol in self._tick_buffer:
            self._tick_buffer[symbol].clear()
