"""
Liquidity pool and sweep engines.

Pools model where resting liquidity likely exists. Sweeps are valid only after a
pre-existing pool is breached, rejected, and followed by displacement.
"""
import logging
from collections import defaultdict
from datetime import date
from typing import Optional

import numpy as np

from trading_system.config.settings import settings
from trading_system.core.schemas import (
    CandleData,
    Direction,
    LiquidityPoolData,
    LiquidityPoolType,
    LiquiditySweepEvent,
    LiquidityZoneData,
)

logger = logging.getLogger(__name__)


class LiquidityPoolEngine:
    """Detects and scores buy-side and sell-side liquidity pools."""

    def __init__(
        self,
        tolerance: Optional[float] = None,
        lookback: Optional[int] = None,
        swing_lookback: int = 3,
    ):
        self._tolerance = tolerance if tolerance is not None else settings.signal.equal_level_tolerance
        self._lookback = lookback or settings.signal.lookback_periods
        self._swing_lookback = swing_lookback

    def detect_pools(
        self, candles: list[CandleData], min_touches: int = 2
    ) -> list[LiquidityPoolData]:
        if len(candles) < max(6, self._swing_lookback * 2 + 1):
            return []

        window = candles[-self._lookback :] if len(candles) > self._lookback else candles
        offset = len(candles) - len(window)
        atr = self._atr(window)
        pivots = self._find_pivots(window, offset)

        pools: list[LiquidityPoolData] = []
        pools.extend(
            self._cluster_pivots(
                candles=window,
                pivots=[p for p in pivots if p["kind"] == "HIGH"],
                side="BUY_SIDE",
                min_touches=min_touches,
                atr=atr,
            )
        )
        pools.extend(
            self._cluster_pivots(
                candles=window,
                pivots=[p for p in pivots if p["kind"] == "LOW"],
                side="SELL_SIDE",
                min_touches=min_touches,
                atr=atr,
            )
        )
        pools.extend(self._session_pools(candles))
        pools.extend(self._previous_day_pools(candles))
        pools.extend(self._weekly_pools(candles))

        return self._dedupe_pools(pools)

    def _find_pivots(self, candles: list[CandleData], offset: int) -> list[dict]:
        highs = np.array([c.high for c in candles], dtype=float)
        lows = np.array([c.low for c in candles], dtype=float)
        pivots = []
        n = self._swing_lookback

        for i in range(n, len(candles) - n):
            high = highs[i]
            low = lows[i]
            if high > highs[i - n : i].max() and high >= highs[i + 1 : i + n + 1].max():
                pivots.append(
                    {
                        "kind": "HIGH",
                        "price": float(high),
                        "index": i + offset,
                        "timestamp": candles[i].timestamp,
                        "volume": candles[i].volume,
                    }
                )
            if low < lows[i - n : i].min() and low <= lows[i + 1 : i + n + 1].min():
                pivots.append(
                    {
                        "kind": "LOW",
                        "price": float(low),
                        "index": i + offset,
                        "timestamp": candles[i].timestamp,
                        "volume": candles[i].volume,
                    }
                )

        return sorted(pivots, key=lambda p: p["index"])

    def _cluster_pivots(
        self,
        candles: list[CandleData],
        pivots: list[dict],
        side: str,
        min_touches: int,
        atr: float,
    ) -> list[LiquidityPoolData]:
        pools = []
        used: set[int] = set()
        latest_global_index = pivots[-1]["index"] if pivots else len(candles) - 1

        for i, pivot in enumerate(pivots):
            if i in used:
                continue

            band = self._price_band(pivot["price"], atr)
            group = [pivot]
            used_local = [i]

            for j in range(i + 1, len(pivots)):
                candidate = pivots[j]
                if abs(candidate["price"] - pivot["price"]) <= band:
                    group.append(candidate)
                    used_local.append(j)

            if len(group) < min_touches:
                continue

            for idx in used_local:
                used.add(idx)

            prices = [g["price"] for g in group]
            level = float(np.mean(prices))
            upper = max(prices) + band
            lower = min(prices) - band
            pool_type = self._cluster_type(side, len(group))
            age = max(latest_global_index - group[-1]["index"], 0)
            strength = self._score_pool(pool_type, len(group), age, [g["volume"] for g in group], candles)
            pool_id = self._pool_id(candles[0].symbol, candles[0].timeframe, pool_type.value, group[0]["index"])

            pools.append(
                LiquidityPoolData(
                    pool_id=pool_id,
                    symbol=candles[0].symbol,
                    timeframe=candles[0].timeframe,
                    zone_type=side,
                    liquidity_type=pool_type,
                    price_level=level,
                    upper_bound=float(upper),
                    lower_bound=float(lower),
                    touch_count=len(group),
                    formed_at=group[0]["timestamp"],
                    last_touched_at=group[-1]["timestamp"],
                    age_candles=age,
                    strength_score=strength,
                    source_indices=[g["index"] for g in group],
                    metadata={"prices": prices},
                )
            )

        return pools

    def _cluster_type(self, side: str, touches: int) -> LiquidityPoolType:
        if side == "BUY_SIDE":
            return LiquidityPoolType.TRIPLE_TOP if touches >= 3 else LiquidityPoolType.EQUAL_HIGH
        return LiquidityPoolType.TRIPLE_BOTTOM if touches >= 3 else LiquidityPoolType.EQUAL_LOW

    def _session_pools(self, candles: list[CandleData]) -> list[LiquidityPoolData]:
        grouped: dict[date, list[tuple[int, CandleData]]] = defaultdict(list)
        for idx, candle in enumerate(candles):
            grouped[candle.timestamp.date()].append((idx, candle))

        pools = []
        latest_index = len(candles) - 1
        for session_date, rows in grouped.items():
            if len(rows) < 3:
                continue
            high_idx, high_candle = max(rows, key=lambda row: row[1].high)
            low_idx, low_candle = min(rows, key=lambda row: row[1].low)
            pools.append(
                self._single_level_pool(
                    candle=high_candle,
                    source_index=high_idx,
                    latest_index=latest_index,
                    pool_type=LiquidityPoolType.SESSION_HIGH,
                    side="BUY_SIDE",
                    level=high_candle.high,
                    tag=session_date.isoformat(),
                )
            )
            pools.append(
                self._single_level_pool(
                    candle=low_candle,
                    source_index=low_idx,
                    latest_index=latest_index,
                    pool_type=LiquidityPoolType.SESSION_LOW,
                    side="SELL_SIDE",
                    level=low_candle.low,
                    tag=session_date.isoformat(),
                )
            )
        return pools

    def _previous_day_pools(self, candles: list[CandleData]) -> list[LiquidityPoolData]:
        dates = sorted({c.timestamp.date() for c in candles})
        if len(dates) < 2:
            return []

        prev_date = dates[-2]
        rows = [(idx, c) for idx, c in enumerate(candles) if c.timestamp.date() == prev_date]
        if not rows:
            return []

        latest_index = len(candles) - 1
        high_idx, high_candle = max(rows, key=lambda row: row[1].high)
        low_idx, low_candle = min(rows, key=lambda row: row[1].low)
        return [
            self._single_level_pool(
                high_candle,
                high_idx,
                latest_index,
                LiquidityPoolType.PREVIOUS_DAY_HIGH,
                "BUY_SIDE",
                high_candle.high,
                prev_date.isoformat(),
            ),
            self._single_level_pool(
                low_candle,
                low_idx,
                latest_index,
                LiquidityPoolType.PREVIOUS_DAY_LOW,
                "SELL_SIDE",
                low_candle.low,
                prev_date.isoformat(),
            ),
        ]

    def _weekly_pools(self, candles: list[CandleData]) -> list[LiquidityPoolData]:
        weekly: dict[tuple[int, int], list[tuple[int, CandleData]]] = defaultdict(list)
        for idx, candle in enumerate(candles):
            iso = candle.timestamp.isocalendar()
            weekly[(iso.year, iso.week)].append((idx, candle))

        weeks = sorted(weekly)
        if len(weeks) < 2:
            return []

        prev_week = weeks[-2]
        rows = weekly[prev_week]
        latest_index = len(candles) - 1
        high_idx, high_candle = max(rows, key=lambda row: row[1].high)
        low_idx, low_candle = min(rows, key=lambda row: row[1].low)
        tag = f"{prev_week[0]}-W{prev_week[1]:02d}"
        return [
            self._single_level_pool(
                high_candle,
                high_idx,
                latest_index,
                LiquidityPoolType.WEEKLY_HIGH,
                "BUY_SIDE",
                high_candle.high,
                tag,
            ),
            self._single_level_pool(
                low_candle,
                low_idx,
                latest_index,
                LiquidityPoolType.WEEKLY_LOW,
                "SELL_SIDE",
                low_candle.low,
                tag,
            ),
        ]

    def _single_level_pool(
        self,
        candle: CandleData,
        source_index: int,
        latest_index: int,
        pool_type: LiquidityPoolType,
        side: str,
        level: float,
        tag: str,
    ) -> LiquidityPoolData:
        band = self._price_band(level, 0)
        age = max(latest_index - source_index, 0)
        strength = self._score_pool(pool_type, 1, age, [candle.volume], [candle])
        return LiquidityPoolData(
            pool_id=self._pool_id(candle.symbol, candle.timeframe, pool_type.value, source_index),
            symbol=candle.symbol,
            timeframe=candle.timeframe,
            zone_type=side,
            liquidity_type=pool_type,
            price_level=float(level),
            upper_bound=float(level + band),
            lower_bound=float(level - band),
            touch_count=1,
            formed_at=candle.timestamp,
            last_touched_at=candle.timestamp,
            age_candles=age,
            strength_score=strength,
            source_indices=[source_index],
            metadata={"tag": tag},
        )

    def _score_pool(
        self,
        pool_type: LiquidityPoolType,
        touches: int,
        age: int,
        touch_volumes: list[int],
        candles: list[CandleData],
    ) -> float:
        base_by_type = {
            LiquidityPoolType.EQUAL_HIGH: 48,
            LiquidityPoolType.EQUAL_LOW: 48,
            LiquidityPoolType.TRIPLE_TOP: 62,
            LiquidityPoolType.TRIPLE_BOTTOM: 62,
            LiquidityPoolType.SESSION_HIGH: 58,
            LiquidityPoolType.SESSION_LOW: 58,
            LiquidityPoolType.PREVIOUS_DAY_HIGH: 72,
            LiquidityPoolType.PREVIOUS_DAY_LOW: 72,
            LiquidityPoolType.WEEKLY_HIGH: 82,
            LiquidityPoolType.WEEKLY_LOW: 82,
        }
        score = base_by_type[pool_type]
        score += min(max(touches - 1, 0) * 7, 21)
        score += min(age / 50, 1.0) * 8

        avg_volume = np.mean([c.volume for c in candles]) if candles else 0
        if avg_volume > 0 and touch_volumes:
            score += min((np.mean(touch_volumes) / avg_volume) / 1.5, 1.0) * 12
        return float(min(score, 100))

    def _dedupe_pools(self, pools: list[LiquidityPoolData]) -> list[LiquidityPoolData]:
        deduped: list[LiquidityPoolData] = []
        for pool in sorted(pools, key=lambda p: p.strength_score, reverse=True):
            duplicate = False
            for existing in deduped:
                same_side = existing.zone_type == pool.zone_type
                overlaps = not (
                    pool.upper_bound < existing.lower_bound or pool.lower_bound > existing.upper_bound
                )
                same_type = existing.liquidity_type == pool.liquidity_type
                if same_side and overlaps and same_type:
                    duplicate = True
                    break
            if not duplicate:
                deduped.append(pool)
        return sorted(deduped, key=lambda p: p.formed_at)

    def _price_band(self, price: float, atr: float) -> float:
        return max(abs(price) * self._tolerance, atr * 0.05)

    def _pool_id(self, symbol: str, timeframe: str, pool_type: str, index: int) -> str:
        safe_symbol = symbol.replace(":", "_").replace("/", "_")
        return f"{safe_symbol}:{timeframe}:{pool_type}:{index}"

    def _atr(self, candles: list[CandleData], period: int = 14) -> float:
        if not candles:
            return 0
        true_ranges = []
        for i, candle in enumerate(candles):
            if i == 0:
                true_ranges.append(candle.high - candle.low)
            else:
                prev_close = candles[i - 1].close
                true_ranges.append(
                    max(
                        candle.high - candle.low,
                        abs(candle.high - prev_close),
                        abs(candle.low - prev_close),
                    )
                )
        return float(np.mean(true_ranges[-period:]))


