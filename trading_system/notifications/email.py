"""
Email Notification Service

Sends formatted trade signal alerts via email using Gmail SMTP.
Includes rate limiting and HTML message formatting.
"""
import asyncio
import logging
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from trading_system.config.settings import settings
from trading_system.core.schemas import SignalCreate, Direction

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent.parent


class EmailNotifier:
    """Sends trade signals via email."""
    
    GMAIL_SMTP_SERVER = "smtp.gmail.com"
    GMAIL_SMTP_PORT = 587
    RECIPIENTS_FILE = BASE_DIR / "recipients.txt"
    
    def __init__(self):
        self._email = settings.email.email
        self._app_pass_key = settings.email.app_pass_key
        self._enabled = settings.email.enabled
        self._rate_limit_seconds = 2  # Min seconds between messages
        self._last_sent: Optional[datetime] = None
        self._recipients: list[str] = []
    
    async def connect(self):
        """Initialize email service (no persistent connection needed for SMTP)."""
        if not self._email or not self._app_pass_key:
            logger.warning("Email credentials not configured")
            self._enabled = False
            return
        
        # Load recipients from file
        self._load_recipients()
        
        if not self._recipients:
            logger.warning("No recipients configured in recipients.txt")
            self._enabled = False
    
    def _load_recipients(self):
        """Load email recipients from recipients.txt file."""
        try:
            if self.RECIPIENTS_FILE.exists():
                with open(self.RECIPIENTS_FILE, 'r') as f:
                    self._recipients = [
                        email.strip() 
                        for email in f.readlines() 
                        if email.strip() and not email.strip().startswith('#')
                    ]
                logger.info(f"Loaded {len(self._recipients)} email recipients")
            else:
                logger.warning(f"Recipients file not found: {self.RECIPIENTS_FILE}")
        except Exception as e:
            logger.error(f"Error loading recipients: {e}")
    
    async def disconnect(self):
        """Close email service (SMTP is stateless)."""
        pass
    
    async def send_signal(self, signal: SignalCreate) -> bool:
        """
        Send trade signal notification via email.
        
        Returns True if sent successfully.
        """
        if not self._enabled:
            logger.info(f"Email disabled, skipping signal for {signal.symbol}")
            return False
        
        if not self._email or not self._app_pass_key:
            logger.warning("Email credentials not configured")
            return False
        
        if not self._recipients:
            logger.warning(f"No recipients configured, skipping signal for {signal.symbol}")
            return False
        
        # Rate limiting
        if self._last_sent:
            elapsed = (datetime.now() - self._last_sent).total_seconds()
            if elapsed < self._rate_limit_seconds:
                await asyncio.sleep(self._rate_limit_seconds - elapsed)
        
        subject = self._format_subject(signal)
        html_body = self._format_signal_html(signal)
        
        success = await self._send_email(subject, html_body)
        
        if success:
            self._last_sent = datetime.now()
            logger.info(f"Signal email sent for {signal.symbol} ({signal.direction.value})")
        
        return success
    
    def _format_subject(self, signal: SignalCreate) -> str:
        """Format email subject line."""
        direction = "LONG" if signal.direction == Direction.LONG else "SHORT"
        return f"🎯 Trade Signal: {signal.symbol} - {direction}"
    
    def _format_signal_html(self, signal: SignalCreate) -> str:
        """Format signal into HTML email body."""
        direction_text = "LONG" if signal.direction == Direction.LONG else "SHORT"
        direction_color = "#00B050" if signal.direction == Direction.LONG else "#FF0000"
        emoji = "📈" if signal.direction == Direction.LONG else "📉"
        
        # Component breakdown
        components_html = ""
        if signal.liquidity_sweep_score > 0:
            components_html += f"<tr><td>Liquidity Sweep:</td><td><strong>{signal.liquidity_sweep_score:.0f}/100</strong></td></tr>\n"
        if signal.fvg_score > 0:
            components_html += f"<tr><td>FVG Quality:</td><td><strong>{signal.fvg_score:.0f}/100</strong></td></tr>\n"
        if signal.vwap_score > 0:
            components_html += f"<tr><td>VWAP:</td><td><strong>{signal.vwap_score:.0f}/100</strong></td></tr>\n"
        if signal.volume_profile_score > 0:
            components_html += f"<tr><td>Volume Profile:</td><td><strong>{signal.volume_profile_score:.0f}/100</strong></td></tr>\n"
        if signal.order_flow_score > 0:
            components_html += f"<tr><td>Order Flow:</td><td><strong>{signal.order_flow_score:.0f}/100</strong></td></tr>\n"
        
        if not components_html:
            components_html = "<tr><td colspan='2'>N/A</td></tr>\n"
        
        # Calculate R:R
        entry_mid = (signal.entry_zone_low + signal.entry_zone_high) / 2
        risk = abs(entry_mid - signal.stop_loss)
        reward_1 = abs(signal.target_1 - entry_mid) if signal.target_1 else 0
        reward_2 = abs(signal.target_2 - entry_mid) if signal.target_2 else 0
        rr_1 = f"{reward_1/risk:.1f}" if risk > 0 else "N/A"
        rr_2 = f"{reward_2/risk:.1f}" if risk > 0 and signal.target_2 else "N/A"
        
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; background-color: #f9f9f9; padding: 20px; border-radius: 8px; }}
        .header {{ background-color: {direction_color}; color: white; padding: 15px; border-radius: 5px; text-align: center; margin-bottom: 20px; }}
        .header h1 {{ margin: 0; font-size: 24px; }}
        .section {{ background-color: white; padding: 15px; margin-bottom: 15px; border-left: 4px solid {direction_color}; border-radius: 3px; }}
        .section h2 {{ margin-top: 0; color: {direction_color}; font-size: 16px; border-bottom: 2px solid {direction_color}; padding-bottom: 10px; }}
        table {{ width: 100%; border-collapse: collapse; }}
        td {{ padding: 8px; border-bottom: 1px solid #eee; }}
        td:first-child {{ font-weight: bold; width: 45%; color: #555; }}
        td:last-child {{ text-align: right; }}
        .confidence {{ background-color: #f0f8ff; padding: 10px; border-radius: 3px; text-align: center; font-size: 18px; font-weight: bold; color: {direction_color}; }}
        .reasoning {{ background-color: #fafafa; padding: 10px; border-radius: 3px; font-style: italic; line-height: 1.8; }}
        .footer {{ text-align: center; font-size: 12px; color: #888; margin-top: 20px; padding-top: 10px; border-top: 1px solid #ddd; }}
        .disclaimer {{ color: #ff6600; font-weight: bold; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{emoji} TRADE SIGNAL - {direction_text} {emoji}</h1>
        </div>
        
        <div class="section">
            <h2>📋 Trade Details</h2>
            <table>
                <tr><td>Ticker:</td><td><strong>{signal.symbol}</strong></td></tr>
                <tr><td>Direction:</td><td><strong>{direction_text}</strong></td></tr>
                <tr><td>Timeframe:</td><td><strong>{signal.timeframe or 'Multi-TF'}</strong></td></tr>
            </table>
        </div>
        
        <div class="section">
            <h2>📊 Price Levels</h2>
            <table>
                <tr><td>Entry Zone:</td><td>₹{signal.entry_zone_low:.2f} - ₹{signal.entry_zone_high:.2f}</td></tr>
                <tr><td>Stop Loss:</td><td>₹{signal.stop_loss:.2f}</td></tr>
                <tr><td>Target 1:</td><td>₹{signal.target_1:.2f} (R:R {rr_1})</td></tr>
                <tr><td>Target 2:</td><td>₹{signal.target_2:.2f} (R:R {rr_2})</td></tr>
            </table>
        </div>
        
        <div class="section">
            <h2>📈 Component Scores</h2>
            <table>
                {components_html}
            </table>
        </div>
        
        <div class="section">
            <div class="confidence">
                🎯 Confidence Score: {signal.confidence_score:.0f}/100
            </div>
        </div>
        
        <div class="section">
            <h2>📝 Reasoning</h2>
            <div class="reasoning">
                {signal.reasoning or 'N/A'}
            </div>
        </div>
        
        <div class="footer">
            <p>⏰ <strong>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST</strong></p>
            <p><span class="disclaimer">⚠️ For analysis only. No execution.</span></p>
            <p>Sent by Trading Signal Detection System</p>
        </div>
    </div>
</body>
</html>
"""
        return html
    
    async def send_system_alert(self, message: str) -> bool:
        """Send system alert via email."""
        if not self._enabled or not self._email or not self._app_pass_key:
            logger.info(f"Email disabled or unconfigured, skipping alert")
            return False
        
        if not self._recipients:
            logger.warning("No recipients configured, skipping system alert")
            return False
        
        subject = f"⚠️ Trading System Alert - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{ font-family: Arial, sans-serif; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; background-color: #fff3cd; padding: 20px; border-radius: 8px; }}
        .header {{ color: #ff6600; font-size: 18px; font-weight: bold; margin-bottom: 15px; }}
        .content {{ background-color: white; padding: 15px; border-radius: 3px; line-height: 1.6; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">⚠️ System Alert</div>
        <div class="content">
            <p>{message}</p>
            <p style="color: #888; font-size: 12px; margin-top: 20px;">
                Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST<br>
                Trading Signal Detection System
            </p>
        </div>
    </div>
</body>
</html>
"""
        
        return await self._send_email(subject, html_body)
    
    async def _send_email(self, subject: str, html_body: str) -> bool:
        """Send email via Gmail SMTP to all configured recipients."""
        if not self._recipients:
            logger.warning("No recipients configured. Skipping email.")
            return False
        
        try:
            # Create message
            message = MIMEMultipart("alternative")
            message["Subject"] = subject
            message["From"] = self._email
            message["To"] = ", ".join(self._recipients)
            
            # Attach HTML body
            html_part = MIMEText(html_body, "html", "utf-8")
            message.attach(html_part)
            
            # Send email to all recipients
            with smtplib.SMTP(self.GMAIL_SMTP_SERVER, self.GMAIL_SMTP_PORT) as server:
                server.starttls()
                server.login(self._email, self._app_pass_key)
                server.sendmail(self._email, self._recipients, message.as_string())
            
            logger.debug(f"Email sent successfully to {len(self._recipients)} recipient(s): {', '.join(self._recipients)}")
            return True
            
        except smtplib.SMTPAuthenticationError:
            logger.error("Gmail authentication failed. Check email and app password.")
            return False
        except smtplib.SMTPException as e:
            logger.error(f"SMTP error: {e}")
            return False
        except Exception as e:
            logger.error(f"Email send error: {e}")
            return False
