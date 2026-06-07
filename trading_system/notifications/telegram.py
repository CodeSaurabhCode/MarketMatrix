"""
Telegram Notification Service

Sends formatted trade signal alerts to Telegram.
Includes rate limiting and message formatting.
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

import httpx

from trading_system.config.settings import settings
from trading_system.core.schemas import SignalCreate, Direction

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Sends trade signals to Telegram."""
    
    TELEGRAM_API_BASE = "https://api.telegram.org/bot"
    
    def __init__(self):
        self._bot_token = settings.telegram.bot_token
        self._chat_id = settings.telegram.chat_id
        self._enabled = settings.telegram.enabled
        self._rate_limit_seconds = 5  # Min seconds between messages
        self._last_sent: Optional[datetime] = None
        self._client: Optional[httpx.AsyncClient] = None
    
    async def connect(self):
        """Initialize HTTP client."""
        self._client = httpx.AsyncClient(timeout=30.0)
    
    async def disconnect(self):
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
    
    async def send_signal(self, signal: SignalCreate) -> bool:
        """
        Send trade signal notification to Telegram.
        
        Returns True if sent successfully.
        """
        if not self._enabled:
            logger.info(f"Telegram disabled, skipping signal for {signal.symbol}")
            return False
        
        if not self._bot_token or not self._chat_id:
            logger.warning("Telegram bot token or chat ID not configured")
            return False
        
        # Rate limiting
        if self._last_sent:
            elapsed = (datetime.now() - self._last_sent).total_seconds()
            if elapsed < self._rate_limit_seconds:
                await asyncio.sleep(self._rate_limit_seconds - elapsed)
        
        message = self._format_signal_message(signal)
        success = await self._send_message(message)
        
        if success:
            self._last_sent = datetime.now()
            logger.info(f"Signal notification sent for {signal.symbol} ({signal.direction.value})")
        
        return success
    
    def _format_signal_message(self, signal: SignalCreate) -> str:
        """Format signal into Telegram message."""
        emoji = "🟢" if signal.direction == Direction.LONG else "🔴"
        direction_text = "LONG" if signal.direction == Direction.LONG else "SHORT"
        
        # Component breakdown
        components = []
        if signal.liquidity_sweep_score > 0:
            components.append(f"  Liquidity Sweep: {signal.liquidity_sweep_score:.0f}/100")
        if signal.fvg_score > 0:
            components.append(f"  FVG Quality: {signal.fvg_score:.0f}/100")
        if signal.vwap_score > 0:
            components.append(f"  VWAP: {signal.vwap_score:.0f}/100")
        if signal.volume_profile_score > 0:
            components.append(f"  Volume Profile: {signal.volume_profile_score:.0f}/100")
        if signal.order_flow_score > 0:
            components.append(f"  Order Flow: {signal.order_flow_score:.0f}/100")
        
        component_text = "\n".join(components) if components else "  N/A"
        
        # Calculate R:R
        entry_mid = (signal.entry_zone_low + signal.entry_zone_high) / 2
        risk = abs(entry_mid - signal.stop_loss)
        reward_1 = abs(signal.target_1 - entry_mid) if signal.target_1 else 0
        reward_2 = abs(signal.target_2 - entry_mid) if signal.target_2 else 0
        rr_1 = f"{reward_1/risk:.1f}" if risk > 0 else "N/A"
        rr_2 = f"{reward_2/risk:.1f}" if risk > 0 and signal.target_2 else "N/A"
        
        message = f"""
{emoji} <b>TRADE SIGNAL - {direction_text}</b> {emoji}

<b>Ticker:</b> {signal.symbol}
<b>Signal:</b> {direction_text}
<b>Timeframe:</b> {signal.timeframe or 'Multi-TF'}

━━━━━━━━━━━━━━━━━━
<b>📊 Levels</b>
<b>Entry Zone:</b> ₹{signal.entry_zone_low:.2f} - ₹{signal.entry_zone_high:.2f}
<b>Stop Loss:</b> ₹{signal.stop_loss:.2f}
<b>Target 1:</b> ₹{signal.target_1:.2f} (R:R {rr_1})
<b>Target 2:</b> ₹{signal.target_2:.2f} (R:R {rr_2})

━━━━━━━━━━━━━━━━━━
<b>📈 Component Scores</b>
{component_text}

━━━━━━━━━━━━━━━━━━
<b>🎯 Confidence Score: {signal.confidence_score:.0f}/100</b>

━━━━━━━━━━━━━━━━━━
<b>📝 Reasoning</b>
{signal.reasoning or 'N/A'}

━━━━━━━━━━━━━━━━━━
<i>⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST</i>
<i>⚠️ For analysis only. No execution.</i>
"""
        return message.strip()
    
    async def _send_message(self, text: str) -> bool:
        """Send message via Telegram Bot API."""
        if not self._client:
            await self.connect()
        
        url = f"{self.TELEGRAM_API_BASE}{self._bot_token}/sendMessage"
        
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        
        try:
            response = await self._client.post(url, json=payload)
            
            if response.status_code == 200:
                return True
            else:
                logger.error(f"Telegram API error: {response.status_code} - {response.text}")
                return False
                
        except httpx.TimeoutException:
            logger.error("Telegram API timeout")
            return False
        except Exception as e:
            logger.error(f"Telegram send error: {e}")
            return False
    
    async def send_system_alert(self, message: str) -> bool:
        """Send a system/operational alert."""
        if not self._enabled:
            return False
        
        formatted = f"⚙️ <b>SYSTEM ALERT</b>\n\n{message}\n\n<i>{datetime.now().strftime('%H:%M:%S')}</i>"
        return await self._send_message(formatted)
    
    async def send_daily_summary(self, summary: dict) -> bool:
        """Send end-of-day summary."""
        if not self._enabled:
            return False
        
        message = f"""
📊 <b>DAILY SUMMARY - {datetime.now().strftime('%Y-%m-%d')}</b>

<b>Signals Generated:</b> {summary.get('total_signals', 0)}
<b>High Confidence (>80):</b> {summary.get('high_confidence', 0)}
<b>Notified:</b> {summary.get('notified', 0)}

<b>Outcomes (if tracked):</b>
  Wins: {summary.get('wins', 0)}
  Losses: {summary.get('losses', 0)}
  Active: {summary.get('active', 0)}

<b>Best Setup:</b> {summary.get('best_setup', 'N/A')}
"""
        return await self._send_message(message.strip())
