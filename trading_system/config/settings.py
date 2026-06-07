import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings

# Load .env file globally
load_dotenv(override=True)


BASE_DIR = Path(__file__).resolve().parent.parent.parent


class DatabaseSettings(BaseSettings):
    host: str = "localhost"
    port: int = 5432
    name: str = "trading_signals"
    user: str = "postgres"
    password: str = "postgres"
    
    @property
    def url(self) -> str:
        return f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"
    
    @property
    def sync_url(self) -> str:
        return f"postgresql+psycopg2://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"

    model_config = {"env_prefix": "DB_"}


class RedisSettings(BaseSettings):
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: Optional[str] = None
    
    @property
    def url(self) -> str:
        if self.password:
            return f"redis://:{self.password}@{self.host}:{self.port}/{self.db}"
        return f"redis://{self.host}:{self.port}/{self.db}"

    model_config = {"env_prefix": "REDIS_"}


class AngelOneSettings(BaseSettings):
    api_key: str = ""
    client_code: str = ""
    password: str = ""
    totp_secret: str = ""
    feed_token: Optional[str] = None
    
    model_config = {"env_prefix": "ANGEL_"}


class TelegramSettings(BaseSettings):
    bot_token: str = ""
    chat_id: str = ""
    enabled: bool = True
    
    model_config = {"env_prefix": "TELEGRAM_"}


class SignalSettings(BaseSettings):
    min_confidence_score: float = 80.0
    lookback_periods: int = 50
    equal_level_tolerance: float = 0.001  # 0.1% tolerance for equal highs/lows
    fvg_min_gap_percent: float = 0.1  # Minimum gap size as % of price
    volume_spike_threshold: float = 2.0  # 2x average volume
    sweep_min_wick_ratio: float = 0.6  # Wick must be 60% of candle range
    vwap_proximity_percent: float = 0.3  # Within 0.3% of VWAP
    
    model_config = {"env_prefix": "SIGNAL_"}


class AppSettings(BaseSettings):
    debug: bool = False
    log_level: str = "INFO"
    log_dir: str = str(BASE_DIR / "logs")
    
    # Timeframes to monitor (in minutes)
    timeframes: list[int] = [1, 5, 15]
    
    # Market hours (IST)
    market_open_hour: int = 9
    market_open_minute: int = 15
    market_close_hour: int = 15
    market_close_minute: int = 30
    
    # Data settings
    scrip_master_file: str = str(BASE_DIR / "OpenAPIScripMaster.json")
    trading_config_file: str = str(BASE_DIR / "trading_config.json")
    
    # Backfill settings
    backfill_days: int = 30
    
    model_config = {"env_prefix": "APP_"}


class Settings:
    def __init__(self):
        self.db = DatabaseSettings()
        self.redis = RedisSettings()
        self.angel = AngelOneSettings()
        self.telegram = TelegramSettings()
        self.signal = SignalSettings()
        self.app = AppSettings()


settings = Settings()
