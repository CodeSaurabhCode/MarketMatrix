"""
Symbol manager - loads and manages the trading universe.
"""
import json
import logging
from pathlib import Path
from typing import Optional

import pandas as pd

from trading_system.config.settings import settings

logger = logging.getLogger(__name__)


class SymbolManager:
    """Manages the trading symbol universe using OpenAPIScripMaster.json."""
    
    def __init__(self):
        self._scrip_master: Optional[pd.DataFrame] = None
        self._focused_symbols: list[dict] = []
        self._symbol_map: dict = {}  # Quick lookup by symbol
    
    def load_scrip_master(self) -> pd.DataFrame:
        """Load the full scrip master for token lookups."""
        scrip_path = Path(settings.app.scrip_master_file)
        
        if not scrip_path.exists():
            logger.warning(f"Scrip master not found: {scrip_path}")
            return pd.DataFrame()
        
        self._scrip_master = pd.read_json(scrip_path)
        logger.info(f"Loaded scrip master with {len(self._scrip_master)} entries")
        
        # Build quick lookup map
        self._symbol_map = {row['symbol']: row for _, row in self._scrip_master.iterrows()}
        
        return self._scrip_master
    
    def load_focused_config(self) -> list[dict]:
        """Load focused trading config to track only specific symbols."""
        config_path = Path(settings.app.trading_config_file)
        
        if not config_path.exists():
            logger.warning(f"Trading config not found: {config_path}")
            logger.info("Will use all scraped symbols from scrip master")
            return []
        
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            
            self._focused_symbols = config.get("symbols_to_track", [])
            logger.info(f"Loaded {len(self._focused_symbols)} focused symbols for tracking")
            
            # Enrich focused symbols with token info from scrip master
            enriched = []
            for sym in self._focused_symbols:
                symbol = sym['symbol']
                if symbol in self._symbol_map:
                    row = self._symbol_map[symbol]
                    enriched.append({
                        'symbol': symbol,
                        'name': sym.get('name', row.get('name', '')),
                        'type': sym['type'],
                        'token': str(row['token']),
                        'exchange': row.get('exch_seg', 'NSE'),
                        'instrumenttype': row.get('instrumenttype', ''),
                    })
                else:
                    logger.warning(f"Symbol not found in scrip master: {symbol}")
            
            self._focused_symbols = enriched
            logger.info(f"Enriched {len(self._focused_symbols)} symbols with token info")
            return self._focused_symbols
        
        except Exception as e:
            logger.error(f"Error loading trading config: {e}")
            return []
    
    def get_token(self, symbol: str) -> Optional[str]:
        """Get token for a trading symbol."""
        if self._scrip_master is None or self._scrip_master.empty:
            logger.warning("Scrip master not loaded. Call load_scrip_master() first.")
            return None
        
        # Search by symbol
        result = self._scrip_master[self._scrip_master["symbol"] == symbol]
        if not result.empty:
            return str(result.iloc[0]["token"])
        
        # Fallback: search by name
        result = self._scrip_master[self._scrip_master["name"].str.contains(symbol, case=False, na=False)]
        if not result.empty:
            return str(result.iloc[0]["token"])
        
        return None
    
    def get_equity_symbols(self) -> list[dict]:
        """Get only equity symbols from scrip master.
        
        Angel One uses empty instrumenttype or specific equity types for stocks.
        """
        if self._scrip_master is None or self._scrip_master.empty:
            logger.warning("Scrip master not loaded. Call load_scrip_master() first.")
            return []
        
        # Filter equities: empty instrumenttype or contains stock-like indicators
        equities = self._scrip_master[
            (self._scrip_master["instrumenttype"].isin(['', 'UNDIRT', 'FUTSTK', 'OPTSTK']))
            & (self._scrip_master["exch_seg"].isin(['NSE', 'BSE']))
        ]
        return equities.to_dict(orient="records")
    
    def get_index_symbols(self) -> list[dict]:
        """Get only index symbols from scrip master.
        
        Angel One uses 'AMXIDX' for indices.
        """
        if self._scrip_master is None or self._scrip_master.empty:
            logger.warning("Scrip master not loaded. Call load_scrip_master() first.")
            return []
        
        # Indices use AMXIDX as instrumenttype
        indices = self._scrip_master[self._scrip_master["instrumenttype"] == "AMXIDX"]
        return indices.to_dict(orient="records")
    
    def get_focused_symbols(self) -> list[dict]:
        """Get only the focused symbols defined in trading_config.json."""
        return self._focused_symbols
    
    def get_focused_websocket_tokens(self) -> list[dict]:
        """Get tokens formatted for WebSocket subscription (focused symbols only)."""
        if not self._focused_symbols:
            logger.warning("No focused symbols loaded. Call load_focused_config() first.")
            return []
        
        return [
            {
                "exchange": sym.get("exchange", "NSE"),
                "token": sym.get("token"),
                "symbol": sym.get("symbol", ""),
            }
            for sym in self._focused_symbols
        ]
    
    def get_websocket_tokens(self) -> list[dict]:
        """Get tokens formatted for WebSocket subscription.
        
        Returns ONLY focused symbols. Raises error if no focused config loaded.
        """
        if not self._focused_symbols:
            logger.error("No focused symbols available!")
            logger.error("trading_config.json not found or empty.")
            logger.error("Please create/configure trading_config.json with symbols_to_track.")
            raise RuntimeError(
                "Focused symbols required. "
                "Create trading_config.json with symbols_to_track list."
            )
        
        return self.get_focused_websocket_tokens()
    
    @property
    def symbols(self) -> list[dict]:
        """Return symbols - focused if available, otherwise all."""
        if self._focused_symbols:
            return self._focused_symbols
        
        if self._scrip_master is None or self._scrip_master.empty:
            return []
        return self._scrip_master.to_dict(orient="records")


symbol_manager = SymbolManager()
