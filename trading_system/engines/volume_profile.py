"""
Volume Profile Engine

Calculates:
- POC (Point of Control) - price level with highest volume
- Value Area High (VAH) - upper boundary of 70% volume
- Value Area Low (VAL) - lower boundary of 70% volume
- High Volume Nodes (HVN)
- Low Volume Nodes (LVN)

Detects:
- Price rejection from POC
- Price acceptance into value
- LVN breakout
- HVN reversal
"""
import logging
from datetime import datetime
from typing import Optional

import numpy as np

from trading_system.config.settings import settings
from trading_system.core.schemas import CandleData, VolumeProfileData

logger = logging.getLogger(__name__)


class VolumeProfileEngine:
    """Calculates volume profile and detects price interactions with volume nodes."""
    
    def __init__(self, num_bins: int = 50, value_area_pct: float = 0.70):
        """
        Args:
            num_bins: Number of price bins for the profile
            value_area_pct: Percentage of volume to define value area (default 70%)
        """
        self._num_bins = num_bins
        self._value_area_pct = value_area_pct
        
        # Cached profiles: {symbol: VolumeProfileData}
        self._profiles: dict[str, VolumeProfileData] = {}
    
    def calculate_profile(
        self,
        candles: list[CandleData],
        symbol: str,
        timeframe: str,
    ) -> Optional[VolumeProfileData]:
        """
        Calculate volume profile from OHLCV candles.
        
        Distributes each candle's volume across the price range it covers,
        weighted toward the close (TPO approximation).
        """
        if not candles:
            return None
        
        # Determine price range
        all_highs = [c.high for c in candles]
        all_lows = [c.low for c in candles]
        price_high = max(all_highs)
        price_low = min(all_lows)
        
        if price_high == price_low:
            return None
        
        # Create price bins
        bin_edges = np.linspace(price_low, price_high, self._num_bins + 1)
        bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
        bin_volumes = np.zeros(self._num_bins)
        
        # Distribute volume across bins
        for candle in candles:
            if candle.volume == 0:
                continue
            
            # Find bins that this candle covers
            candle_low_bin = np.searchsorted(bin_edges, candle.low, side="right") - 1
            candle_high_bin = np.searchsorted(bin_edges, candle.high, side="right") - 1
            
            candle_low_bin = max(0, min(candle_low_bin, self._num_bins - 1))
            candle_high_bin = max(0, min(candle_high_bin, self._num_bins - 1))
            
            # Distribute volume with weight toward close
            covered_bins = list(range(candle_low_bin, candle_high_bin + 1))
            if not covered_bins:
                continue
            
            # Weight distribution: more volume near the close price
            close_bin = np.searchsorted(bin_edges, candle.close, side="right") - 1
            close_bin = max(0, min(close_bin, self._num_bins - 1))
            
            weights = np.array([
                1.0 / (1.0 + abs(b - close_bin)) for b in covered_bins
            ])
            weights /= weights.sum()
            
            for idx, bin_idx in enumerate(covered_bins):
                bin_volumes[bin_idx] += candle.volume * weights[idx]
        
        # Calculate POC
        poc_bin = int(np.argmax(bin_volumes))
        poc_price = float(bin_centers[poc_bin])
        
        # Calculate Value Area (70% of total volume around POC)
        total_volume = bin_volumes.sum()
        value_area_volume = total_volume * self._value_area_pct
        
        vah, val = self._calculate_value_area(
            bin_centers, bin_volumes, poc_bin, value_area_volume
        )
        
        # Identify HVN and LVN
        hvn = self._find_high_volume_nodes(bin_centers, bin_volumes)
        lvn = self._find_low_volume_nodes(bin_centers, bin_volumes)
        
        profile = VolumeProfileData(
            symbol=symbol,
            timeframe=timeframe,
            session_date=candles[-1].timestamp,
            poc_price=poc_price,
            value_area_high=vah,
            value_area_low=val,
            high_volume_nodes=hvn,
            low_volume_nodes=lvn,
            total_volume=int(total_volume),
        )
        
        self._profiles[symbol] = profile
        return profile
    
    def _calculate_value_area(
        self,
        bin_centers: np.ndarray,
        bin_volumes: np.ndarray,
        poc_bin: int,
        target_volume: float,
    ) -> tuple[float, float]:
        """Calculate VAH and VAL using the TPO method (expanding from POC)."""
        accumulated = bin_volumes[poc_bin]
        upper = poc_bin
        lower = poc_bin
        
        while accumulated < target_volume:
            # Compare volume above vs below
            upper_vol = bin_volumes[upper + 1] if upper + 1 < len(bin_volumes) else 0
            lower_vol = bin_volumes[lower - 1] if lower - 1 >= 0 else 0
            
            if upper_vol == 0 and lower_vol == 0:
                break
            
            if upper_vol >= lower_vol:
                upper += 1
                accumulated += upper_vol
            else:
                lower -= 1
                accumulated += lower_vol
            
            if upper >= len(bin_volumes) - 1 and lower <= 0:
                break
        
        vah = float(bin_centers[min(upper, len(bin_centers) - 1)])
        val = float(bin_centers[max(lower, 0)])
        
        return vah, val
    
    def _find_high_volume_nodes(
        self, bin_centers: np.ndarray, bin_volumes: np.ndarray, threshold_pct: float = 0.7
    ) -> list[dict]:
        """Find price levels with significantly above-average volume."""
        avg_volume = np.mean(bin_volumes)
        threshold = avg_volume * (1 + threshold_pct)
        
        hvn = []
        for i, vol in enumerate(bin_volumes):
            if vol > threshold:
                hvn.append({
                    "price": float(bin_centers[i]),
                    "volume": float(vol),
                    "strength": float(vol / avg_volume),
                })
        
        # Sort by volume descending
        hvn.sort(key=lambda x: x["volume"], reverse=True)
        return hvn[:10]  # Top 10 HVNs
    
    def _find_low_volume_nodes(
        self, bin_centers: np.ndarray, bin_volumes: np.ndarray, threshold_pct: float = 0.3
    ) -> list[dict]:
        """Find price levels with significantly below-average volume (gaps in profile)."""
        avg_volume = np.mean(bin_volumes)
        threshold = avg_volume * threshold_pct
        
        lvn = []
        for i, vol in enumerate(bin_volumes):
            if 0 < vol < threshold:
                lvn.append({
                    "price": float(bin_centers[i]),
                    "volume": float(vol),
                    "weakness": float(1 - (vol / avg_volume)),
                })
        
        # Sort by weakness descending (least volume first)
        lvn.sort(key=lambda x: x["weakness"], reverse=True)
        return lvn[:10]
    
    def detect_poc_rejection(
        self, candles: list[CandleData], profile: VolumeProfileData
    ) -> Optional[dict]:
        """Detect price rejection from POC level."""
        if len(candles) < 2:
            return None
        
        latest = candles[-1]
        poc = profile.poc_price
        proximity = poc * 0.002  # 0.2% proximity
        
        # Price touched POC and bounced
        if (latest.low <= poc + proximity and latest.close > poc and
                latest.close > latest.open):
            return {
                "type": "POC_REJECTION_BULLISH",
                "poc_price": poc,
                "rejection_price": latest.low,
                "close_price": latest.close,
                "strength": min(abs(latest.close - poc) / poc * 1000, 100),
            }
        
        if (latest.high >= poc - proximity and latest.close < poc and
                latest.close < latest.open):
            return {
                "type": "POC_REJECTION_BEARISH",
                "poc_price": poc,
                "rejection_price": latest.high,
                "close_price": latest.close,
                "strength": min(abs(latest.close - poc) / poc * 1000, 100),
            }
        
        return None
    
    def detect_value_acceptance(
        self, candles: list[CandleData], profile: VolumeProfileData
    ) -> Optional[dict]:
        """Detect price accepting into value area (mean reversion)."""
        if len(candles) < 3:
            return None
        
        latest = candles[-1]
        prev = candles[-2]
        
        vah = profile.value_area_high
        val = profile.value_area_low
        
        # Entering value from above
        if prev.close > vah and latest.close <= vah and latest.close >= val:
            return {
                "type": "VALUE_ACCEPTANCE_FROM_ABOVE",
                "entry_price": latest.close,
                "vah": vah,
                "val": val,
            }
        
        # Entering value from below
        if prev.close < val and latest.close >= val and latest.close <= vah:
            return {
                "type": "VALUE_ACCEPTANCE_FROM_BELOW",
                "entry_price": latest.close,
                "vah": vah,
                "val": val,
            }
        
        return None
    
    def detect_lvn_breakout(
        self, candles: list[CandleData], profile: VolumeProfileData
    ) -> Optional[dict]:
        """Detect price breaking through a Low Volume Node (fast move expected)."""
        if len(candles) < 2 or not profile.low_volume_nodes:
            return None
        
        latest = candles[-1]
        prev = candles[-2]
        
        for lvn in profile.low_volume_nodes:
            lvn_price = lvn["price"]
            
            # Bullish breakout through LVN
            if prev.close < lvn_price and latest.close > lvn_price:
                return {
                    "type": "LVN_BREAKOUT_BULLISH",
                    "lvn_price": lvn_price,
                    "close_price": latest.close,
                    "weakness": lvn["weakness"],
                }
            
            # Bearish breakdown through LVN
            if prev.close > lvn_price and latest.close < lvn_price:
                return {
                    "type": "LVN_BREAKOUT_BEARISH",
                    "lvn_price": lvn_price,
                    "close_price": latest.close,
                    "weakness": lvn["weakness"],
                }
        
        return None
    
    def detect_hvn_reversal(
        self, candles: list[CandleData], profile: VolumeProfileData
    ) -> Optional[dict]:
        """Detect price reversal at a High Volume Node (support/resistance)."""
        if len(candles) < 2 or not profile.high_volume_nodes:
            return None
        
        latest = candles[-1]
        
        for hvn in profile.high_volume_nodes:
            hvn_price = hvn["price"]
            proximity = hvn_price * 0.002
            
            # Bullish reversal at HVN (support)
            if (latest.low <= hvn_price + proximity and
                    latest.close > hvn_price and latest.close > latest.open):
                return {
                    "type": "HVN_REVERSAL_BULLISH",
                    "hvn_price": hvn_price,
                    "strength": hvn["strength"],
                }
            
            # Bearish reversal at HVN (resistance)
            if (latest.high >= hvn_price - proximity and
                    latest.close < hvn_price and latest.close < latest.open):
                return {
                    "type": "HVN_REVERSAL_BEARISH",
                    "hvn_price": hvn_price,
                    "strength": hvn["strength"],
                }
        
        return None
    
    def get_profile(self, symbol: str) -> Optional[VolumeProfileData]:
        """Get cached volume profile for a symbol."""
        return self._profiles.get(symbol)
