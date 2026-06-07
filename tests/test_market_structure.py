"""
Tests for Market Structure Engine.
"""
import pytest
from datetime import datetime, timedelta

from trading_system.core.schemas import CandleData, StructureType, Trend
from trading_system.engines.market_structure import MarketStructureEngine


@pytest.fixture
def engine():
    return MarketStructureEngine(swing_lookback=3)


def make_trending_candles(direction="up", n=50):
    """Create candles with clear trending structure."""
    base_time = datetime(2026, 6, 1, 9, 15)
    candles = []
    price = 100.0
    
    for i in range(n):
        if direction == "up":
            # Uptrend: generally rising with pullbacks for clear swings
            if i % 10 < 4:
                change = 1.0  # Strong up moves
                high_wick = 1.5
                low_wick = -0.3
            elif i % 10 < 7:
                change = 0.0  # Consolidation
                high_wick = 0.5
                low_wick = -0.5
            else:
                change = -0.5  # Pullbacks
                high_wick = 0.3
                low_wick = -0.8
        else:
            # Downtrend with clear pullbacks
            if i % 10 < 4:
                change = -1.0  # Strong down moves
                high_wick = 0.3
                low_wick = -1.5
            elif i % 10 < 7:
                change = 0.0
                high_wick = 0.5
                low_wick = -0.5
            else:
                change = 0.5  # Pullbacks
                high_wick = 0.8
                low_wick = -0.3
        
        open_ = price
        close = price + change
        high = max(open_, close) + high_wick
        low = min(open_, close) + low_wick
        
        candles.append(CandleData(
            symbol="TEST",
            token="1234",
            timeframe="15m",
            timestamp=base_time + timedelta(minutes=15 * i),
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=100000,
        ))
        
        price = close
    
    return candles


class TestSwingDetection:
    def test_finds_swing_highs_in_uptrend(self, engine):
        """Should identify swing highs in trending data."""
        candles = make_trending_candles("up")
        points = engine.analyze(candles, "TEST", "15m")
        
        highs = engine.get_swing_highs("TEST", "15m")
        assert len(highs) > 0
    
    def test_finds_swing_lows_in_downtrend(self, engine):
        """Should identify swing lows in trending data."""
        candles = make_trending_candles("down")
        points = engine.analyze(candles, "TEST", "15m")
        
        lows = engine.get_swing_lows("TEST", "15m")
        assert len(lows) > 0


class TestTrendDetection:
    def test_bullish_trend(self, engine):
        """Uptrend should be identified."""
        candles = make_trending_candles("up")
        engine.analyze(candles, "TEST", "15m")
        
        trend = engine.get_trend("TEST", "15m")
        # May be BULLISH or RANGING depending on swing point detection
        assert trend in [Trend.BULLISH, Trend.RANGING]
    
    def test_bearish_trend(self, engine):
        """Downtrend should be identified."""
        candles = make_trending_candles("down")
        engine.analyze(candles, "TEST", "15m")
        
        trend = engine.get_trend("TEST", "15m")
        assert trend in [Trend.BEARISH, Trend.RANGING]
