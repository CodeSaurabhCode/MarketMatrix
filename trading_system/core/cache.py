"""
Redis cache manager for real-time data caching.
Includes fallback in-memory cache if Redis is unavailable.
"""
import json
import logging
from typing import Optional, Any
from datetime import timedelta, datetime

import redis.asyncio as redis

from trading_system.config.settings import settings

logger = logging.getLogger(__name__)


class InMemoryCacheFallback:
    """In-memory cache fallback for when Redis is unavailable."""
    
    def __init__(self):
        self._cache: dict[str, tuple[Any, Optional[datetime]]] = {}
    
    def _cleanup_expired(self):
        """Remove expired keys."""
        now = datetime.now()
        expired_keys = [k for k, (_, exp) in self._cache.items() if exp and exp < now]
        for k in expired_keys:
            del self._cache[k]
    
    async def set(self, key: str, value: str, ex: Optional[int] = None):
        exp_time = datetime.now() if ex is None else datetime.now() + timedelta(seconds=ex)
        self._cache[key] = (value, exp_time)
    
    async def get(self, key: str) -> Optional[str]:
        self._cleanup_expired()
        if key in self._cache:
            val, exp = self._cache[key]
            if exp is None or exp >= datetime.now():
                return val
            else:
                del self._cache[key]
        return None
    
    async def exists(self, key: str) -> int:
        self._cleanup_expired()
        return 1 if key in self._cache else 0
    
    async def close(self):
        pass


class CacheManager:
    def __init__(self):
        self._redis: Optional[redis.Redis] = None
        self._fallback: Optional[InMemoryCacheFallback] = None
        self._use_fallback = False
    
    async def connect(self):
        try:
            self._redis = redis.from_url(
                settings.redis.url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=5,
            )
            await self._redis.ping()
            logger.info("[REDIS OK] Redis connected successfully")
        except Exception as e:
            logger.warning(f"[WARNING] Redis connection failed: {e}")
            logger.info("Using in-memory cache fallback instead")
            self._fallback = InMemoryCacheFallback()
            self._use_fallback = True
    
    async def disconnect(self):
        if self._redis:
            await self._redis.close()
        if self._fallback:
            await self._fallback.close()
    
    @property
    def redis(self):
        if self._use_fallback:
            return self._fallback
        if not self._redis:
            raise RuntimeError("Redis not connected. Call connect() first.")
        return self._redis
    
    # ─── Price Cache ─────────────────────────────────────────────────────────
    
    async def set_ltp(self, symbol: str, price: float):
        await self.redis.set(f"ltp:{symbol}", str(price), ex=60)
    
    async def get_ltp(self, symbol: str) -> Optional[float]:
        val = await self.redis.get(f"ltp:{symbol}")
        return float(val) if val else None
    
    # ─── Candle Cache ────────────────────────────────────────────────────────
    
    async def set_latest_candle(self, symbol: str, timeframe: str, candle: dict):
        key = f"candle:{symbol}:{timeframe}"
        await self.redis.set(key, json.dumps(candle, default=str), ex=300)
    
    async def get_latest_candle(self, symbol: str, timeframe: str) -> Optional[dict]:
        key = f"candle:{symbol}:{timeframe}"
        val = await self.redis.get(key)
        return json.loads(val) if val else None
    
    # ─── VWAP Cache ──────────────────────────────────────────────────────────
    
    async def set_vwap(self, symbol: str, anchor_type: str, value: float):
        key = f"vwap:{symbol}:{anchor_type}"
        await self.redis.set(key, str(value), ex=300)
    
    async def get_vwap(self, symbol: str, anchor_type: str) -> Optional[float]:
        key = f"vwap:{symbol}:{anchor_type}"
        val = await self.redis.get(key)
        return float(val) if val else None
    
    # ─── Volume Profile Cache ────────────────────────────────────────────────
    
    async def set_volume_profile(self, symbol: str, profile: dict):
        key = f"vprofile:{symbol}"
        await self.redis.set(key, json.dumps(profile, default=str), ex=600)
    
    async def get_volume_profile(self, symbol: str) -> Optional[dict]:
        key = f"vprofile:{symbol}"
        val = await self.redis.get(key)
        return json.loads(val) if val else None
    
    # ─── Signal Deduplication ────────────────────────────────────────────────
    
    async def is_signal_sent(self, symbol: str, direction: str, timeframe: str) -> bool:
        """Prevent duplicate signals within cooldown period."""
        key = f"signal_sent:{symbol}:{direction}:{timeframe}"
        return await self.redis.exists(key) > 0
    
    async def mark_signal_sent(self, symbol: str, direction: str, timeframe: str, cooldown_minutes: int = 30):
        key = f"signal_sent:{symbol}:{direction}:{timeframe}"
        await self.redis.set(key, "1", ex=cooldown_minutes * 60)
    
    # ─── Generic Cache ───────────────────────────────────────────────────────
    
    async def set_json(self, key: str, data: Any, ttl_seconds: int = 300):
        await self.redis.set(key, json.dumps(data, default=str), ex=ttl_seconds)
    
    async def get_json(self, key: str) -> Optional[Any]:
        val = await self.redis.get(key)
        return json.loads(val) if val else None


cache = CacheManager()
