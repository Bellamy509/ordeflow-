from pydantic_settings import BaseSettings
from pydantic import Field
from typing import List, Dict
from enum import Enum


class TradingMode(str, Enum):
    PAPER = "paper"
    LIVE = "live"


SYMBOL_DEFAULTS: Dict[str, Dict] = {
    "BTC/USDT:USDT": {"scale": 0.5, "imbalance_volume": 5, "min_size": 0.001},
    "ETH/USDT:USDT": {"scale": 0.1, "imbalance_volume": 5, "min_size": 0.01},
    "SOL/USDT:USDT": {"scale": 0.01, "imbalance_volume": 10, "min_size": 0.1},
    "XRP/USDT:USDT": {"scale": 0.0001, "imbalance_volume": 50, "min_size": 1.0},
    "DOGE/USDT:USDT": {"scale": 0.00001, "imbalance_volume": 100, "min_size": 10.0},
    "BNB/USDT:USDT": {"scale": 0.05, "imbalance_volume": 5, "min_size": 0.01},
    "AVAX/USDT:USDT": {"scale": 0.01, "imbalance_volume": 10, "min_size": 0.1},
    "LINK/USDT:USDT": {"scale": 0.005, "imbalance_volume": 10, "min_size": 0.1},
}


class Config(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    # Trading mode
    trading_mode: TradingMode = Field(default=TradingMode.PAPER)

    # Exchange
    exchange_api_key: str = Field(default="")
    exchange_api_secret: str = Field(default="")
    exchange_testnet: bool = Field(default=True)

    # Multi-market: comma-separated symbols
    symbols: str = Field(default="BTC/USDT:USDT")
    timeframe_minutes: int = Field(default=5)

    # Footprint defaults (per-symbol overrides via SYMBOL_DEFAULTS)
    footprint_scale: float = Field(default=0.5)
    imbalance_ratio: float = Field(default=3.0)
    imbalance_volume: float = Field(default=5)
    stacked_imbalance_min: int = Field(default=3)

    # Strategy
    min_confluence_score: float = Field(default=65.0)
    max_open_positions: int = Field(default=3)

    # Risk
    risk_per_trade_pct: float = Field(default=1.0)
    max_daily_loss_pct: float = Field(default=3.0)
    max_position_size_usd: float = Field(default=1000.0)
    default_leverage: int = Field(default=5)

    # Telegram
    telegram_bot_token: str = Field(default="")
    telegram_chat_id: str = Field(default="")

    # AI / Sentiment
    openai_api_key: str = Field(default="")

    # Trailing stop
    trailing_activation_pct: float = Field(default=0.3)
    trailing_distance_pct: float = Field(default=0.2)

    # Dashboard (Railway uses PORT env var)
    dashboard_host: str = Field(default="0.0.0.0")
    dashboard_port: int = Field(default=8080)
    port: int = Field(default=0)

    @property
    def effective_port(self) -> int:
        return self.port if self.port > 0 else self.dashboard_port

    @property
    def is_paper(self) -> bool:
        return self.trading_mode == TradingMode.PAPER

    @property
    def symbol_list(self) -> List[str]:
        return [s.strip() for s in self.symbols.split(",") if s.strip()]

    @property
    def binance_ws_base(self) -> str:
        return "wss://fstream.binance.com/ws"

    def get_symbol_config(self, symbol: str) -> Dict:
        defaults = SYMBOL_DEFAULTS.get(symbol, {})
        return {
            "scale": defaults.get("scale", self.footprint_scale),
            "imbalance_volume": defaults.get("imbalance_volume", self.imbalance_volume),
            "imbalance_ratio": self.imbalance_ratio,
            "stacked_imbalance_min": self.stacked_imbalance_min,
            "min_size": defaults.get("min_size", 0.001),
        }

    @staticmethod
    def ccxt_to_ws(symbol: str) -> str:
        """Convert CCXT symbol (BTC/USDT:USDT) to Binance WS format (btcusdt)."""
        base = symbol.split("/")[0].lower()
        quote = symbol.split("/")[1].split(":")[0].lower()
        return base + quote
