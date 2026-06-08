"""
Institutional market structure engine.

The engine keeps the legacy public API while adding:
- External and internal swing streams
- Protected highs/lows
- Bullish and bearish BOS
- Bullish and bearish CHoCH
- Market structure shift tagging
- Displacement scoring
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import numpy as np

from trading_system.core.schemas import (
    CandleData,
    Direction,
    MarketStructurePoint,
    StructureScope,
    StructureType,
    Trend,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SwingPoint:
    index: int
    timestamp: datetime
    price: float
    kind: str
    scope: StructureScope
    label: Optional[StructureType] = None
    strength: float = 0

    def with_label(self, label: Optional[StructureType]) -> "SwingPoint":
        return SwingPoint(
            index=self.index,
            timestamp=self.timestamp,
            price=self.price,
            kind=self.kind,
            scope=self.scope,
            label=label,
            strength=self.strength,
        )

    def as_dict(self) -> dict:
        return {
            "price": self.price,
            "timestamp": self.timestamp,
            "index": self.index,
            "kind": self.kind,
            "scope": self.scope.value,
            "label": self.label.value if self.label else None,
            "strength": self.strength,
        }


@dataclass
class StructureState:
    trend: Trend = Trend.RANGING
    protected_high: Optional[SwingPoint] = None
    protected_low: Optional[SwingPoint] = None
    last_high: Optional[SwingPoint] = None
    last_low: Optional[SwingPoint] = None
    last_event: Optional[MarketStructurePoint] = None


class MarketStructureEngine:
    """Analyzes institutional market structure for trend and key levels."""

    def __init__(
        self,
        swing_lookback: int = 5,
        internal_lookback: Optional[int] = None,
        external_lookback: Optional[int] = None,
        displacement_atr_multiplier: float = 1.15,
        displacement_body_ratio: float = 0.55,
        min_displacement_score: float = 35,
        close_break: bool = True,
    ):
        """
        Args:
            swing_lookback: Backward-compatible lookback used for internal pivots.
            internal_lookback: Minor structure pivot width.
            external_lookback: Major structure pivot width.
            displacement_atr_multiplier: Range/ATR threshold for strong impulse candles.
            displacement_body_ratio: Body/range threshold for strong impulse candles.
            min_displacement_score: Minimum score to tag a CHoCH as an MSS.
            close_break: Require close through structure instead of wick-only break.
        """
        self._swing_lookback = swing_lookback
        self._internal_lookback = internal_lookback or swing_lookback
        self._external_lookback = external_lookback or max(swing_lookback * 3, swing_lookback + 2)
        self._displacement_atr_multiplier = displacement_atr_multiplier
        self._displacement_body_ratio = displacement_body_ratio
        self._min_displacement_score = min_displacement_score
        self._close_break = close_break

        self._swing_highs: dict[str, list[dict]] = {}
        self._swing_lows: dict[str, list[dict]] = {}
        self._structure_points: dict[str, list[MarketStructurePoint]] = {}
        self._current_trend: dict[str, Trend] = {}
        self._states: dict[str, StructureState] = {}
        self._swings: dict[str, list[SwingPoint]] = {}

    def analyze(
        self, candles: list[CandleData], symbol: str, timeframe: str
    ) -> list[MarketStructurePoint]:
        """Run full internal and external market structure analysis."""
        key = f"{symbol}:{timeframe}"
        if len(candles) < (self._internal_lookback * 2) + 1:
            self._structure_points[key] = []
            self._current_trend[key] = Trend.RANGING
            return []

        atr_values = self._atr_values(candles)

        internal_swings = self._label_swings(
            self._find_swing_points(candles, self._internal_lookback, StructureScope.INTERNAL)
        )
        external_swings = self._label_swings(
            self._find_swing_points(candles, self._external_lookback, StructureScope.EXTERNAL)
        )

        # If the external window is too wide for the available history, promote the
        # cleaner internal stream so the engine still emits a usable state.
        if len(external_swings) < 4:
            external_swings = [
                SwingPoint(
                    index=s.index,
                    timestamp=s.timestamp,
                    price=s.price,
                    kind=s.kind,
                    scope=StructureScope.EXTERNAL,
                    label=s.label,
                    strength=s.strength,
                )
                for s in internal_swings
            ]

        all_swings = self._merge_swings(external_swings, internal_swings)
        self._swings[key] = all_swings
        self._swing_highs[key] = [s.as_dict() for s in all_swings if s.kind == "HIGH"]
        self._swing_lows[key] = [s.as_dict() for s in all_swings if s.kind == "LOW"]

        points: list[MarketStructurePoint] = []
        points.extend(self._swing_labels_to_points(external_swings, symbol, timeframe))
        points.extend(self._swing_labels_to_points(internal_swings, symbol, timeframe))
        points.extend(
            self._detect_structure_events(
                candles,
                external_swings,
                symbol,
                timeframe,
                StructureScope.EXTERNAL,
                self._external_lookback,
                atr_values,
            )
        )
        points.extend(
            self._detect_structure_events(
                candles,
                internal_swings,
                symbol,
                timeframe,
                StructureScope.INTERNAL,
                self._internal_lookback,
                atr_values,
            )
        )

        points.sort(key=lambda p: (p.timestamp, p.source_index or -1, p.structure_type.value))
        self._structure_points[key] = points
        self._current_trend[key] = self._get_trend(points)
        self._states[key] = self._build_latest_state(points, external_swings)
        return points

    def _find_swing_points(
        self, candles: list[CandleData], lookback: int, scope: StructureScope
    ) -> list[SwingPoint]:
        """Find and clean alternating swing points."""
        if len(candles) < (lookback * 2) + 1:
            return []

        highs = np.array([c.high for c in candles], dtype=float)
        lows = np.array([c.low for c in candles], dtype=float)
        atr_values = self._atr_values(candles)
        raw: list[SwingPoint] = []

        for i in range(lookback, len(candles) - lookback):
            left_high = highs[i - lookback : i]
            right_high = highs[i + 1 : i + lookback + 1]
            left_low = lows[i - lookback : i]
            right_low = lows[i + 1 : i + lookback + 1]

            is_high = highs[i] > left_high.max() and highs[i] >= right_high.max()
            is_low = lows[i] < left_low.min() and lows[i] <= right_low.min()

            if is_high and is_low:
                body_bias = candles[i].close >= candles[i].open
                is_low = not body_bias
                is_high = body_bias

            if is_high:
                neighbor_high = max(left_high.max(), right_high.max())
                strength = self._swing_strength(highs[i] - neighbor_high, atr_values[i])
                raw.append(
                    SwingPoint(
                        index=i,
                        timestamp=candles[i].timestamp,
                        price=float(highs[i]),
                        kind="HIGH",
                        scope=scope,
                        strength=strength,
                    )
                )

            if is_low:
                neighbor_low = min(left_low.min(), right_low.min())
                strength = self._swing_strength(neighbor_low - lows[i], atr_values[i])
                raw.append(
                    SwingPoint(
                        index=i,
                        timestamp=candles[i].timestamp,
                        price=float(lows[i]),
                        kind="LOW",
                        scope=scope,
                        strength=strength,
                    )
                )

        return self._clean_alternating(raw)

    def _clean_alternating(self, swings: list[SwingPoint]) -> list[SwingPoint]:
        """
        Remove consecutive same-side pivots, keeping the more extreme point.

        This mirrors the useful idea from the reference SMC implementation without
        copying it: pivots must form one alternating stream before structure logic
        is allowed to classify BOS/CHoCH.
        """
        if len(swings) < 2:
            return swings

        cleaned = sorted(swings, key=lambda s: s.index)
        changed = True
        while changed and len(cleaned) >= 2:
            changed = False
            next_cleaned: list[SwingPoint] = []
            i = 0
            while i < len(cleaned):
                current = cleaned[i]
                if i + 1 < len(cleaned) and current.kind == cleaned[i + 1].kind:
                    nxt = cleaned[i + 1]
                    if current.kind == "HIGH":
                        keep = current if current.price >= nxt.price else nxt
                    else:
                        keep = current if current.price <= nxt.price else nxt
                    next_cleaned.append(keep)
                    i += 2
                    changed = True
                else:
                    next_cleaned.append(current)
                    i += 1
            cleaned = next_cleaned
        return cleaned

    def _label_swings(self, swings: list[SwingPoint]) -> list[SwingPoint]:
        last_high: Optional[SwingPoint] = None
        last_low: Optional[SwingPoint] = None
        labeled: list[SwingPoint] = []

        for swing in swings:
            label = None
            if swing.kind == "HIGH":
                if last_high:
                    label = StructureType.HH if swing.price > last_high.price else StructureType.LH
                last_high = swing
            else:
                if last_low:
                    label = StructureType.HL if swing.price > last_low.price else StructureType.LL
                last_low = swing
            labeled.append(swing.with_label(label))

        return labeled

    def _merge_swings(
        self, external_swings: list[SwingPoint], internal_swings: list[SwingPoint]
    ) -> list[SwingPoint]:
        seen = set()
        merged: list[SwingPoint] = []
        for swing in external_swings + internal_swings:
            key = (swing.index, swing.kind, swing.scope)
            if key not in seen:
                seen.add(key)
                merged.append(swing)
        return sorted(merged, key=lambda s: (s.index, 0 if s.scope == StructureScope.EXTERNAL else 1))

    def _swing_labels_to_points(
        self, swings: list[SwingPoint], symbol: str, timeframe: str
    ) -> list[MarketStructurePoint]:
        points = []
        for swing in swings:
            if not swing.label:
                continue
            trend = (
                Trend.BULLISH
                if swing.label in {StructureType.HH, StructureType.HL}
                else Trend.BEARISH
            )
            points.append(
                MarketStructurePoint(
                    symbol=symbol,
                    timeframe=timeframe,
                    structure_type=swing.label,
                    price=swing.price,
                    timestamp=swing.timestamp,
                    trend=trend,
                    scope=swing.scope,
                    strength_score=swing.strength,
                    source_index=swing.index,
                    metadata={"swing_kind": swing.kind},
                )
            )
        return points

    def _detect_structure_events(
        self,
        candles: list[CandleData],
        swings: list[SwingPoint],
        symbol: str,
        timeframe: str,
        scope: StructureScope,
        confirmation_lookback: int,
        atr_values: np.ndarray,
    ) -> list[MarketStructurePoint]:
        if len(swings) < 3:
            return []

        state = StructureState(trend=self._trend_from_recent_swings(swings[:4]))
        events: list[MarketStructurePoint] = []
        confirmed_by_index: dict[int, list[SwingPoint]] = {}
        broken_highs: set[int] = set()
        broken_lows: set[int] = set()

        for swing in swings:
            confirmed_at = min(swing.index + confirmation_lookback, len(candles) - 1)
            confirmed_by_index.setdefault(confirmed_at, []).append(swing)

        for i, candle in enumerate(candles):
            for swing in confirmed_by_index.get(i, []):
                if swing.kind == "HIGH":
                    state.last_high = swing
                    if state.trend == Trend.BEARISH:
                        state.protected_high = swing
                else:
                    state.last_low = swing
                    if state.trend == Trend.BULLISH:
                        state.protected_low = swing

            high_target = self._active_high_target(state)
            low_target = self._active_low_target(state)

            if high_target and high_target.index not in broken_highs:
                if self._breaks_above(candle, high_target.price):
                    event = self._make_break_event(
                        candles=candles,
                        index=i,
                        target=high_target,
                        symbol=symbol,
                        timeframe=timeframe,
                        scope=scope,
                        state=state,
                        direction=Direction.LONG,
                        atr_values=atr_values,
                    )
                    if event:
                        events.extend(event)
                        broken_highs.add(high_target.index)
                        state.trend = Trend.BULLISH
                        state.protected_low = self._last_swing_before(swings, "LOW", i)
                        state.last_event = event[-1]

            if low_target and low_target.index not in broken_lows:
                if self._breaks_below(candle, low_target.price):
                    event = self._make_break_event(
                        candles=candles,
                        index=i,
                        target=low_target,
                        symbol=symbol,
                        timeframe=timeframe,
                        scope=scope,
                        state=state,
                        direction=Direction.SHORT,
                        atr_values=atr_values,
                    )
                    if event:
                        events.extend(event)
                        broken_lows.add(low_target.index)
                        state.trend = Trend.BEARISH
                        state.protected_high = self._last_swing_before(swings, "HIGH", i)
                        state.last_event = event[-1]

        return events

    def _active_high_target(self, state: StructureState) -> Optional[SwingPoint]:
        if state.trend == Trend.BEARISH and state.protected_high:
            return state.protected_high
        return state.last_high

    def _active_low_target(self, state: StructureState) -> Optional[SwingPoint]:
        if state.trend == Trend.BULLISH and state.protected_low:
            return state.protected_low
        return state.last_low

    def _make_break_event(
        self,
        candles: list[CandleData],
        index: int,
        target: SwingPoint,
        symbol: str,
        timeframe: str,
        scope: StructureScope,
        state: StructureState,
        direction: Direction,
        atr_values: np.ndarray,
    ) -> list[MarketStructurePoint]:
        prior_trend = state.trend
        new_trend = Trend.BULLISH if direction == Direction.LONG else Trend.BEARISH
        displacement = self._displacement_score(candles, index, atr_values, direction)
        structure_type = self._classify_break(prior_trend, direction, target)

        if structure_type in {StructureType.BOS, StructureType.CHOCH} and displacement < 15:
            return []

        candle = candles[index]
        event_score = self._break_strength(candles, index, target.price, displacement, target)
        protected_low = state.protected_low.price if state.protected_low else None
        protected_high = state.protected_high.price if state.protected_high else None

        point = MarketStructurePoint(
            symbol=symbol,
            timeframe=timeframe,
            structure_type=structure_type,
            price=target.price,
            timestamp=candle.timestamp,
            trend=new_trend,
            scope=scope,
            direction=direction,
            broken_level=target.price,
            break_price=candle.close if self._close_break else candle.high,
            protected_high=protected_high,
            protected_low=protected_low,
            displacement_score=displacement,
            strength_score=event_score,
            source_index=target.index,
            broken_index=index,
            metadata={
                "prior_trend": prior_trend.value,
                "target_kind": target.kind,
                "target_label": target.label.value if target.label else None,
            },
        )

        events = [point]
        if structure_type == StructureType.CHOCH and displacement >= self._min_displacement_score:
            events.append(
                point.model_copy(
                    update={
                        "structure_type": StructureType.MSS,
                        "strength_score": min(event_score + 10, 100),
                        "metadata": {
                            **point.metadata,
                            "mss_reason": "CHoCH confirmed with displacement",
                        },
                    }
                )
            )
        return events

    def _classify_break(
        self, prior_trend: Trend, direction: Direction, target: SwingPoint
    ) -> StructureType:
        if direction == Direction.LONG:
            if prior_trend == Trend.BEARISH:
                return StructureType.CHOCH
            if prior_trend == Trend.RANGING and target.label == StructureType.LH:
                return StructureType.CHOCH
            return StructureType.BOS

        if prior_trend == Trend.BULLISH:
            return StructureType.CHOCH
        if prior_trend == Trend.RANGING and target.label == StructureType.HL:
            return StructureType.CHOCH
        return StructureType.BOS

    def _breaks_above(self, candle: CandleData, level: float) -> bool:
        return (candle.close if self._close_break else candle.high) > level

    def _breaks_below(self, candle: CandleData, level: float) -> bool:
        return (candle.close if self._close_break else candle.low) < level

    def _last_swing_before(
        self, swings: list[SwingPoint], kind: str, candle_index: int
    ) -> Optional[SwingPoint]:
        candidates = [s for s in swings if s.kind == kind and s.index < candle_index]
        return candidates[-1] if candidates else None

    def _trend_from_recent_swings(self, swings: list[SwingPoint]) -> Trend:
        labels = [s.label for s in swings if s.label]
        recent = labels[-4:]
        bullish = sum(1 for label in recent if label in {StructureType.HH, StructureType.HL})
        bearish = sum(1 for label in recent if label in {StructureType.LH, StructureType.LL})
        if bullish >= 2 and bullish > bearish:
            return Trend.BULLISH
        if bearish >= 2 and bearish > bullish:
            return Trend.BEARISH
        return Trend.RANGING

    def _swing_strength(self, excursion: float, atr: float) -> float:
        if atr <= 0:
            return 0
        return float(min(max((excursion / atr) * 40, 0), 100))

    def _displacement_score(
        self,
        candles: list[CandleData],
        index: int,
        atr_values: np.ndarray,
        direction: Direction,
    ) -> float:
        candle = candles[index]
        candle_range = candle.high - candle.low
        if candle_range <= 0:
            return 0

        body = abs(candle.close - candle.open)
        body_ratio = body / candle_range
        atr = atr_values[index] if index < len(atr_values) else candle_range
        range_ratio = candle_range / atr if atr > 0 else 1

        directional_close = 0.0
        if direction == Direction.LONG:
            directional_close = (candle.close - candle.low) / candle_range
        else:
            directional_close = (candle.high - candle.close) / candle_range

        body_score = min(body_ratio / self._displacement_body_ratio, 1.0) * 40
        range_score = min(range_ratio / self._displacement_atr_multiplier, 1.0) * 35
        close_score = min(directional_close, 1.0) * 25
        return float(min(body_score + range_score + close_score, 100))

    def _break_strength(
        self,
        candles: list[CandleData],
        index: int,
        level: float,
        displacement: float,
        target: SwingPoint,
    ) -> float:
        candle = candles[index]
        atr = self._atr_values(candles)[index]
        distance = abs(candle.close - level)
        distance_score = min(distance / atr if atr > 0 else 0, 1.0) * 25
        swing_score = min(target.strength, 100) * 0.20
        return float(min(displacement * 0.55 + distance_score + swing_score, 100))

    def _atr_values(self, candles: list[CandleData], period: int = 14) -> np.ndarray:
        if not candles:
            return np.array([])

        true_ranges = np.zeros(len(candles), dtype=float)
        for i, candle in enumerate(candles):
            if i == 0:
                true_ranges[i] = candle.high - candle.low
            else:
                prev_close = candles[i - 1].close
                true_ranges[i] = max(
                    candle.high - candle.low,
                    abs(candle.high - prev_close),
                    abs(candle.low - prev_close),
                )

        atr = np.zeros(len(candles), dtype=float)
        for i in range(len(candles)):
            start = max(0, i - period + 1)
            atr[i] = float(np.mean(true_ranges[start : i + 1]))
        return atr

    def _build_latest_state(
        self, points: list[MarketStructurePoint], swings: list[SwingPoint]
    ) -> StructureState:
        state = StructureState(trend=self._get_trend(points))
        state.last_high = next((s for s in reversed(swings) if s.kind == "HIGH"), None)
        state.last_low = next((s for s in reversed(swings) if s.kind == "LOW"), None)
        state.protected_high = next((s for s in reversed(swings) if s.kind == "HIGH"), None)
        state.protected_low = next((s for s in reversed(swings) if s.kind == "LOW"), None)
        for point in reversed(points):
            if point.structure_type in {StructureType.BOS, StructureType.CHOCH, StructureType.MSS}:
                state.last_event = point
                break
        return state

    def _get_trend(self, structure_points: list[MarketStructurePoint]) -> Trend:
        if not structure_points:
            return Trend.RANGING

        for point in reversed(structure_points):
            if point.structure_type in {StructureType.BOS, StructureType.CHOCH, StructureType.MSS}:
                return point.trend

        recent = [
            p
            for p in structure_points[-8:]
            if p.structure_type in {StructureType.HH, StructureType.HL, StructureType.LH, StructureType.LL}
        ]
        bullish_count = sum(1 for p in recent if p.trend == Trend.BULLISH)
        bearish_count = sum(1 for p in recent if p.trend == Trend.BEARISH)

        if bullish_count > bearish_count:
            return Trend.BULLISH
        if bearish_count > bullish_count:
            return Trend.BEARISH
        return Trend.RANGING

    def get_trend(self, symbol: str, timeframe: str) -> Trend:
        key = f"{symbol}:{timeframe}"
        return self._current_trend.get(key, Trend.RANGING)

    def get_swing_highs(self, symbol: str, timeframe: str) -> list[dict]:
        key = f"{symbol}:{timeframe}"
        return self._swing_highs.get(key, [])

    def get_swing_lows(self, symbol: str, timeframe: str) -> list[dict]:
        key = f"{symbol}:{timeframe}"
        return self._swing_lows.get(key, [])

    def get_latest_structure(self, symbol: str, timeframe: str) -> Optional[MarketStructurePoint]:
        key = f"{symbol}:{timeframe}"
        points = [
            p
            for p in self._structure_points.get(key, [])
            if p.structure_type != StructureType.DISPLACEMENT
        ]
        return points[-1] if points else None

    def get_state(self, symbol: str, timeframe: str) -> StructureState:
        key = f"{symbol}:{timeframe}"
        return self._states.get(key, StructureState())

    def get_swings(self, symbol: str, timeframe: str) -> list[dict]:
        key = f"{symbol}:{timeframe}"
        return [s.as_dict() for s in self._swings.get(key, [])]
