"""
Main Orchestrator

Coordinates all components:
- WebSocket tick ingestion
- Candle aggregation
- Engine execution on candle close
- Signal generation
- Notification dispatch
"""
import asyncio
import logging
import signal
import sys
from datetime import datetime, time
from typing import Optional

from trading_system.config.settings import settings
from trading_system.core.database import init_db, get_session, AsyncSessionLocal
from trading_system.core.cache import cache
from trading_system.core.models import Candle, Signal as SignalModel
from trading_system.core.schemas import (
    TickData, CandleData, SignalContext, SignalCreate
)
from trading_system.data.smartapi_client import AngelOneClient
from trading_system.data.symbol_manager import symbol_manager
from trading_system.data.candle_aggregator import CandleAggregator
from trading_system.engines.liquidity_sweep import LiquiditySweepDetector
from trading_system.engines.anchored_vwap import AnchoredVWAPEngine
from trading_system.engines.fair_value_gap import FairValueGapDetector
from trading_system.engines.volume_profile import VolumeProfileEngine
from trading_system.engines.order_flow import OrderFlowProxyEngine
from trading_system.engines.market_structure import MarketStructureEngine
from trading_system.engines.signal_aggregator import SignalAggregator
from trading_system.notifications.email import EmailNotifier

logger = logging.getLogger(__name__)


