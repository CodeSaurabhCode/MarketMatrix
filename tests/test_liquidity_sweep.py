"""
Tests for Liquidity Sweep Detector.
"""
import pytest
from datetime import datetime, timedelta

from trading_system.core.schemas import CandleData, LiquidityZoneData
from trading_system.engines.liquidity_sweep import LiquiditySweepDetector


@pytest.fixture
def detector():
    return LiquiditySweepDetector()


@pytest.fixture
def sample_candles():
    """Create sample candle data with equal highs."""
    base_time = datetime(2026, 6, 1, 9, 15)
    candles = []
    
    # Create candles with equal highs at 100.0
    for i in range(50):
        if i in [10, 20, 30]:  # Equal highs
            high = 100.0
        else:
            high = 98.0 + (i % 3) * 0.5
        
        if i in [12, 22, 32]:  # Equal lows
            low = 95.0
        else:
            low = 96.0 + (i % 3) * 0.3
        
        candles.append(CandleData(
            symbol="RELIANCE-EQ",
            token="2885",
            timeframe="15m",
            timestamp=base_time + timedelta(minutes=15 * i),
            open=low + 1,
            high=high,
            low=low,
            close=low + 1.5,
            volume=100000 + i * 1000,
        ))
    
    return candles


def test_detect_equal_levels(detector, sample_candles):
    """Test detection of equal highs and lows."""
    buy_zones, sell_zones = detector.detect_equal_levels(sample_candles, min_touches=3)
    
    # Should find equal highs (buy-side liquidity)
    assert len(buy_zones) > 0
    # Equal highs at 100.0
    found_100 = any(abs(z.price_level - 100.0) < 0.5 for z in buy_zones)
    assert found_100
    
    # Should find equal lows (sell-side liquidity)
    assert len(sell_zones) > 0
    found_95 = any(abs(z.price_level - 95.0) < 0.5 for z in sell_zones)
    assert found_95


def test_detect_sweep_buy_side(detector):
    """Test buy-side liquidity sweep detection."""
    base_time = datetime(2026, 6, 1, 9, 15)
    
    # Create a sweep candle that goes above resistance but closes below
    candles = [
        CandleData(
            symbol="RELIANCE-EQ", token="2885", timeframe="15m",
            timestamp=base_time,
            open=99.0, high=99.5, low=98.5, close=99.2, volume=100000,
        ),
        CandleData(
            symbol="RELIANCE-EQ", token="2885", timeframe="15m",
            timestamp=base_time + timedelta(minutes=15),
            # Sweep candle: goes above 100 but closes below
            open=99.5, high=100.8, low=99.0, close=99.2, volume=150000,
        ),
    ]
    
    zone = LiquidityZoneData(
        symbol="RELIANCE-EQ",
        timeframe="15m",
        zone_type="BUY_SIDE",
        price_level=100.0,
        touch_count=3,
        formed_at=base_time - timedelta(hours=1),
    )
    
    sweeps = detector.detect_sweep(candles, [zone])
    
    assert len(sweeps) == 1
    assert sweeps[0].zone_type == "BUY_SIDE"
    assert sweeps[0].rejection_confirmed is True
    assert sweeps[0].sweep_strength > 0


def test_detect_sweep_sell_side(detector):
    """Test sell-side liquidity sweep detection."""
    base_time = datetime(2026, 6, 1, 9, 15)
    
    candles = [
        CandleData(
            symbol="RELIANCE-EQ", token="2885", timeframe="15m",
            timestamp=base_time,
            open=96.0, high=96.5, low=95.5, close=95.8, volume=100000,
        ),
        CandleData(
            symbol="RELIANCE-EQ", token="2885", timeframe="15m",
            timestamp=base_time + timedelta(minutes=15),
            # Sweep below 95 but closes above
            open=95.5, high=96.5, low=94.2, close=95.8, volume=200000,
        ),
    ]
    
    zone = LiquidityZoneData(
        symbol="RELIANCE-EQ",
        timeframe="15m",
        zone_type="SELL_SIDE",
        price_level=95.0,
        touch_count=4,
        formed_at=base_time - timedelta(hours=1),
    )
    
    sweeps = detector.detect_sweep(candles, [zone])
    
    assert len(sweeps) == 1
    assert sweeps[0].zone_type == "SELL_SIDE"
    assert sweeps[0].rejection_confirmed is True


def test_no_sweep_when_no_rejection(detector):
    """No sweep should be detected if price doesn't reject."""
    base_time = datetime(2026, 6, 1, 9, 15)
    
    candles = [
        CandleData(
            symbol="RELIANCE-EQ", token="2885", timeframe="15m",
            timestamp=base_time,
            open=99.0, high=99.5, low=98.5, close=99.2, volume=100000,
        ),
        CandleData(
            symbol="RELIANCE-EQ", token="2885", timeframe="15m",
            timestamp=base_time + timedelta(minutes=15),
            # Breaks above but closes above too (no rejection)
            open=99.5, high=101.0, low=99.5, close=100.5, volume=150000,
        ),
    ]
    
    zone = LiquidityZoneData(
        symbol="RELIANCE-EQ",
        timeframe="15m",
        zone_type="BUY_SIDE",
        price_level=100.0,
        touch_count=3,
        formed_at=base_time - timedelta(hours=1),
    )
    
    sweeps = detector.detect_sweep(candles, [zone])
    assert len(sweeps) == 0
