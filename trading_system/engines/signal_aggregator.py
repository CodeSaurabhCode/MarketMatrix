"""
Signal Aggregator

Combines signals from all detection engines to generate high-confidence trade setups.
Implements the signal scoring rules and generates final trade signals.
"""
import logging
from datetime import datetime
from typing import Optional

import numpy as np

from trading_system.config.settings import settings
from trading_system.core.schemas import (
    CandleData, SignalCreate, SignalContext, Direction,
    LiquiditySweepEvent, FVGData, FVGDirection, VWAPSignal,
    VolumeProfileData, OrderFlowSnapshot, MarketStructurePoint, Trend
)
from trading_system.engines.liquidity_sweep import LiquiditySweepDetector
from trading_system.engines.anchored_vwap import AnchoredVWAPEngine
from trading_system.engines.fair_value_gap import FairValueGapDetector
from trading_system.engines.volume_profile import VolumeProfileEngine
from trading_system.engines.order_flow import OrderFlowProxyEngine
from trading_system.engines.market_structure import MarketStructureEngine

logger = logging.getLogger(__name__)


class SignalAggregator:
    """
    Aggregates all detection engine outputs into actionable trade signals.
    
    Scoring weights:
    - Liquidity Sweep: 25%
    - FVG: 20%
    - Anchored VWAP: 20%
    - Volume Profile: 20%
    - Order Flow: 15%
    """
    
    WEIGHT_LIQUIDITY = 0.25
    WEIGHT_FVG = 0.20
    WEIGHT_VWAP = 0.20
    WEIGHT_VOLUME_PROFILE = 0.20
    WEIGHT_ORDER_FLOW = 0.15
    
    def __init__(
        self,
        liquidity_detector: LiquiditySweepDetector,
        vwap_engine: AnchoredVWAPEngine,
        fvg_detector: FairValueGapDetector,
        volume_profile_engine: VolumeProfileEngine,
        order_flow_engine: OrderFlowProxyEngine,
        market_structure_engine: MarketStructureEngine,
    ):
        self._liquidity = liquidity_detector
        self._vwap = vwap_engine
        self._fvg = fvg_detector
        self._volume_profile = volume_profile_engine
        self._order_flow = order_flow_engine
        self._market_structure = market_structure_engine
        
        self._min_score = settings.signal.min_confidence_score
    
    def evaluate(self, context: SignalContext) -> Optional[SignalCreate]:
        """
        Evaluate a signal context and generate a trade signal if confidence is high enough.
        
        Returns SignalCreate if score >= min_confidence_score, else None.
        """
        # Determine potential direction based on available signals
        long_score = self._score_long(context)
        short_score = self._score_short(context)
        
        # Take the stronger direction
        if long_score >= short_score and long_score >= self._min_score:
            return self._build_signal(context, Direction.LONG, long_score)
        elif short_score > long_score and short_score >= self._min_score:
            return self._build_signal(context, Direction.SHORT, short_score)
        
        return None
    
    def _score_long(self, context: SignalContext) -> float:
        """Score a potential LONG signal."""
        scores = {}
        
        # 1. Liquidity Sweep Score (sell-side sweep = bullish)
        if context.liquidity_sweep:
            if context.liquidity_sweep.zone_type == "SELL_SIDE":
                scores["liquidity"] = context.liquidity_sweep.sweep_strength
            else:
                scores["liquidity"] = 0
        else:
            scores["liquidity"] = 0
        
        # 2. FVG Score (bullish FVG nearby)
        if context.nearest_fvg:
            if context.nearest_fvg.direction == FVGDirection.BULLISH:
                # Check if price is near the FVG
                fvg_mid = (context.nearest_fvg.gap_high + context.nearest_fvg.gap_low) / 2
                distance_pct = abs(context.current_price - fvg_mid) / context.current_price * 100
                
                if distance_pct < 1.0:  # Within 1% of FVG
                    scores["fvg"] = context.nearest_fvg.quality_score * (1 - distance_pct)
                else:
                    scores["fvg"] = context.nearest_fvg.quality_score * 0.3
            else:
                scores["fvg"] = 0
        else:
            scores["fvg"] = 0
        
        # 3. VWAP Score (price above anchored VWAP or reclaiming)
        scores["vwap"] = self._score_vwap_long(context)
        
        # 4. Volume Profile Score (support at HVN/POC)
        scores["volume_profile"] = self._score_volume_profile_long(context)
        
        # 5. Order Flow Score (positive delta)
        if context.order_flow:
            if context.order_flow.volume_delta > 0:
                # Positive delta with volume expansion
                delta_score = min(abs(context.order_flow.volume_delta) / 1000, 1.0) * 60
                rvol_bonus = min(context.order_flow.relative_volume / 2, 1.0) * 40
                scores["order_flow"] = delta_score + rvol_bonus
            else:
                scores["order_flow"] = 0
        else:
            scores["order_flow"] = 0
        
        # Market structure bonus
        structure_bonus = 0
        if context.market_structure and context.market_structure.trend == Trend.BULLISH:
            structure_bonus = 10
        
        # Weighted composite score
        composite = (
            scores["liquidity"] * self.WEIGHT_LIQUIDITY +
            scores["fvg"] * self.WEIGHT_FVG +
            scores["vwap"] * self.WEIGHT_VWAP +
            scores["volume_profile"] * self.WEIGHT_VOLUME_PROFILE +
            scores["order_flow"] * self.WEIGHT_ORDER_FLOW +
            structure_bonus
        )
        
        return min(composite, 100.0)
    
    def _score_short(self, context: SignalContext) -> float:
        """Score a potential SHORT signal."""
        scores = {}
        
        # 1. Liquidity Sweep Score (buy-side sweep = bearish)
        if context.liquidity_sweep:
            if context.liquidity_sweep.zone_type == "BUY_SIDE":
                scores["liquidity"] = context.liquidity_sweep.sweep_strength
            else:
                scores["liquidity"] = 0
        else:
            scores["liquidity"] = 0
        
        # 2. FVG Score (bearish FVG nearby)
        if context.nearest_fvg:
            if context.nearest_fvg.direction == FVGDirection.BEARISH:
                fvg_mid = (context.nearest_fvg.gap_high + context.nearest_fvg.gap_low) / 2
                distance_pct = abs(context.current_price - fvg_mid) / context.current_price * 100
                
                if distance_pct < 1.0:
                    scores["fvg"] = context.nearest_fvg.quality_score * (1 - distance_pct)
                else:
                    scores["fvg"] = context.nearest_fvg.quality_score * 0.3
            else:
                scores["fvg"] = 0
        else:
            scores["fvg"] = 0
        
        # 3. VWAP Score (price below anchored VWAP or rejecting)
        scores["vwap"] = self._score_vwap_short(context)
        
        # 4. Volume Profile Score (resistance at HVN/POC)
        scores["volume_profile"] = self._score_volume_profile_short(context)
        
        # 5. Order Flow Score (negative delta)
        if context.order_flow:
            if context.order_flow.volume_delta < 0:
                delta_score = min(abs(context.order_flow.volume_delta) / 1000, 1.0) * 60
                rvol_bonus = min(context.order_flow.relative_volume / 2, 1.0) * 40
                scores["order_flow"] = delta_score + rvol_bonus
            else:
                scores["order_flow"] = 0
        else:
            scores["order_flow"] = 0
        
        # Market structure bonus
        structure_bonus = 0
        if context.market_structure and context.market_structure.trend == Trend.BEARISH:
            structure_bonus = 10
        
        composite = (
            scores["liquidity"] * self.WEIGHT_LIQUIDITY +
            scores["fvg"] * self.WEIGHT_FVG +
            scores["vwap"] * self.WEIGHT_VWAP +
            scores["volume_profile"] * self.WEIGHT_VOLUME_PROFILE +
            scores["order_flow"] * self.WEIGHT_ORDER_FLOW +
            structure_bonus
        )
        
        return min(composite, 100.0)
    
    def _score_vwap_long(self, context: SignalContext) -> float:
        """Score VWAP alignment for long signal."""
        if not context.vwap_signals:
            return 0
        
        score = 0
        for vwap_signal in context.vwap_signals:
            if vwap_signal.signal_type == "RECLAIM" and vwap_signal.current_price > vwap_signal.vwap_price:
                score = max(score, vwap_signal.confidence)
            elif vwap_signal.signal_type == "CROSS_WITH_VOLUME" and vwap_signal.current_price > vwap_signal.vwap_price:
                score = max(score, vwap_signal.confidence * 0.9)
            elif vwap_signal.signal_type == "REJECTION" and vwap_signal.current_price > vwap_signal.vwap_price:
                score = max(score, vwap_signal.confidence * 0.7)
        
        return score
    
    def _score_vwap_short(self, context: SignalContext) -> float:
        """Score VWAP alignment for short signal."""
        if not context.vwap_signals:
            return 0
        
        score = 0
        for vwap_signal in context.vwap_signals:
            if vwap_signal.signal_type == "REJECTION" and vwap_signal.current_price < vwap_signal.vwap_price:
                score = max(score, vwap_signal.confidence)
            elif vwap_signal.signal_type == "CROSS_WITH_VOLUME" and vwap_signal.current_price < vwap_signal.vwap_price:
                score = max(score, vwap_signal.confidence * 0.9)
        
        return score
    
    def _score_volume_profile_long(self, context: SignalContext) -> float:
        """Score volume profile support for long."""
        if not context.volume_profile:
            return 0
        
        vp = context.volume_profile
        price = context.current_price
        
        score = 0
        
        # Price at or near POC from below (support)
        poc_distance = abs(price - vp.poc_price) / vp.poc_price * 100
        if poc_distance < 0.5 and price >= vp.poc_price * 0.998:
            score = max(score, 80)
        
        # Price near VAL (value area support)
        val_distance = abs(price - vp.value_area_low) / vp.value_area_low * 100
        if val_distance < 0.3:
            score = max(score, 70)
        
        # Price at HVN from below
        for hvn in vp.high_volume_nodes:
            hvn_distance = abs(price - hvn["price"]) / hvn["price"] * 100
            if hvn_distance < 0.3 and price >= hvn["price"] * 0.998:
                hvn_score = min(hvn["strength"] * 30, 75)
                score = max(score, hvn_score)
        
        return score
    
    def _score_volume_profile_short(self, context: SignalContext) -> float:
        """Score volume profile resistance for short."""
        if not context.volume_profile:
            return 0
        
        vp = context.volume_profile
        price = context.current_price
        
        score = 0
        
        # Price at or near POC from above (resistance)
        poc_distance = abs(price - vp.poc_price) / vp.poc_price * 100
        if poc_distance < 0.5 and price <= vp.poc_price * 1.002:
            score = max(score, 80)
        
        # Price near VAH (value area resistance)
        vah_distance = abs(price - vp.value_area_high) / vp.value_area_high * 100
        if vah_distance < 0.3:
            score = max(score, 70)
        
        # Price at HVN from above
        for hvn in vp.high_volume_nodes:
            hvn_distance = abs(price - hvn["price"]) / hvn["price"] * 100
            if hvn_distance < 0.3 and price <= hvn["price"] * 1.002:
                hvn_score = min(hvn["strength"] * 30, 75)
                score = max(score, hvn_score)
        
        return score
    
    def _build_signal(
        self, context: SignalContext, direction: Direction, score: float
    ) -> SignalCreate:
        """Build the final trade signal with entry, SL, and targets."""
        atr = context.atr if context.atr > 0 else context.current_price * 0.005  # Default 0.5% ATR
        
        if direction == Direction.LONG:
            entry_low = context.current_price
            entry_high = context.current_price + atr * 0.3
            stop_loss = context.current_price - atr * 1.5
            target_1 = context.current_price + atr * 2.0
            target_2 = context.current_price + atr * 3.5
            
            # Adjust SL to below FVG or liquidity sweep level
            if context.nearest_fvg and context.nearest_fvg.direction == FVGDirection.BULLISH:
                fvg_sl = context.nearest_fvg.gap_low - atr * 0.2
                stop_loss = min(stop_loss, fvg_sl)
            
            if context.liquidity_sweep and context.liquidity_sweep.zone_type == "SELL_SIDE":
                sweep_sl = context.liquidity_sweep.sweep_price - atr * 0.2
                stop_loss = min(stop_loss, sweep_sl)
        
        else:  # SHORT
            entry_low = context.current_price - atr * 0.3
            entry_high = context.current_price
            stop_loss = context.current_price + atr * 1.5
            target_1 = context.current_price - atr * 2.0
            target_2 = context.current_price - atr * 3.5
            
            if context.nearest_fvg and context.nearest_fvg.direction == FVGDirection.BEARISH:
                fvg_sl = context.nearest_fvg.gap_high + atr * 0.2
                stop_loss = max(stop_loss, fvg_sl)
            
            if context.liquidity_sweep and context.liquidity_sweep.zone_type == "BUY_SIDE":
                sweep_sl = context.liquidity_sweep.sweep_price + atr * 0.2
                stop_loss = max(stop_loss, sweep_sl)
        
        # Build reasoning text
        reasoning = self._build_reasoning(context, direction, score)
        
        # Component scores
        liq_score = context.liquidity_sweep.sweep_strength if context.liquidity_sweep else 0
        fvg_score = context.nearest_fvg.quality_score if context.nearest_fvg else 0
        vwap_score = max((v.confidence for v in context.vwap_signals), default=0)
        vp_score = self._score_volume_profile_long(context) if direction == Direction.LONG else self._score_volume_profile_short(context)
        of_score = 0
        if context.order_flow:
            of_score = min(abs(context.order_flow.volume_delta) / 1000 * 100, 100)
        
        return SignalCreate(
            symbol=context.symbol,
            direction=direction,
            entry_zone_low=round(entry_low, 2),
            entry_zone_high=round(entry_high, 2),
            stop_loss=round(stop_loss, 2),
            target_1=round(target_1, 2),
            target_2=round(target_2, 2),
            confidence_score=round(score, 1),
            liquidity_sweep_score=round(liq_score, 1),
            fvg_score=round(fvg_score, 1),
            vwap_score=round(vwap_score, 1),
            volume_profile_score=round(vp_score, 1),
            order_flow_score=round(of_score, 1),
            reasoning=reasoning,
            timeframe=context.timeframe,
        )
    
    def _build_reasoning(
        self, context: SignalContext, direction: Direction, score: float
    ) -> str:
        """Build human-readable reasoning for the signal."""
        parts = []
        
        if context.liquidity_sweep:
            sweep = context.liquidity_sweep
            parts.append(
                f"{'Sell' if sweep.zone_type == 'SELL_SIDE' else 'Buy'}-side liquidity swept "
                f"at {sweep.price_level:.2f} (strength: {sweep.sweep_strength:.0f})"
            )
        
        if context.nearest_fvg:
            fvg = context.nearest_fvg
            parts.append(
                f"{fvg.direction.value} FVG [{fvg.gap_low:.2f} - {fvg.gap_high:.2f}] "
                f"(quality: {fvg.quality_score:.0f})"
            )
        
        if context.vwap_signals:
            best_vwap = max(context.vwap_signals, key=lambda v: v.confidence)
            parts.append(
                f"VWAP {best_vwap.signal_type} from {best_vwap.anchor_type} "
                f"at {best_vwap.vwap_price:.2f}"
            )
        
        if context.volume_profile:
            vp = context.volume_profile
            parts.append(f"POC: {vp.poc_price:.2f}, VAH: {vp.value_area_high:.2f}, VAL: {vp.value_area_low:.2f}")
        
        if context.order_flow:
            of = context.order_flow
            parts.append(
                f"Volume delta: {of.volume_delta:+.0f}, RVOL: {of.relative_volume:.1f}x"
            )
        
        if context.market_structure:
            ms = context.market_structure
            parts.append(f"Market structure: {ms.structure_type.value} ({ms.trend.value})")
        
        return " | ".join(parts)
    
    def calculate_atr(self, candles: list[CandleData], period: int = 14) -> float:
        """Calculate Average True Range."""
        if len(candles) < period + 1:
            if candles:
                return (candles[-1].high - candles[-1].low)
            return 0
        
        true_ranges = []
        for i in range(1, len(candles)):
            high = candles[i].high
            low = candles[i].low
            prev_close = candles[i - 1].close
            
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            true_ranges.append(tr)
        
        return float(np.mean(true_ranges[-period:]))
