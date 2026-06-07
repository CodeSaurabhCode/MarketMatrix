"""
Tests for Signal Aggregator.
"""
import pytest
from datetime import datetime

from trading_system.core.schemas import (
    SignalContext, LiquiditySweepEvent, FVGData, FVGDirection,
    VWAPSignal, VolumeProfileData, OrderFlowSnapshot, MarketStructurePoint,
    StructureType, Trend, Direction
)
from trading_system.engines.liquidity_sweep import LiquiditySweepDetector
from trading_system.engines.anchored_vwap import AnchoredVWAPEngine
from trading_system.engines.fair_value_gap import FairValueGapDetector
from trading_system.engines.volume_profile import VolumeProfileEngine
from trading_system.engines.order_flow import OrderFlowProxyEngine
from trading_system.engines.market_structure import MarketStructureEngine
from trading_system.engines.signal_aggregator import SignalAggregator


@pytest.fixture
def aggregator():
    return SignalAggregator(
        liquidity_detector=LiquiditySweepDetector(),
        vwap_engine=AnchoredVWAPEngine(),
        fvg_detector=FairValueGapDetector(),
        volume_profile_engine=VolumeProfileEngine(),
        order_flow_engine=OrderFlowProxyEngine(),
        market_structure_engine=MarketStructureEngine(),
    )


class TestHighConfidenceLong:
    def test_generates_long_signal(self, aggregator):
        """All bullish conditions met should generate high confidence LONG."""
        context = SignalContext(
            symbol="RELIANCE-EQ",
            timeframe="15m",
            current_price=2500.0,
            timestamp=datetime(2026, 6, 1, 10, 0),
            liquidity_sweep=LiquiditySweepEvent(
                symbol="RELIANCE-EQ",
                timeframe="15m",
                zone_type="SELL_SIDE",
                price_level=2490.0,
                sweep_price=2485.0,
                sweep_time=datetime(2026, 6, 1, 9, 55),
                sweep_strength=85.0,
                candle_close=2500.0,
                rejection_confirmed=True,
            ),
            nearest_fvg=FVGData(
                symbol="RELIANCE-EQ",
                timeframe="15m",
                direction=FVGDirection.BULLISH,
                gap_high=2505.0,
                gap_low=2495.0,
                gap_size_percent=0.4,
                formation_time=datetime(2026, 6, 1, 9, 45),
                volume_at_formation=500000,
                quality_score=80.0,
            ),
            vwap_signals=[
                VWAPSignal(
                    symbol="RELIANCE-EQ",
                    anchor_type="prev_day_low",
                    signal_type="RECLAIM",
                    vwap_price=2495.0,
                    current_price=2500.0,
                    confidence=75.0,
                )
            ],
            volume_profile=VolumeProfileData(
                symbol="RELIANCE-EQ",
                timeframe="15m",
                session_date=datetime(2026, 6, 1),
                poc_price=2498.0,
                value_area_high=2520.0,
                value_area_low=2480.0,
                high_volume_nodes=[{"price": 2498.0, "volume": 500000, "strength": 2.0}],
                low_volume_nodes=[],
                total_volume=5000000,
            ),
            order_flow=OrderFlowSnapshot(
                symbol="RELIANCE-EQ",
                timestamp=datetime(2026, 6, 1, 10, 0),
                volume_delta=5000.0,
                aggressive_buy_volume=300000,
                aggressive_sell_volume=200000,
                relative_volume=2.5,
                large_trade_detected=True,
                cumulative_delta=15000.0,
            ),
            market_structure=MarketStructurePoint(
                symbol="RELIANCE-EQ",
                timeframe="15m",
                structure_type=StructureType.HL,
                price=2490.0,
                timestamp=datetime(2026, 6, 1, 9, 50),
                trend=Trend.BULLISH,
            ),
            atr=15.0,
        )
        
        signal = aggregator.evaluate(context)
        
        assert signal is not None
        assert signal.direction == Direction.LONG
        assert signal.confidence_score >= 80
        assert signal.entry_zone_low > 0
        assert signal.stop_loss < signal.entry_zone_low
        assert signal.target_1 > signal.entry_zone_high


class TestHighConfidenceShort:
    def test_generates_short_signal(self, aggregator):
        """All bearish conditions met should generate high confidence SHORT."""
        context = SignalContext(
            symbol="RELIANCE-EQ",
            timeframe="15m",
            current_price=2500.0,
            timestamp=datetime(2026, 6, 1, 10, 0),
            liquidity_sweep=LiquiditySweepEvent(
                symbol="RELIANCE-EQ",
                timeframe="15m",
                zone_type="BUY_SIDE",
                price_level=2510.0,
                sweep_price=2515.0,
                sweep_time=datetime(2026, 6, 1, 9, 55),
                sweep_strength=85.0,
                candle_close=2500.0,
                rejection_confirmed=True,
            ),
            nearest_fvg=FVGData(
                symbol="RELIANCE-EQ",
                timeframe="15m",
                direction=FVGDirection.BEARISH,
                gap_high=2505.0,
                gap_low=2495.0,
                gap_size_percent=0.4,
                formation_time=datetime(2026, 6, 1, 9, 45),
                volume_at_formation=500000,
                quality_score=80.0,
            ),
            vwap_signals=[
                VWAPSignal(
                    symbol="RELIANCE-EQ",
                    anchor_type="prev_day_high",
                    signal_type="REJECTION",
                    vwap_price=2505.0,
                    current_price=2500.0,
                    confidence=75.0,
                )
            ],
            volume_profile=VolumeProfileData(
                symbol="RELIANCE-EQ",
                timeframe="15m",
                session_date=datetime(2026, 6, 1),
                poc_price=2502.0,
                value_area_high=2520.0,
                value_area_low=2480.0,
                high_volume_nodes=[{"price": 2502.0, "volume": 500000, "strength": 2.0}],
                low_volume_nodes=[],
                total_volume=5000000,
            ),
            order_flow=OrderFlowSnapshot(
                symbol="RELIANCE-EQ",
                timestamp=datetime(2026, 6, 1, 10, 0),
                volume_delta=-5000.0,
                aggressive_buy_volume=200000,
                aggressive_sell_volume=300000,
                relative_volume=2.5,
                large_trade_detected=True,
                cumulative_delta=-15000.0,
            ),
            market_structure=MarketStructurePoint(
                symbol="RELIANCE-EQ",
                timeframe="15m",
                structure_type=StructureType.LH,
                price=2510.0,
                timestamp=datetime(2026, 6, 1, 9, 50),
                trend=Trend.BEARISH,
            ),
            atr=15.0,
        )
        
        signal = aggregator.evaluate(context)
        
        assert signal is not None
        assert signal.direction == Direction.SHORT
        assert signal.confidence_score >= 80
        assert signal.stop_loss > signal.entry_zone_high
        assert signal.target_1 < signal.entry_zone_low


class TestLowConfidence:
    def test_no_signal_when_weak(self, aggregator):
        """No signal when conditions are not met."""
        context = SignalContext(
            symbol="RELIANCE-EQ",
            timeframe="15m",
            current_price=2500.0,
            timestamp=datetime(2026, 6, 1, 10, 0),
            atr=15.0,
        )
        
        signal = aggregator.evaluate(context)
        assert signal is None
