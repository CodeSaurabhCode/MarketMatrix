"""
Utility functions - logging setup, helpers.
"""
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from trading_system.config.settings import settings


def setup_logging(name: str = "trading_system") -> logging.Logger:
    """Configure and return a logger."""
    log_dir = Path(settings.app.log_dir)
    today = datetime.now().strftime("%Y-%m-%d")
    log_path = log_dir / today
    log_path.mkdir(parents=True, exist_ok=True)
    
    log_file = log_path / f"{name}.log"
    
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, settings.app.log_level, logging.INFO))
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    console_handler.setFormatter(console_fmt)
    
    # File handler
    file_handler = logging.FileHandler(str(log_file))
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s"
    )
    file_handler.setFormatter(file_fmt)
    
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    
    return logger


def is_market_hours() -> bool:
    """Check if current time is within market hours (IST)."""
    now = datetime.now()
    market_open = now.replace(
        hour=settings.app.market_open_hour,
        minute=settings.app.market_open_minute,
        second=0,
    )
    market_close = now.replace(
        hour=settings.app.market_close_hour,
        minute=settings.app.market_close_minute,
        second=0,
    )
    return market_open <= now <= market_close


def is_trading_day() -> bool:
    """Check if today is a trading day (Mon-Fri, not holiday)."""
    today = datetime.now()
    # Weekend check
    if today.weekday() >= 5:
        return False
    # TODO: Add NSE holiday calendar
    return True


def round_to_tick(price: float, tick_size: float = 0.05) -> float:
    """Round price to nearest tick size."""
    return round(price / tick_size) * tick_size
