"""
Signal aggregator and confidence engine.

Combines detection engine outputs into explainable 0-100 trade setup scores.
"""
import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from trading_system.config.settings import settings
from trading_system.core.schemas import (
    CandleData,
    Direction,
    FVGData,
    FVGDirection,
    LiquiditySweepEvent,
    MarketStructurePoint,
    OrderFlowSnapshot,
    SignalContext,
    SignalCreate,
    StructureType,
    Trend,
)
from trading_system.engines.anchored_vwap import AnchoredVWAPEngine
from trading_system.engines.fair_value_gap import FairValueGapDetector
from trading_system.engines.liquidity_sweep import LiquiditySweepDetector
from trading_system.engines.market_structure import MarketStructureEngine
from trading_system.engines.order_flow import OrderFlowProxyEngine
from trading_system.engines.volume_profile import VolumeProfileEngine

logger = logging.getLogger(__name__)


@dataclass
class ConfidenceResult:
    score: float
    component_scores: dict[str, float] = field(default_factory=dict)
    weighted_scores: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


class ConfidenceEngine:
    """Institutional confidence model with explainable component scores."""

    WEIGHTS = {
        "market_structure": 0.25,
        "liquidity_sweep": 0.30,
        "fvg": 0.20,
        "vwap": 0.10,
        "volume_profile": 0.10,
        "volume_confirmation": 0.05,
    }

    def score(self, context: SignalContext, direction: Direction) -> ConfidenceResult:
        components = {
            "market_structure": self._score_market_structure(context, direction),
            "liquidity_sweep": self._score_liquidity(context.liquidity_sweep, direction),
            "fvg": self._score_fvg(context.nearest_fvg, context.current_price, direction),
            "vwap": self._score_vwap(context, direction),
            "volume_profile": self._score_volume_profile(context, direction),
            "volume_confirmation": self._score_volume_confirmation(context.order_flow, direction),
        }
        weighted = {
            name: score * self.WEIGHTS[name]
            for name, score in components.items()
        }
        total = min(sum(weighted.values()), 100.0)
        return ConfidenceResult(
            score=total,
            component_scores=components,
            weighted_scores=weighted,
            reasons=self._build_reasons(context, direction, components),
        )

    def _score_market_structure(self, context: SignalContext, direction: Direction) -> float:
        candidates = [
            context.market_structure,
            context.higher_timeframe_structure,
            context.execution_structure,
        ]
        best = 0.0
        for structure in [c for c in candidates if c is not None]:
            aligned = self._structure_aligned(structure, direction)
            if not aligned:
                continue

            base_by_type = {
                StructureType.MSS: 96,
                StructureType.CHOCH: 92,
                StructureType.BOS: 88,
                StructureType.HL: 72,
                StructureType.HH: 65,
                StructureType.LH: 72,
                StructureType.LL: 65,
            }
            base = base_by_type.get(structure.structure_type, 55)
            displacement_bonus = min(structure.displacement_score * 0.12, 8)
            strength_bonus = min(structure.strength_score * 0.05, 5)
            best = max(best, min(base + displacement_bonus + strength_bonus, 100))
        return best

    def _structure_aligned(self, structure: MarketStructurePoint, direction: Direction) -> bool:
        if direction == Direction.LONG:
            return structure.trend == Trend.BULLISH
        return structure.trend == Trend.BEARISH

    def _score_liquidity(
        self, sweep: Optional[LiquiditySweepEvent], direction: Direction
    ) -> float:
        if not sweep:
            return 0
        if direction == Direction.LONG and sweep.zone_type == "SELL_SIDE":
            return sweep.sweep_strength
        if direction == Direction.SHORT and sweep.zone_type == "BUY_SIDE":
            return sweep.sweep_strength
        return 0

    def _score_fvg(
        self, fvg: Optional[FVGData], current_price: float, direction: Direction
    ) -> float:
        if not fvg:
            return 0
        if direction == Direction.LONG and fvg.direction != FVGDirection.BULLISH:
            return 0
        if direction == Direction.SHORT and fvg.direction != FVGDirection.BEARISH:
            return 0

        distance_pct = abs(current_price - fvg.midpoint) / current_price * 100
        if distance_pct <= 1.0:
            proximity_factor = 1 - (distance_pct * 0.35)
        else:
            proximity_factor = 0.35

        lifecycle_factor = 1.0
        if fvg.fill_percent >= 100:
            lifecycle_factor = 0
        elif fvg.fill_percent > 50:
            lifecycle_factor = 0.75

        return max(min(fvg.quality_score * proximity_factor * lifecycle_factor, 100), 0)

    def _score_vwap(self, context: SignalContext, direction: Direction) -> float:
        if not context.vwap_signals:
            return 0

        score = 0.0
        for signal in context.vwap_signals:
            above = signal.current_price > signal.vwap_price
            below = signal.current_price < signal.vwap_price
            if direction == Direction.LONG:
                if signal.signal_type == "RECLAIM" and above:
                    score = max(score, signal.confidence)
                elif signal.signal_type == "CROSS_WITH_VOLUME" and above:
                    score = max(score, signal.confidence * 0.9)
                elif signal.signal_type == "REJECTION" and above:
                    score = max(score, signal.confidence * 0.7)
            else:
                if signal.signal_type == "REJECTION" and below:
                    score = max(score, signal.confidence)
                elif signal.signal_type == "CROSS_WITH_VOLUME" and below:
                    score = max(score, signal.confidence * 0.9)
        return score

    def _score_volume_profile(self, context: SignalContext, direction: Direction) -> float:
        if not context.volume_profile:
            return 0

        vp = context.volume_profile
        price = context.current_price
        score = 0.0

        poc_distance = abs(price - vp.poc_price) / vp.poc_price * 100
        if poc_distance < 0.5:
            if direction == Direction.LONG and price >= vp.poc_price * 0.998:
                score = max(score, 80)
            if direction == Direction.SHORT and price <= vp.poc_price * 1.002:
                score = max(score, 80)

        if direction == Direction.LONG:
            val_distance = abs(price - vp.value_area_low) / vp.value_area_low * 100
            if val_distance < 0.3:
                score = max(score, 70)
        else:
            vah_distance = abs(price - vp.value_area_high) / vp.value_area_high * 100
            if vah_distance < 0.3:
                score = max(score, 70)

        for hvn in vp.high_volume_nodes:
            hvn_distance = abs(price - hvn["price"]) / hvn["price"] * 100
            if hvn_distance < 0.3:
                if direction == Direction.LONG and price >= hvn["price"] * 0.998:
                    score = max(score, min(hvn["strength"] * 30, 75))
                if direction == Direction.SHORT and price <= hvn["price"] * 1.002:
                    score = max(score, min(hvn["strength"] * 30, 75))
        return score

    def _score_volume_confirmation(
        self, order_flow: Optional[OrderFlowSnapshot], direction: Direction
    ) -> float:
        if not order_flow:
            return 0

        delta_aligned = (
            direction == Direction.LONG and order_flow.volume_delta > 0
        ) or (
            direction == Direction.SHORT and order_flow.volume_delta < 0
        )
        if not delta_aligned:
            return 0

        delta_score = min(abs(order_flow.volume_delta) / 1000, 1.0) * 55
        rvol_score = min(order_flow.relative_volume / 2.0, 1.0) * 35
        large_trade_bonus = 10 if order_flow.large_trade_detected else 0
        return min(delta_score + rvol_score + large_trade_bonus, 100)

    def _build_reasons(
        self, context: SignalContext, direction: Direction, components: dict[str, float]
    ) -> list[str]:
        reasons = []
        if components["liquidity_sweep"] > 0 and context.liquidity_sweep:
            sweep = context.liquidity_sweep
            liq_type = sweep.liquidity_type.replace("_", " ").title() if sweep.liquidity_type else sweep.zone_type
            reasons.append(f"{liq_type} sweep ({sweep.sweep_strength:.0f})")

        if components["market_structure"] > 0 and context.market_structure:
            ms = context.market_structure
            reasons.append(f"{ms.trend.value.title()} {ms.structure_type.value}")

        if components["fvg"] > 0 and context.nearest_fvg:
            reasons.append(f"{context.nearest_fvg.direction.value.title()} FVG")

        if components["vwap"] > 0:
            reasons.append("VWAP alignment")

        if components["volume_profile"] > 0:
            reasons.append("Volume profile support/resistance")

        if components["volume_confirmation"] > 0:
            reasons.append("Positive volume confirmation" if direction == Direction.LONG else "Negative volume confirmation")
        return reasons