class LiquiditySweepEngine:
    """Validates sweeps against existing liquidity pools."""

    def __init__(self, pool_engine: Optional[LiquidityPoolEngine] = None):
        self._tolerance = settings.signal.equal_level_tolerance
        self._min_wick_ratio = settings.signal.sweep_min_wick_ratio
        self._lookback = settings.signal.lookback_periods
        self._pool_engine = pool_engine or LiquidityPoolEngine()
        self._min_displacement_score = 30

    def detect_equal_levels(
        self, candles: list[CandleData], min_touches: int = 3
    ) -> tuple[list[LiquidityZoneData], list[LiquidityZoneData]]:
        pools = self._pool_engine.detect_pools(candles, min_touches=min_touches)
        equal_pools = [
            p
            for p in pools
            if p.liquidity_type
            in {
                LiquidityPoolType.EQUAL_HIGH,
                LiquidityPoolType.EQUAL_LOW,
                LiquidityPoolType.TRIPLE_TOP,
                LiquidityPoolType.TRIPLE_BOTTOM,
            }
        ]

        zones = [self._pool_to_zone(pool) for pool in equal_pools]
        buy_side = [z for z in zones if z.zone_type == "BUY_SIDE"]
        sell_side = [z for z in zones if z.zone_type == "SELL_SIDE"]
        return buy_side, sell_side

    def detect_pools(
        self, candles: list[CandleData], min_touches: int = 2
    ) -> list[LiquidityPoolData]:
        return self._pool_engine.detect_pools(candles, min_touches=min_touches)

    def detect_sweep(
        self,
        candles: list[CandleData],
        liquidity_zones: list[LiquidityZoneData | LiquidityPoolData],
    ) -> list[LiquiditySweepEvent]:
        if len(candles) < 2:
            return []

        latest = candles[-1]
        sweeps = []

        for zone in liquidity_zones:
            pool = self._normalize_pool(zone, latest)
            if pool.formed_at >= latest.timestamp:
                continue

            if pool.zone_type == "BUY_SIDE":
                breached = latest.high > pool.upper_bound
                rejected = latest.close < pool.price_level
                direction = Direction.SHORT
                sweep_price = latest.high
            else:
                breached = latest.low < pool.lower_bound
                rejected = latest.close > pool.price_level
                direction = Direction.LONG
                sweep_price = latest.low

            if not breached or not rejected:
                continue

            sweep = self._validate_sweep(candles, latest, pool, direction, sweep_price)
            if sweep:
                sweeps.append(sweep)

        sweeps.sort(key=lambda s: s.sweep_strength, reverse=True)
        return sweeps

    def _validate_sweep(
        self,
        candles: list[CandleData],
        candle: CandleData,
        pool: LiquidityPoolData,
        direction: Direction,
        sweep_price: float,
    ) -> Optional[LiquiditySweepEvent]:
        candle_range = candle.high - candle.low
        if candle_range <= 0:
            return None

        if pool.zone_type == "BUY_SIDE":
            wick = candle.high - max(candle.open, candle.close)
            rejection_depth = pool.price_level - candle.close
        else:
            wick = min(candle.open, candle.close) - candle.low
            rejection_depth = candle.close - pool.price_level

        wick_ratio = wick / candle_range
        if wick_ratio < self._min_wick_ratio * 0.5:
            return None

        displacement = self._reversal_displacement_score(candles, direction, pool.price_level)
        if displacement < self._min_displacement_score:
            return None

        breach_depth = abs(sweep_price - pool.price_level)
        breach_depth_pct = (breach_depth / pool.price_level) * 100 if pool.price_level else 0
        rejection_score = min(wick_ratio / self._min_wick_ratio, 1.0) * 70
        if rejection_depth > 0:
            rejection_score += min(rejection_depth / max(breach_depth, 0.01), 1.0) * 30
        rejection_score = min(rejection_score, 100)
        volume_score = self._volume_score(candles)

        strength = self._calculate_sweep_strength(
            pool_strength=pool.strength_score,
            rejection_score=rejection_score,
            displacement_score=displacement,
            breach_depth_percent=breach_depth_pct,
            volume_score=volume_score,
        )

        reasons = [
            f"{pool.liquidity_type.value.replace('_', ' ').title()} existed first",
            "Price breached pool",
            "Close rejected back through pool",
            f"Displacement score {displacement:.0f}",
        ]

        return LiquiditySweepEvent(
            symbol=candle.symbol,
            timeframe=candle.timeframe,
            zone_type=pool.zone_type,
            price_level=pool.price_level,
            sweep_price=sweep_price,
            sweep_time=candle.timestamp,
            sweep_strength=strength,
            candle_close=candle.close,
            rejection_confirmed=True,
            pool_id=pool.pool_id,
            liquidity_type=pool.liquidity_type.value,
            pool_strength_score=pool.strength_score,
            pool_age_candles=pool.age_candles,
            displacement_score=displacement,
            rejection_score=rejection_score,
            breach_depth_percent=breach_depth_pct,
            volume_score=volume_score,
            reasons=reasons,
        )

    def _calculate_sweep_strength(
        self,
        pool_strength: float,
        rejection_score: float,
        displacement_score: float,
        breach_depth_percent: float,
        volume_score: float,
    ) -> float:
        breach_score = min(breach_depth_percent / 0.35, 1.0) * 100
        score = (
            pool_strength * 0.25
            + rejection_score * 0.25
            + displacement_score * 0.30
            + breach_score * 0.10
            + volume_score * 0.10
        )
        return float(min(score, 100))

    def _reversal_displacement_score(
        self, candles: list[CandleData], direction: Direction, level: float
    ) -> float:
        candle = candles[-1]
        candle_range = candle.high - candle.low
        if candle_range <= 0:
            return 0

        body = abs(candle.close - candle.open)
        body_ratio = body / candle_range
        atr = self._atr(candles[-20:])
        range_ratio = candle_range / atr if atr > 0 else 1

        if direction == Direction.LONG:
            rejection_close = max((candle.close - level) / candle_range, 0)
            directional_close = (candle.close - candle.low) / candle_range
        else:
            rejection_close = max((level - candle.close) / candle_range, 0)
            directional_close = (candle.high - candle.close) / candle_range

        return float(
            min(
                body_ratio * 25
                + min(range_ratio / 1.15, 1.0) * 25
                + min(directional_close, 1.0) * 30
                + min(rejection_close, 1.0) * 20,
                100,
            )
        )

    def _volume_score(self, candles: list[CandleData]) -> float:
        if len(candles) < 5:
            return 50
        latest = candles[-1]
        avg_volume = np.mean([c.volume for c in candles[-20:-1]]) if len(candles) > 20 else np.mean(
            [c.volume for c in candles[:-1]]
        )
        if avg_volume <= 0:
            return 50
        return float(min((latest.volume / avg_volume) / 2.0, 1.0) * 100)

    def _normalize_pool(
        self, zone: LiquidityZoneData | LiquidityPoolData, latest: CandleData
    ) -> LiquidityPoolData:
        if isinstance(zone, LiquidityPoolData):
            return zone

        band = max(zone.price_level * self._tolerance, 0.01)
        liquidity_type = self._zone_liquidity_type(zone)
        return LiquidityPoolData(
            pool_id=zone.pool_id or f"{zone.symbol}:{zone.timeframe}:{zone.zone_type}:{zone.formed_at.isoformat()}",
            symbol=zone.symbol,
            timeframe=zone.timeframe,
            zone_type=zone.zone_type,
            liquidity_type=liquidity_type,
            price_level=zone.price_level,
            upper_bound=zone.upper_bound if zone.upper_bound is not None else zone.price_level + band,
            lower_bound=zone.lower_bound if zone.lower_bound is not None else zone.price_level - band,
            touch_count=zone.touch_count,
            formed_at=zone.formed_at,
            last_touched_at=zone.formed_at,
            age_candles=zone.age_candles,
            strength_score=zone.strength_score or min(45 + zone.touch_count * 8, 85),
            swept=zone.swept,
            source_indices=[],
            metadata={"normalized_from": "LiquidityZoneData", "latest": latest.timestamp.isoformat()},
        )

    def _zone_liquidity_type(self, zone: LiquidityZoneData) -> LiquidityPoolType:
        if zone.liquidity_type:
            try:
                return LiquidityPoolType(zone.liquidity_type)
            except ValueError:
                pass
        if zone.zone_type == "BUY_SIDE":
            return LiquidityPoolType.TRIPLE_TOP if zone.touch_count >= 3 else LiquidityPoolType.EQUAL_HIGH
        return LiquidityPoolType.TRIPLE_BOTTOM if zone.touch_count >= 3 else LiquidityPoolType.EQUAL_LOW

    def _pool_to_zone(self, pool: LiquidityPoolData) -> LiquidityZoneData:
        return LiquidityZoneData(
            symbol=pool.symbol,
            timeframe=pool.timeframe,
            zone_type=pool.zone_type,
            price_level=pool.price_level,
            touch_count=pool.touch_count,
            formed_at=pool.formed_at,
            pool_id=pool.pool_id,
            liquidity_type=pool.liquidity_type.value,
            upper_bound=pool.upper_bound,
            lower_bound=pool.lower_bound,
            strength_score=pool.strength_score,
            age_candles=pool.age_candles,
            swept=pool.swept,
        )

    def detect_stop_hunt(
        self,
        candles: list[CandleData],
        support_level: float,
        resistance_level: float,
    ) -> Optional[LiquiditySweepEvent]:
        if len(candles) < 3:
            return None

        latest = candles[-1]
        zones = [
            LiquidityZoneData(
                symbol=latest.symbol,
                timeframe=latest.timeframe,
                zone_type="BUY_SIDE",
                price_level=resistance_level,
                touch_count=2,
                formed_at=candles[-3].timestamp,
            ),
            LiquidityZoneData(
                symbol=latest.symbol,
                timeframe=latest.timeframe,
                zone_type="SELL_SIDE",
                price_level=support_level,
                touch_count=2,
                formed_at=candles[-3].timestamp,
            ),
        ]
        sweeps = self.detect_sweep(candles, zones)
        return sweeps[0] if sweeps else None

    def _atr(self, candles: list[CandleData], period: int = 14) -> float:
        if not candles:
            return 0
        true_ranges = []
        for i, candle in enumerate(candles):
            if i == 0:
                true_ranges.append(candle.high - candle.low)
            else:
                prev_close = candles[i - 1].close
                true_ranges.append(
                    max(
                        candle.high - candle.low,
                        abs(candle.high - prev_close),
                        abs(candle.low - prev_close),
                    )
                )
        return float(np.mean(true_ranges[-period:]))


class LiquiditySweepDetector(LiquiditySweepEngine):
    """Backward-compatible name used by the existing orchestrator and tests."""
