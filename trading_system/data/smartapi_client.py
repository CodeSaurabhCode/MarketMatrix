"""
Angel One SmartAPI WebSocket and REST client.
Handles authentication, real-time tick streaming, and historical data.
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Callable, Awaitable

import pyotp
from SmartApi import SmartConnect
from SmartApi.smartWebSocketV2 import SmartWebSocketV2

from trading_system.config.settings import settings
from trading_system.core.schemas import TickData, CandleData

logger = logging.getLogger(__name__)


class   AngelOneClient:
    """Manages Angel One SmartAPI connection and data retrieval."""
    
    def __init__(self):
        self._smart_api: Optional[SmartConnect] = None
        self._websocket: Optional[SmartWebSocketV2] = None
        self._auth_token: Optional[str] = None
        self._feed_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._tick_callback: Optional[Callable[[TickData], Awaitable[None]]] = None
        self._connected = False
    
    async def authenticate(self) -> bool:
        """Authenticate with Angel One SmartAPI with retry logic for rate limiting."""
        max_retries = 3
        retry_delay = 5  # seconds
        
        for attempt in range(max_retries):
            try:
                self._smart_api = SmartConnect(api_key=settings.angel.api_key)
                
                totp = pyotp.TOTP(settings.angel.totp_secret).now()
                
                data = self._smart_api.generateSession(
                    settings.angel.client_code,
                    settings.angel.password,
                    totp,
                )
                
                if data.get("status"):
                    self._auth_token = data["data"]["jwtToken"]
                    self._refresh_token = data["data"]["refreshToken"]
                    self._feed_token = self._smart_api.getfeedToken()
                    logger.info("✓ Angel One authentication successful")
                    return True
                else:
                    msg = data.get('message', 'Unknown error')
                    if 'rate' in msg.lower() or 'access' in msg.lower():
                        logger.warning(f"Rate limited: {msg}. Retrying in {retry_delay}s...")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(retry_delay)
                            retry_delay *= 2
                            continue
                    logger.error(f"Authentication failed: {msg}")
                    return False
                    
            except Exception as e:
                error_msg = str(e)
                if 'rate' in error_msg.lower() or 'access denied' in error_msg.lower():
                    logger.warning(f"Rate limit/Access error: {e}. Retrying in {retry_delay}s...")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 2
                        continue
                logger.error(f"Authentication error: {e}")
                return False
        
        return False
    
    async def refresh_session(self):
        """Refresh expired token."""
        try:
            if self._refresh_token:
                data = self._smart_api.generateToken(self._refresh_token)
                if data.get("status"):
                    self._auth_token = data["data"]["jwtToken"]
                    logger.info("Token refreshed successfully")
        except Exception as e:
            logger.error(f"Token refresh failed: {e}")
            await self.authenticate()
    
    def get_historical_candles(
        self,
        symbol: str,
        token: str,
        exchange: str,
        interval: str,
        from_date: str,
        to_date: str,
    ) -> list[CandleData]:
        """
        Fetch historical candle data.
        
        Args:
            interval: ONE_MINUTE, FIVE_MINUTE, FIFTEEN_MINUTE, ONE_HOUR, ONE_DAY
        """
        interval_map = {
            "1m": "ONE_MINUTE",
            "5m": "FIVE_MINUTE",
            "15m": "FIFTEEN_MINUTE",
            "1h": "ONE_HOUR",
            "1d": "ONE_DAY",
        }
        
        api_interval = interval_map.get(interval, interval)
        
        params = {
            "exchange": exchange,
            "symboltoken": token,
            "interval": api_interval,
            "fromdate": from_date,
            "todate": to_date,
        }
        
        try:
            response = self._smart_api.getCandleData(params)
            
            if not response or not response.get("status"):
                logger.error(f"Failed to fetch candles for {symbol}: {response}")
                return []
            
            candles = []
            for row in response.get("data", []):
                # Format: [timestamp, open, high, low, close, volume]
                candles.append(CandleData(
                    symbol=symbol,
                    token=token,
                    timeframe=interval,
                    timestamp=datetime.strptime(row[0], "%Y-%m-%dT%H:%M:%S%z"),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=int(row[5]),
                ))
            
            return candles
            
        except Exception as e:
            logger.error(f"Historical data error for {symbol}: {e}")
            return []
    
    async def backfill_symbol(
        self,
        symbol: str,
        token: str,
        exchange: str,
        timeframe: str,
        days: int = 30,
    ) -> list[CandleData]:
        """Backfill historical data for a symbol."""
        all_candles = []
        end_date = datetime.now()
        
        # Angel API limits: max 30 days per request for minute data
        chunk_days = 5 if timeframe == "1m" else 30
        
        current_end = end_date
        remaining_days = days
        
        while remaining_days > 0:
            chunk = min(remaining_days, chunk_days)
            current_start = current_end - timedelta(days=chunk)
            
            from_str = current_start.strftime("%Y-%m-%d 09:15")
            to_str = current_end.strftime("%Y-%m-%d 15:30")
            
            candles = self.get_historical_candles(
                symbol=symbol,
                token=token,
                exchange=exchange,
                interval=timeframe,
                from_date=from_str,
                to_date=to_str,
            )
            
            all_candles.extend(candles)
            current_end = current_start
            remaining_days -= chunk
            
            # Rate limiting
            await asyncio.sleep(0.5)
        
        logger.info(f"Backfilled {len(all_candles)} candles for {symbol} ({timeframe})")
        return all_candles
    
    def start_websocket(
        self,
        tokens: list[dict],
        on_tick: Callable[[TickData], Awaitable[None]],
    ):
        """
        Start WebSocket connection for real-time ticks.
        
        Args:
            tokens: List of {"exchange": "NSE", "token": "2885"} dicts
            on_tick: Async callback for each tick
        """
        self._tick_callback = on_tick
        
        # Build token list for WebSocket subscription
        # Format: exchange_type|token
        # exchange_type: 1=NSE, 2=NFO, 3=BSE
        exchange_map = {"NSE": 1, "NFO": 2, "BSE": 3}
        
        correlation_id = "gameplan_ws"
        action = 1  # Subscribe
        mode = 3  # SnapQuote mode (full data)
        
        token_list = []
        for t in tokens:
            exchange_type = exchange_map.get(t["exchange"], 1)
            token_list.append({
                "exchangeType": exchange_type,
                "tokens": [t["token"]],
            })
        
        self._websocket = SmartWebSocketV2(
            self._auth_token,
            settings.angel.api_key,
            settings.angel.client_code,
            self._feed_token,
        )
        
        # Build token-to-symbol map
        self._token_map = {t["token"]: t.get("symbol", t["token"]) for t in tokens}
        
        def on_data(wsapp, message):
            self._handle_tick(message)
        
        def on_open(wsapp):
            logger.info("WebSocket connected")
            self._connected = True
            self._websocket.subscribe(correlation_id, mode, token_list)
        
        def on_error(wsapp, error):
            logger.error(f"WebSocket error: {error}")
            self._connected = False
        
        def on_close(wsapp):
            logger.info("WebSocket closed")
            self._connected = False
        
        self._websocket.on_open = on_open
        self._websocket.on_data = on_data
        self._websocket.on_error = on_error
        self._websocket.on_close = on_close
        
        self._websocket.connect()
    
    def _handle_tick(self, message: dict):
        """Parse WebSocket tick message."""
        try:
            token = str(message.get("token", ""))
            symbol = self._token_map.get(token, token)
            
            tick = TickData(
                symbol=symbol,
                token=token,
                timestamp=datetime.now(),
                ltp=float(message.get("last_traded_price", 0)) / 100,  # Angel sends in paise
                volume=int(message.get("volume_trade_for_the_day", 0)),
                open_interest=int(message.get("open_interest", 0)),
                best_bid=float(message.get("best_5_buy_data", [{}])[0].get("price", 0)) / 100 if message.get("best_5_buy_data") else None,
                best_ask=float(message.get("best_5_sell_data", [{}])[0].get("price", 0)) / 100 if message.get("best_5_sell_data") else None,
                bid_qty=int(message.get("best_5_buy_data", [{}])[0].get("quantity", 0)) if message.get("best_5_buy_data") else None,
                ask_qty=int(message.get("best_5_sell_data", [{}])[0].get("quantity", 0)) if message.get("best_5_sell_data") else None,
            )
            
            if self._tick_callback:
                # Schedule async callback
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(self._tick_callback(tick))
                else:
                    loop.run_until_complete(self._tick_callback(tick))
                    
        except Exception as e:
            logger.error(f"Tick parse error: {e}")
    
    def stop_websocket(self):
        """Stop WebSocket connection."""
        if self._websocket:
            try:
                self._websocket.close_connection()
            except Exception:
                pass
            self._connected = False
    
    @property
    def is_connected(self) -> bool:
        return self._connected
