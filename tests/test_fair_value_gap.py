"""
Tests for Fair Value Gap Detector.
"""
import pytest
from datetime import datetime, timedelta

from trading_system.core.schemas import CandleData, FVGDirection
from trading_system.engines.fair_value_gap import FairValueGapDetector


@pytest.fixture
def detector():
    return FairValueGapDetector()


def make_candle(symbol, timestamp, open_, high, low, close, volume=100000):
    return CandleData(
        symbol=symbol, token="2885", timeframe="15m",
        timestamp=timestamp,
        open=open_, high=high, low=low, close=close, volume=volume,
    )


class TestBullishFVG:
    def test_detects_bullish_fvg(self, detector):
        """Bullish FVG: candle 3 low > candle 1 high."""
        base = datetime(2026, 6, 1, 9, 15)
        
        candles = [
            make_candle("TEST", base, 100, 101, 99, 100.5),            # C1: high=101
            make_candle("TEST", base + timedelta(minutes=15), 101, 104, 100.5, 103.5, 200000),  # C2: impulse
            make_candle("TEST", base + timedelta(minutes=30), 103.5, 105, 101.5, 104.5),  # C3: low=101.5
        ]
        
        # C3.low (101.5) > C1.high (101) => Bullish FVG
        fvgs = detector.detect_fvg(candles)
        
        assert len(fvgs) == 1
        assert fvgs[0].direction == FVGDirection.BULLISH
        assert fvgs[0].gap_low == 101.0  # C1 high
        assert fvgs[0].gap_high == 101.5  # C3 low
    
    def test_no_fvg_when_overlap(self, detector):
        """No FVG when candle 3 low <= candle 1 high."""
        base = datetime(2026, 6, 1, 9, 15)
        
        candles = [
            make_candle("TEST", base, 100, 102, 99, 101),             # C1: high=102
            make_candle("TEST", base + timedelta(minutes=15), 101, 103, 100.5, 102.5),
            make_candle("TEST", base + timedelta(minutes=30), 102.5, 104, 101.5, 103),  # C3: low=101.5 < C1.high=102
        ]
        
        fvgs = detector.detect_fvg(candles)
        assert len(fvgs) == 0


class TestBearishFVG:
    def test_detects_bearish_fvg(self, detector):
        """Bearish FVG: candle 3 high < candle 1 low."""
        base = datetime(2026, 6, 1, 9, 15)
        
        candles = [
            make_candle("TEST", base, 100, 101, 99, 100.5),           # C1: low=99
            make_candle("TEST", base + timedelta(minutes=15), 99, 99.5, 96, 96.5, 200000),  # C2: impulse down
            make_candle("TEST", base + timedelta(minutes=30), 96.5, 98.5, 95.5, 96),  # C3: high=98.5
        ]
        
        # C3.high (98.5) < C1.low (99) => Bearish FVG
        fvgs = detector.detect_fvg(candles)
        
        assert len(fvgs) == 1
        assert fvgs[0].direction == FVGDirection.BEARISH
        assert fvgs[0].gap_high == 99.0  # C1 low
        assert fvgs[0].gap_low == 98.5  # C3 high


class TestFVGQuality:
    def test_quality_score_higher_with_volume(self, detector):
        """Higher volume at formation should increase quality."""
        base = datetime(2026, 6, 1, 9, 15)
        
        # Generate warmup candles
        candles = []
        for i in range(20):
            candles.append(make_candle(
                "TEST", base + timedelta(minutes=15 * i),
                100, 101, 99, 100, 50000  # Low volume history
            ))
        
        # Add high-volume FVG formation
        candles.extend([
            make_candle("TEST", base + timedelta(minutes=300), 100, 101, 99, 100.5, 50000),
            make_candle("TEST", base + timedelta(minutes=315), 101, 104, 100.5, 103.5, 300000),  # 6x avg volume
            make_candle("TEST", base + timedelta(minutes=330), 103.5, 105, 101.5, 104.5, 100000),
        ])
        
        fvgs = detector.detect_fvg(candles)
        
        assert len(fvgs) == 1
        assert fvgs[0].quality_score > 40  # Should be high due to volume


class TestFVGTracking:
    def test_nearest_fvg(self, detector):
        """Test getting nearest FVG to current price."""
        base = datetime(2026, 6, 1, 9, 15)
        
        # Create a bullish FVG at 101-101.5
        candles = [
            make_candle("TEST", base, 100, 101, 99, 100.5),
            make_candle("TEST", base + timedelta(minutes=15), 101, 104, 100.5, 103.5, 200000),
            make_candle("TEST", base + timedelta(minutes=30), 103.5, 105, 101.5, 104.5),
        ]
        
        detector.detect_fvg(candles)
        
        # Price at 102 - closest to FVG at 101-101.5
        nearest = detector.get_nearest_fvg("TEST", 102.0)
        assert nearest is not None
        assert nearest.direction == FVGDirection.BULLISH
