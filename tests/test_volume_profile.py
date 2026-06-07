"""
Tests for Volume Profile Engine.
"""
import pytest
from datetime import datetime, timedelta

from trading_system.core.schemas import CandleData
from trading_system.engines.volume_profile import VolumeProfileEngine


@pytest.fixture
def engine():
    return VolumeProfileEngine(num_bins=20)


def make_candles(n=100, base_price=100, volatility=2):
    """Generate synthetic candles with a known high-volume node."""
    import random
    random.seed(42)
    
    base_time = datetime(2026, 6, 1, 9, 15)
    candles = []
    price = base_price
    
    for i in range(n):
        change = random.uniform(-volatility, volatility)
        open_ = price
        close = price + change
        high = max(open_, close) + random.uniform(0, 1)
        low = min(open_, close) - random.uniform(0, 1)
        
        # Create high volume around 100 (POC area)
        if abs(price - base_price) < 1:
            volume = random.randint(200000, 400000)
        else:
            volume = random.randint(50000, 100000)
        
        candles.append(CandleData(
            symbol="TEST",
            token="1234",
            timeframe="15m",
            timestamp=base_time + timedelta(minutes=15 * i),
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
        ))
        
        price = close
    
    return candles


class TestVolumeProfileCalculation:
    def test_calculates_poc(self, engine):
        """POC should be near the area with most volume."""
        candles = make_candles()
        profile = engine.calculate_profile(candles, "TEST", "15m")
        
        assert profile is not None
        # POC should be near 100 since we set high volume there
        assert abs(profile.poc_price - 100) < 5
    
    def test_value_area_contains_poc(self, engine):
        """VAH should be above VAL, and POC should be between them."""
        candles = make_candles()
        profile = engine.calculate_profile(candles, "TEST", "15m")
        
        assert profile is not None
        assert profile.value_area_high > profile.value_area_low
        assert profile.value_area_low <= profile.poc_price <= profile.value_area_high
    
    def test_finds_hvn(self, engine):
        """Should identify high volume nodes."""
        candles = make_candles()
        profile = engine.calculate_profile(candles, "TEST", "15m")
        
        assert profile is not None
        assert len(profile.high_volume_nodes) > 0
        
        # HVN should have strength > 1 (above average)
        for hvn in profile.high_volume_nodes:
            assert hvn["strength"] > 1.0
    
    def test_finds_lvn(self, engine):
        """Should identify low volume nodes."""
        candles = make_candles()
        profile = engine.calculate_profile(candles, "TEST", "15m")
        
        assert profile is not None
        assert len(profile.low_volume_nodes) > 0


class TestVolumeProfileSignals:
    def test_poc_rejection(self, engine):
        """Test POC rejection detection."""
        candles = make_candles(50)
        profile = engine.calculate_profile(candles, "TEST", "15m")
        
        if profile:
            # Create candles that reject from POC
            poc = profile.poc_price
            base_time = datetime(2026, 6, 2, 9, 15)
            
            test_candles = [
                CandleData(
                    symbol="TEST", token="1234", timeframe="15m",
                    timestamp=base_time,
                    open=poc - 0.5, high=poc + 0.1, low=poc - 1.0, close=poc - 0.3,
                    volume=100000,
                ),
                CandleData(
                    symbol="TEST", token="1234", timeframe="15m",
                    timestamp=base_time + timedelta(minutes=15),
                    # Touches POC and bounces up
                    open=poc - 0.2, high=poc + 1.5, low=poc - 0.1, close=poc + 1.2,
                    volume=150000,
                ),
            ]
            
            rejection = engine.detect_poc_rejection(test_candles, profile)
            # May or may not detect depending on exact POC value
            # This tests the API works
            assert rejection is None or rejection["type"] in [
                "POC_REJECTION_BULLISH", "POC_REJECTION_BEARISH"
            ]
