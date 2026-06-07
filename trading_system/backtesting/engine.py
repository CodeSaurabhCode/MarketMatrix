"""
Backtesting Engine

Replays historical data through the signal detection system
to measure performance metrics.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from trading_system.config.settings import settings
from trading_system.core.models import Signal, Candle
from trading_system.core.schemas import CandleData, SignalContext, Direction
from trading_system.core.database import SyncSessionLocal
from trading_system.engines.liquidity_sweep import LiquiditySweepDetector
from trading_system.engines.anchored_vwap import AnchoredVWAPEngine
from trading_system.engines.fair_value_gap import FairValueGapDetector
from trading_system.engines.volume_profile import VolumeProfileEngine
from trading_system.engines.order_flow import OrderFlowProxyEngine
from trading_system.engines.market_structure import MarketStructureEngine
from trading_system.engines.signal_aggregator import SignalAggregator

logger = logging.getLogger(__name__)


class BacktestResult:
    """Holds backtesting results."""
    
    def __init__(self):
        self.total_signals = 0
        self.wins = 0
        self.losses = 0
        self.breakevens = 0
        self.total_rr = 0.0
        self.max_drawdown = 0.0
        self.win_streak = 0
        self.loss_streak = 0
        self.signals: list[dict] = []
    
    @property
    def win_rate(self) -> float:
        total = self.wins + self.losses
        return (self.wins / total * 100) if total > 0 else 0
    
    @property
    def avg_rr(self) -> float:
        return (self.total_rr / self.wins) if self.wins > 0 else 0
    
    @property
    def expectancy(self) -> float:
        """Calculate system expectancy."""
        if self.wins + self.losses == 0:
            return 0
        win_rate = self.win_rate / 100
        avg_win = self.avg_rr
        avg_loss = 1.0  # Always risking 1R
        return (win_rate * avg_win) - ((1 - win_rate) * avg_loss)
    
    def summary(self) -> dict:
        return {
            "total_signals": self.total_signals,
            "wins": self.wins,
            "losses": self.losses,
            "breakevens": self.breakevens,
            "win_rate": round(self.win_rate, 1),
            "avg_rr": round(self.avg_rr, 2),
            "expectancy": round(self.expectancy, 3),
            "max_drawdown": round(self.max_drawdown, 2),
        }


class BacktestEngine:
    """Replays historical data for signal evaluation."""
    
    def __init__(self):
        self._liquidity = LiquiditySweepDetector()
        self._vwap = AnchoredVWAPEngine()
        self._fvg = FairValueGapDetector()
        self._volume_profile = VolumeProfileEngine()
        self._order_flow = OrderFlowProxyEngine()
        self._market_structure = MarketStructureEngine()
        
        self._aggregator = SignalAggregator(
            liquidity_detector=self._liquidity,
            vwap_engine=self._vwap,
            fvg_detector=self._fvg,
            volume_profile_engine=self._volume_profile,
            order_flow_engine=self._order_flow,
            market_structure_engine=self._market_structure,
        )
    
    def run_backtest(
        self,
        symbol: str,
        candles: list[CandleData],
        timeframe: str = "15m",
        warmup_periods: int = 50,
    ) -> BacktestResult:
        """
        Run backtest on historical candle data.
        
        Args:
            symbol: Trading symbol
            candles: Historical OHLCV candles (sorted by time ascending)
            timeframe: Timeframe for analysis
            warmup_periods: Number of candles for indicator warmup
        """
        result = BacktestResult()
        
        if len(candles) < warmup_periods + 10:
            logger.warning(f"Insufficient data for backtest: {len(candles)} candles")
            return result
        
        active_signals: list[dict] = []
        
        # Replay candles
        for i in range(warmup_periods, len(candles)):
            current_candles = candles[:i + 1]
            latest = current_candles[-1]
            
            # Check active signals for exit
            still_active = []
            for sig in active_signals:
                outcome = self._check_signal_exit(sig, latest)
                if outcome:
                    result.total_signals += 1
                    if outcome["result"] == "WIN":
                        result.wins += 1
                        result.total_rr += outcome["rr"]
                    elif outcome["result"] == "LOSS":
                        result.losses += 1
                    else:
                        result.breakevens += 1
                    
                    result.signals.append({
                        **sig,
                        "outcome": outcome["result"],
                        "rr": outcome.get("rr", 0),
                        "exit_price": outcome["exit_price"],
                        "exit_time": latest.timestamp,
                    })
                else:
                    still_active.append(sig)
            
            active_signals = still_active
            
            # Generate new signals (skip if we have active signal for this symbol)
            if not any(s["symbol"] == symbol for s in active_signals):
                signal = self._evaluate_candles(current_candles, symbol, timeframe)
                if signal:
                    active_signals.append({
                        "symbol": signal.symbol,
                        "direction": signal.direction.value,
                        "entry_price": (signal.entry_zone_low + signal.entry_zone_high) / 2,
                        "stop_loss": signal.stop_loss,
                        "target_1": signal.target_1,
                        "target_2": signal.target_2,
                        "confidence": signal.confidence_score,
                        "entry_time": latest.timestamp,
                    })
        
        # Calculate max drawdown
        result.max_drawdown = self._calculate_max_drawdown(result.signals)
        
        return result
    
    def _evaluate_candles(
        self, candles: list[CandleData], symbol: str, timeframe: str
    ) -> Optional[object]:
        """Run signal evaluation on current candle state."""
        latest = candles[-1]
        
        # Run all engines
        # Market structure
        self._market_structure.analyze(candles, symbol, timeframe)
        ms = self._market_structure.get_latest_structure(symbol, timeframe)
        
        # Liquidity sweep
        buy_zones, sell_zones = self._liquidity.detect_equal_levels(candles)
        all_zones = buy_zones + sell_zones
        sweeps = self._liquidity.detect_sweep(candles, all_zones)
        sweep = sweeps[0] if sweeps else None
        
        # FVG
        self._fvg.detect_fvg(candles)
        nearest_fvg = self._fvg.get_nearest_fvg(symbol, latest.close)
        
        # Volume profile
        profile = self._volume_profile.calculate_profile(candles[-100:], symbol, timeframe)
        
        # Order flow
        of_snapshot = self._order_flow.calculate_snapshot(symbol, candles)
        
        # VWAP (simplified for backtest - use prev day data)
        prev_day_candles = self._get_prev_day_candles(candles, latest)
        vwaps = self._vwap.calculate_all_anchors(candles[-50:], prev_day_candles, symbol)
        vwap_signals = self._vwap.detect_vwap_signals(candles[-10:], vwaps)
        
        # ATR
        atr = self._aggregator.calculate_atr(candles)
        
        # Build context
        context = SignalContext(
            symbol=symbol,
            timeframe=timeframe,
            current_price=latest.close,
            timestamp=latest.timestamp,
            liquidity_sweep=sweep,
            nearest_fvg=nearest_fvg,
            vwap_signals=vwap_signals,
            volume_profile=profile,
            order_flow=of_snapshot,
            market_structure=ms,
            atr=atr,
        )
        
        return self._aggregator.evaluate(context)
    
    def _check_signal_exit(self, signal: dict, candle: CandleData) -> Optional[dict]:
        """Check if signal hit SL or target."""
        if signal["direction"] == "LONG":
            # Check stop loss
            if candle.low <= signal["stop_loss"]:
                return {
                    "result": "LOSS",
                    "exit_price": signal["stop_loss"],
                    "rr": -1.0,
                }
            # Check target 1
            if candle.high >= signal["target_1"]:
                entry = signal["entry_price"]
                sl = signal["stop_loss"]
                risk = entry - sl
                reward = signal["target_1"] - entry
                rr = reward / risk if risk > 0 else 0
                return {
                    "result": "WIN",
                    "exit_price": signal["target_1"],
                    "rr": rr,
                }
        
        else:  # SHORT
            # Check stop loss
            if candle.high >= signal["stop_loss"]:
                return {
                    "result": "LOSS",
                    "exit_price": signal["stop_loss"],
                    "rr": -1.0,
                }
            # Check target 1
            if candle.low <= signal["target_1"]:
                entry = signal["entry_price"]
                sl = signal["stop_loss"]
                risk = sl - entry
                reward = entry - signal["target_1"]
                rr = reward / risk if risk > 0 else 0
                return {
                    "result": "WIN",
                    "exit_price": signal["target_1"],
                    "rr": rr,
                }
        
        return None
    
    def _get_prev_day_candles(
        self, candles: list[CandleData], current: CandleData
    ) -> list[CandleData]:
        """Get previous trading day candles."""
        current_date = current.timestamp.date()
        prev_date = current_date - timedelta(days=1)
        
        # Skip weekends
        while prev_date.weekday() >= 5:
            prev_date -= timedelta(days=1)
        
        return [c for c in candles if c.timestamp.date() == prev_date]
    
    def _calculate_max_drawdown(self, signals: list[dict]) -> float:
        """Calculate maximum drawdown in R multiples."""
        if not signals:
            return 0
        
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        
        for sig in signals:
            rr = sig.get("rr", 0)
            if sig.get("outcome") == "LOSS":
                equity -= 1.0
            elif sig.get("outcome") == "WIN":
                equity += rr
            
            peak = max(peak, equity)
            dd = peak - equity
            max_dd = max(max_dd, dd)
        
        return max_dd
    
    def run_multi_symbol_backtest(
        self,
        symbols: list[str],
        candles_by_symbol: dict[str, list[CandleData]],
        timeframe: str = "15m",
    ) -> dict[str, BacktestResult]:
        """Run backtest across multiple symbols."""
        results = {}
        
        for symbol in symbols:
            if symbol in candles_by_symbol:
                logger.info(f"Backtesting {symbol}...")
                results[symbol] = self.run_backtest(
                    symbol, candles_by_symbol[symbol], timeframe
                )
        
        return results
    
    def generate_report(self, results: dict[str, BacktestResult]) -> str:
        """Generate a text report from backtest results."""
        lines = ["=" * 60, "BACKTEST REPORT", "=" * 60, ""]
        
        total_signals = 0
        total_wins = 0
        total_losses = 0
        
        for symbol, result in results.items():
            lines.append(f"\n{'─' * 40}")
            lines.append(f"Symbol: {symbol}")
            lines.append(f"{'─' * 40}")
            summary = result.summary()
            for k, v in summary.items():
                lines.append(f"  {k}: {v}")
            
            total_signals += result.total_signals
            total_wins += result.wins
            total_losses += result.losses
        
        lines.append(f"\n{'═' * 60}")
        lines.append("AGGREGATE RESULTS")
        lines.append(f"{'═' * 60}")
        lines.append(f"  Total Signals: {total_signals}")
        lines.append(f"  Total Wins: {total_wins}")
        lines.append(f"  Total Losses: {total_losses}")
        
        if total_wins + total_losses > 0:
            wr = total_wins / (total_wins + total_losses) * 100
            lines.append(f"  Overall Win Rate: {wr:.1f}%")
        
        return "\n".join(lines)