class SignalAggregator:
    """Aggregates all detection engine outputs into actionable trade signals."""

    WEIGHT_MARKET_STRUCTURE = ConfidenceEngine.WEIGHTS["market_structure"]
    WEIGHT_LIQUIDITY = ConfidenceEngine.WEIGHTS["liquidity_sweep"]
    WEIGHT_FVG = ConfidenceEngine.WEIGHTS["fvg"]
    WEIGHT_VWAP = ConfidenceEngine.WEIGHTS["vwap"]
    WEIGHT_VOLUME_PROFILE = ConfidenceEngine.WEIGHTS["volume_profile"]
    WEIGHT_VOLUME_CONFIRMATION = ConfidenceEngine.WEIGHTS["volume_confirmation"]

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
        self._confidence = ConfidenceEngine()
        self._min_score = settings.signal.min_confidence_score

    def evaluate(self, context: SignalContext) -> Optional[SignalCreate]:
        long_result = self._confidence.score(context, Direction.LONG)
        short_result = self._confidence.score(context, Direction.SHORT)

        if long_result.score >= short_result.score and long_result.score >= self._min_score:
            return self._build_signal(context, Direction.LONG, long_result)
        if short_result.score > long_result.score and short_result.score >= self._min_score:
            return self._build_signal(context, Direction.SHORT, short_result)
        return None

    def _score_long(self, context: SignalContext) -> float:
        return self._confidence.score(context, Direction.LONG).score

    def _score_short(self, context: SignalContext) -> float:
        return self._confidence.score(context, Direction.SHORT).score

    def _score_vwap_long(self, context: SignalContext) -> float:
        return self._confidence._score_vwap(context, Direction.LONG)

    def _score_vwap_short(self, context: SignalContext) -> float:
        return self._confidence._score_vwap(context, Direction.SHORT)

    def _score_volume_profile_long(self, context: SignalContext) -> float:
        return self._confidence._score_volume_profile(context, Direction.LONG)

    def _score_volume_profile_short(self, context: SignalContext) -> float:
        return self._confidence._score_volume_profile(context, Direction.SHORT)

    def _build_signal(
        self, context: SignalContext, direction: Direction, result: ConfidenceResult
    ) -> SignalCreate:
        atr = context.atr if context.atr > 0 else context.current_price * 0.005

        if direction == Direction.LONG:
            entry_low = context.current_price
            entry_high = context.current_price + atr * 0.3
            stop_loss = context.current_price - atr * 1.5
            target_1 = context.current_price + atr * 2.0
            target_2 = context.current_price + atr * 3.5

            if context.nearest_fvg and context.nearest_fvg.direction == FVGDirection.BULLISH:
                stop_loss = min(stop_loss, context.nearest_fvg.gap_low - atr * 0.2)
            if context.liquidity_sweep and context.liquidity_sweep.zone_type == "SELL_SIDE":
                stop_loss = min(stop_loss, context.liquidity_sweep.sweep_price - atr * 0.2)
        else:
            entry_low = context.current_price - atr * 0.3
            entry_high = context.current_price
            stop_loss = context.current_price + atr * 1.5
            target_1 = context.current_price - atr * 2.0
            target_2 = context.current_price - atr * 3.5

            if context.nearest_fvg and context.nearest_fvg.direction == FVGDirection.BEARISH:
                stop_loss = max(stop_loss, context.nearest_fvg.gap_high + atr * 0.2)
            if context.liquidity_sweep and context.liquidity_sweep.zone_type == "BUY_SIDE":
                stop_loss = max(stop_loss, context.liquidity_sweep.sweep_price + atr * 0.2)

        reasoning = self._build_reasoning(context, direction, result)
        components = result.component_scores

        return SignalCreate(
            symbol=context.symbol,
            direction=direction,
            entry_zone_low=round(entry_low, 2),
            entry_zone_high=round(entry_high, 2),
            stop_loss=round(stop_loss, 2),
            target_1=round(target_1, 2),
            target_2=round(target_2, 2),
            confidence_score=round(result.score, 1),
            market_structure_score=round(components.get("market_structure", 0), 1),
            liquidity_sweep_score=round(components.get("liquidity_sweep", 0), 1),
            fvg_score=round(components.get("fvg", 0), 1),
            vwap_score=round(components.get("vwap", 0), 1),
            volume_profile_score=round(components.get("volume_profile", 0), 1),
            order_flow_score=round(components.get("volume_confirmation", 0), 1),
            volume_confirmation_score=round(components.get("volume_confirmation", 0), 1),
            reasoning=reasoning,
            timeframe=context.timeframe,
            explainability={
                "components": result.component_scores,
                "weighted": result.weighted_scores,
                "reasons": result.reasons,
            },
        )

    def _build_reasoning(
        self, context: SignalContext, direction: Direction, result: ConfidenceResult
    ) -> str:
        parts = list(result.reasons)

        if context.liquidity_sweep:
            sweep = context.liquidity_sweep
            parts.append(
                f"{'Sell' if sweep.zone_type == 'SELL_SIDE' else 'Buy'}-side liquidity swept "
                f"at {sweep.price_level:.2f} (strength {sweep.sweep_strength:.0f})"
            )

        if context.nearest_fvg:
            fvg = context.nearest_fvg
            parts.append(
                f"{fvg.direction.value} FVG [{fvg.gap_low:.2f}-{fvg.gap_high:.2f}] "
                f"quality {fvg.quality_score:.0f}"
            )

        if context.vwap_signals:
            best_vwap = max(context.vwap_signals, key=lambda v: v.confidence)
            parts.append(
                f"VWAP {best_vwap.signal_type} from {best_vwap.anchor_type} "
                f"at {best_vwap.vwap_price:.2f}"
            )

        if context.order_flow:
            of = context.order_flow
            parts.append(f"Delta {of.volume_delta:+.0f}, RVOL {of.relative_volume:.1f}x")

        # Keep notification prose compact and unique.
        deduped = []
        for part in parts:
            if part not in deduped:
                deduped.append(part)
        return " | ".join(deduped)

    def calculate_atr(self, candles: list[CandleData], period: int = 14) -> float:
        if len(candles) < period + 1:
            return (candles[-1].high - candles[-1].low) if candles else 0

        true_ranges = []
        for i in range(1, len(candles)):
            high = candles[i].high
            low = candles[i].low
            prev_close = candles[i - 1].close
            true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        return float(np.mean(true_ranges[-period:]))