class TradingOrchestrator:
    """Main system orchestrator."""
    
    def __init__(self):
        # Data components
        self._client = AngelOneClient()
        self._aggregator = CandleAggregator(timeframes=settings.app.timeframes)
        self._notifier = EmailNotifier()
        
        # Detection engines
        self._liquidity = LiquiditySweepDetector()
        self._vwap = AnchoredVWAPEngine()
        self._fvg = FairValueGapDetector()
        self._volume_profile = VolumeProfileEngine()
        self._order_flow = OrderFlowProxyEngine()
        self._market_structure = MarketStructureEngine()
        
        # Signal aggregator
        self._signal_agg = SignalAggregator(
            liquidity_detector=self._liquidity,
            vwap_engine=self._vwap,
            fvg_detector=self._fvg,
            volume_profile_engine=self._volume_profile,
            order_flow_engine=self._order_flow,
            market_structure_engine=self._market_structure,
        )
        
        # State
        self._candle_buffer: dict[str, dict[str, list[CandleData]]] = {}  # {symbol: {timeframe: [candles]}}
        self._running = False
        self._buffer_size = 200  # Keep last N candles per symbol/timeframe
    
    async def start(self):
        """Start the trading system."""
        logger.info("Starting Trading Signal Detection System...")
        
        # Initialize infrastructure
        await init_db()
        await cache.connect()
        await self._notifier.connect()
        
        # Authenticate with Angel One
        authenticated = await self._client.authenticate()
        if not authenticated:
            logger.error("Failed to authenticate with Angel One. Exiting.")
            return
        
        # Load scrip master and focused config
        symbol_manager.load_scrip_master()
        symbol_manager.load_focused_config()
        
        # Validate focused symbols are loaded
        focused_symbols = symbol_manager.get_focused_symbols()
        if not focused_symbols:
            logger.error("=" * 80)
            logger.error("ERROR: No focused symbols configured!")
            logger.error("=" * 80)
            logger.error("")
            logger.error("The system requires trading_config.json to specify which")
            logger.error("symbols to track. This prevents tracking all 164,905+ symbols.")
            logger.error("")
            logger.error("File: d:\\Saurabh\\Gameplan\\trading_config.json")
            logger.error("")
            logger.error("Example:")
            logger.error('{')
            logger.error('  "symbols_to_track": [')
            logger.error('    {"symbol": "Nifty 50", "name": "NSE Nifty 50", "type": "INDEX"},')
            logger.error('    {"symbol": "SBIN-EQ", "name": "State Bank", "type": "EQUITY"}')
            logger.error('  ]')
            logger.error('}')
            logger.error("")
            logger.error("To find symbols, run: python TRADING_CONFIG_GUIDE.py")
            logger.error("=" * 80)
            return
        
        logger.info(f"[SYMBOLS OK] Loaded {len(focused_symbols)} focused symbols:")
        for sym in focused_symbols:
            logger.info(f"  - {sym['symbol']} ({sym['type']})")
        
        # Backfill historical data
        await self._backfill_data()
        
        # Set up candle callback
        self._aggregator.set_candle_callback(self._on_candle_complete)
        
        # Start WebSocket
        self._running = True
        try:
            tokens = symbol_manager.get_websocket_tokens()
        except RuntimeError as e:
            logger.error(f"Failed to get WebSocket tokens: {e}")
            return
        
        # Run WebSocket in separate thread
        ws_task = asyncio.create_task(self._run_websocket(tokens))
        
        # Send startup notification
        await self._notifier.send_system_alert(
            f"System started. Monitoring {len(tokens)} symbols."
        )
        
        logger.info(f"System running. Monitoring {len(tokens)} symbols.")
        
        # Keep alive
        try:
            while self._running:
                await asyncio.sleep(1)
                
                # Check if market is closed
                if self._is_market_closed():
                    logger.info("Market closed. Running end-of-day tasks...")
                    await self._end_of_day()
                    break
                    
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()
    
    async def stop(self):
        """Gracefully stop the system."""
        self._running = False
        self._client.stop_websocket()
        await self._notifier.disconnect()
        await cache.disconnect()
        logger.info("System stopped.")
    
    async def _run_websocket(self, tokens: list[dict]):
        """Run WebSocket in background."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            self._client.start_websocket,
            tokens,
            self._on_tick,
            loop,
        )
    
    async def _on_tick(self, tick: TickData):
        """Handle incoming tick."""
        # Note: Tick logging suppressed to reduce verbosity (1000s per second)
        # Uncomment to debug: logger.debug(f"[TICK] {tick.symbol} @ ₹{tick.ltp}")
        
        # Update cache
        await cache.set_ltp(tick.symbol, tick.ltp)
        
        # Process tick through order flow engine
        self._order_flow.process_tick(tick)
        
        # Aggregate into candles
        await self._aggregator.process_tick(tick)
    
    async def _on_candle_complete(self, candle: CandleData):
        """Handle completed candle - trigger analysis."""
        symbol = candle.symbol
        timeframe = candle.timeframe
        logger.info(f"[CANDLE] {symbol} {timeframe}m: O={candle.open:.2f} H={candle.high:.2f} L={candle.low:.2f} C={candle.close:.2f} V={candle.volume}")
        
        # Add to buffer
        if symbol not in self._candle_buffer:
            self._candle_buffer[symbol] = {}
        if timeframe not in self._candle_buffer[symbol]:
            self._candle_buffer[symbol][timeframe] = []
        
        self._candle_buffer[symbol][timeframe].append(candle)
        
        # Trim buffer
        if len(self._candle_buffer[symbol][timeframe]) > self._buffer_size:
            self._candle_buffer[symbol][timeframe] = \
                self._candle_buffer[symbol][timeframe][-self._buffer_size:]
        
        # Store candle in database
        await self._store_candle(candle)
        
        # Run analysis
        await self._analyze(symbol, timeframe)
    
    async def _analyze(self, symbol: str, timeframe: str):
        """Run full analysis pipeline on latest candle data."""
        candles = self._candle_buffer.get(symbol, {}).get(timeframe, [])
        
        if len(candles) < settings.signal.lookback_periods:
            logger.debug(f"[ANALYSIS SKIP] {symbol} {timeframe}m: Not enough candles ({len(candles)}/{settings.signal.lookback_periods})")
            return
        
        logger.info(f"[ANALYSIS START] {symbol} {timeframe}m (candles={len(candles)})")  
        latest = candles[-1]
        
        try:
            # 1. Market Structure
            self._market_structure.analyze(candles, symbol, timeframe)
            ms = self._market_structure.get_latest_structure(symbol, timeframe)
            logger.debug(f"  [MS] {ms.structure if ms else 'N/A'}")
            
            # 2. Liquidity Pool and Sweep Detection
            liquidity_pools = self._liquidity.detect_pools(candles, min_touches=2)
            sweeps = self._liquidity.detect_sweep(candles, liquidity_pools)
            sweep = sweeps[0] if sweeps else None
            logger.debug(f"  [LIQUIDITY] Pools={len(liquidity_pools)}, Sweeps={len(sweeps)}, Latest={sweep.direction if sweep else 'None'}")
            
            # 3. FVG Detection
            new_fvgs = self._fvg.detect_fvg(
                candles,
                market_structure=ms,
                liquidity_context=sweep,
            )
            self._fvg.update_fvgs(candles)
            nearest_fvg = self._fvg.get_nearest_fvg(symbol, latest.close)
            logger.debug(f"  [FVG] New={len(new_fvgs)}, Nearest={nearest_fvg.gap_size if nearest_fvg else 'None'}")
            
            # 4. Volume Profile
            profile = self._volume_profile.calculate_profile(candles[-100:], symbol, timeframe)
            logger.debug(f"  [VOLUME] POC={profile.poc if profile else 'N/A'}")
            
            # 5. Anchored VWAP
            prev_day_candles = self._get_prev_day_candles(symbol, timeframe)
            vwaps = self._vwap.calculate_all_anchors(candles[-50:], prev_day_candles, symbol)
            vwap_signals = self._vwap.detect_vwap_signals(candles[-10:], vwaps)
            logger.debug(f"  [VWAP] Signals={len(vwap_signals)}")
            
            # 6. Order Flow
            of_snapshot = self._order_flow.calculate_snapshot(symbol, candles)
            logger.debug(f"  [ORDER_FLOW] Delta={of_snapshot.cumulative_delta if of_snapshot else 'N/A'}")
            
            # 7. ATR
            atr = self._signal_agg.calculate_atr(candles)
            logger.debug(f"  [ATR] {atr:.2f}")
            
            # Build signal context
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
            
            # Evaluate signal
            signal = self._signal_agg.evaluate(context)
            logger.debug(f"[SIGNAL EVAL] Generated={signal is not None}")
            
            if signal:
                await self._process_signal(signal)
                
        except Exception as e:
            logger.error(f"Analysis error for {symbol} ({timeframe}): {e}", exc_info=True)
    
    async def _process_signal(self, signal: SignalCreate):
        """Process a generated signal - store and notify."""
        # Check deduplication
        is_duplicate = await cache.is_signal_sent(
            signal.symbol, signal.direction.value, signal.timeframe or ""
        )
        
        if is_duplicate:
            logger.debug(f"Duplicate signal suppressed: {signal.symbol} {signal.direction.value}")
            return
        
        # Store in database
        await self._store_signal(signal)
        
        # Send notification
        if signal.confidence_score >= settings.signal.min_confidence_score:
            success = await self._notifier.send_signal(signal)
            if success:
                await cache.mark_signal_sent(
                    signal.symbol, signal.direction.value, signal.timeframe or ""
                )
        
        logger.info(
            f"Signal: {signal.symbol} {signal.direction.value} "
            f"Score={signal.confidence_score:.0f} "
            f"Entry={signal.entry_zone_low:.2f}-{signal.entry_zone_high:.2f}"
        )
    
    async def _store_candle(self, candle: CandleData):
        """Store candle in database."""
        async with get_session() as session:
            db_candle = Candle(
                symbol=candle.symbol,
                token=candle.token,
                timeframe=candle.timeframe,
                timestamp=candle.timestamp,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
            )
            session.add(db_candle)
    
    async def _store_signal(self, signal: SignalCreate):
        """Store signal in database."""
        async with get_session() as session:
            db_signal = SignalModel(
                symbol=signal.symbol,
                direction=signal.direction.value,
                entry_zone_low=signal.entry_zone_low,
                entry_zone_high=signal.entry_zone_high,
                stop_loss=signal.stop_loss,
                target_1=signal.target_1,
                target_2=signal.target_2,
                confidence_score=signal.confidence_score,
                market_structure_score=signal.market_structure_score,
                liquidity_sweep_score=signal.liquidity_sweep_score,
                fvg_score=signal.fvg_score,
                vwap_score=signal.vwap_score,
                volume_profile_score=signal.volume_profile_score,
                order_flow_score=signal.order_flow_score,
                volume_confirmation_score=signal.volume_confirmation_score,
                reasoning=signal.reasoning,
                explainability=signal.explainability,
                timeframe=signal.timeframe,
                outcome="ACTIVE",
            )
            session.add(db_signal)
    
    async def _backfill_data(self):
        """Backfill historical data for focused symbols only."""
        logger.info("Starting data backfill for focused symbols...")
        
        symbols = symbol_manager.get_focused_symbols()
        if not symbols:
            logger.warning("No focused symbols to backfill")
            return
        
        for sym_info in symbols:
            symbol = sym_info["symbol"]
            token = sym_info["token"]
            exchange = sym_info.get("exchange", "NSE")  # Focused symbols use "exchange"
            
            for tf in ["15m", "5m"]:  # Backfill higher timeframes
                candles = await self._client.backfill_symbol(
                    symbol=symbol,
                    token=token,
                    exchange=exchange,
                    timeframe=tf,
                    days=settings.app.backfill_days,
                )
                
                if candles:
                    if symbol not in self._candle_buffer:
                        self._candle_buffer[symbol] = {}
                    self._candle_buffer[symbol][tf] = candles[-self._buffer_size:]
                    
                    logger.info(f"Backfilled {len(candles)} {tf} candles for {symbol}")
        
        logger.info("Backfill complete.")
    
    def _get_prev_day_candles(self, symbol: str, timeframe: str) -> list[CandleData]:
        """Get previous day candles from buffer."""
        candles = self._candle_buffer.get(symbol, {}).get(timeframe, [])
        if not candles:
            return []
        
        today = datetime.now().date()
        return [c for c in candles if c.timestamp.date() < today][-50:]
    
    def _is_market_closed(self) -> bool:
        """Check if market is closed."""
        now = datetime.now().time()
        market_close = time(settings.app.market_close_hour, settings.app.market_close_minute)
        return now > market_close
    
    async def _end_of_day(self):
        """End of day tasks."""
        await self._notifier.send_system_alert("Market closed. End-of-day processing started.")
        
        # Reset order flow cumulative delta
        for sym in self._candle_buffer:
            self._order_flow.reset_session(sym)
        
        # Reset candle aggregator
        self._aggregator.reset()


def setup_logging():
    """Configure logging."""
    log_dir = settings.app.log_dir
    import os
    os.makedirs(log_dir, exist_ok=True)
    
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = os.path.join(log_dir, today, "trading_system.log")
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    
    logging.basicConfig(
        level=getattr(logging, settings.app.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file),
        ],
    )


async def main():
    """Entry point."""
    setup_logging()
    
    orchestrator = TradingOrchestrator()
    
    # Handle graceful shutdown
    loop = asyncio.get_event_loop()
    
    def handle_shutdown(sig, frame):
        logger.info(f"Received signal {sig}. Shutting down...")
        loop.create_task(orchestrator.stop())
    
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)
    
    await orchestrator.start()


if __name__ == "__main__":
    asyncio.run(main())
