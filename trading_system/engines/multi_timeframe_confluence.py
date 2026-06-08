"""
Multi-timeframe structure confluence.

Canonical model:
- 15m external structure defines directional bias
- 5m internal structure defines pullback or continuation context
- 1m execution structure confirms entry timing
"""
from dataclasses import dataclass, field
from typing import Optional

from trading_system.core.schemas import (
    Direction,
    FVGData,
    FVGDirection,
    LiquiditySweepEvent,
    MarketStructurePoint,
    StructureType,
    Trend,
)


@dataclass
class ConfluenceResult:
    direction: Optional[Direction]
    score: float
    reasons: list[str] = field(default_factory=list)
    aligned: bool = False


class MultiTimeframeConfluenceEngine:
    """Scores HTF bias, internal pullback, and execution confirmation."""

    def __init__(
        self,
        external_timeframe: str = "15m",
        internal_timeframe: str = "5m",
        execution_timeframe: str = "1m",
    ):
        self.external_timeframe = external_timeframe
        self.internal_timeframe = internal_timeframe
        self.execution_timeframe = execution_timeframe

    def evaluate(
        self,
        structures_by_timeframe: dict[str, MarketStructurePoint | list[MarketStructurePoint]],
        liquidity_sweep: Optional[LiquiditySweepEvent] = None,
        fvg: Optional[FVGData] = None,
    ) -> ConfluenceResult:
        external = self._latest(structures_by_timeframe.get(self.external_timeframe))
        internal = self._latest(structures_by_timeframe.get(self.internal_timeframe))
        execution = self._latest(structures_by_timeframe.get(self.execution_timeframe))

        long_score, long_reasons = self._score_direction(
            Direction.LONG, external, internal, execution, liquidity_sweep, fvg
        )
        short_score, short_reasons = self._score_direction(
            Direction.SHORT, external, internal, execution, liquidity_sweep, fvg
        )

        if long_score == 0 and short_score == 0:
            return ConfluenceResult(direction=None, score=0, reasons=[], aligned=False)

        if long_score >= short_score:
            return ConfluenceResult(
                direction=Direction.LONG,
                score=long_score,
                reasons=long_reasons,
                aligned=long_score >= 70,
            )
        return ConfluenceResult(
            direction=Direction.SHORT,
            score=short_score,
            reasons=short_reasons,
            aligned=short_score >= 70,
        )

    def _score_direction(
        self,
        direction: Direction,
        external: Optional[MarketStructurePoint],
        internal: Optional[MarketStructurePoint],
        execution: Optional[MarketStructurePoint],
        liquidity_sweep: Optional[LiquiditySweepEvent],
        fvg: Optional[FVGData],
    ) -> tuple[float, list[str]]:
        trend = Trend.BULLISH if direction == Direction.LONG else Trend.BEARISH
        reasons = []
        score = 0.0

        if external and external.trend == trend:
            score += 35
            reasons.append(f"{self.external_timeframe} {trend.value.title()} external structure")

        if internal:
            if internal.trend != trend:
                score += 15
                reasons.append(f"{self.internal_timeframe} pullback against external bias")
            elif internal.structure_type in {StructureType.BOS, StructureType.HL, StructureType.LH}:
                score += 10
                reasons.append(f"{self.internal_timeframe} internal continuation")

        if execution and execution.trend == trend:
            if execution.structure_type in {StructureType.CHOCH, StructureType.MSS}:
                score += 30
                reasons.append(f"{self.execution_timeframe} execution CHoCH/MSS")
            elif execution.structure_type == StructureType.BOS:
                score += 22
                reasons.append(f"{self.execution_timeframe} execution BOS")

        if liquidity_sweep:
            bullish_sweep = direction == Direction.LONG and liquidity_sweep.zone_type == "SELL_SIDE"
            bearish_sweep = direction == Direction.SHORT and liquidity_sweep.zone_type == "BUY_SIDE"
            if bullish_sweep or bearish_sweep:
                score += min(liquidity_sweep.sweep_strength / 100, 1.0) * 12
                reasons.append("Directional liquidity sweep")

        if fvg:
            bullish_fvg = direction == Direction.LONG and fvg.direction == FVGDirection.BULLISH
            bearish_fvg = direction == Direction.SHORT and fvg.direction == FVGDirection.BEARISH
            if bullish_fvg or bearish_fvg:
                score += min(fvg.quality_score / 100, 1.0) * 8
                reasons.append("Directional FVG")

        return min(score, 100), reasons

    def _latest(
        self, value: Optional[MarketStructurePoint | list[MarketStructurePoint]]
    ) -> Optional[MarketStructurePoint]:
        if isinstance(value, list):
            return value[-1] if value else None
        return value
