from __future__ import annotations
from pydantic import BaseModel, Field
from enum import Enum
from datetime import datetime
from typing import Optional, Dict, List


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class SignalType(str, Enum):
    STACKED_IMBALANCE_BUY = "stacked_imbalance_buy"
    STACKED_IMBALANCE_SELL = "stacked_imbalance_sell"
    DELTA_DIVERGENCE_BULL = "delta_divergence_bull"
    DELTA_DIVERGENCE_BEAR = "delta_divergence_bear"
    ABSORPTION_SUPPORT = "absorption_support"
    ABSORPTION_RESISTANCE = "absorption_resistance"
    EXHAUSTION_BULL = "exhaustion_bull"
    EXHAUSTION_BEAR = "exhaustion_bear"
    CVD_CONFIRMS_UP = "cvd_confirms_up"
    CVD_CONFIRMS_DOWN = "cvd_confirms_down"
    POC_MAGNET_LONG = "poc_magnet_long"
    POC_MAGNET_SHORT = "poc_magnet_short"


class PositionStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    PARTIALLY_CLOSED = "partially_closed"


class StrategyType(str, Enum):
    REVERSAL = "reversal"
    BREAKOUT = "breakout"
    POC_REVERSION = "poc_reversion"


# --- Raw data ---

class RawTick(BaseModel):
    timestamp: int
    price: float
    quantity: float
    is_buyer_maker: bool  # true = seller aggressor, false = buyer aggressor

    @property
    def is_buy(self) -> bool:
        return not self.is_buyer_maker

    @property
    def is_sell(self) -> bool:
        return self.is_buyer_maker


# --- Footprint data ---

class FootprintLevel(BaseModel):
    price: float
    bid_volume: float = 0.0
    ask_volume: float = 0.0
    trades: int = 0

    @property
    def delta(self) -> float:
        return self.ask_volume - self.bid_volume

    @property
    def total_volume(self) -> float:
        return self.bid_volume + self.ask_volume

    @property
    def imbalance_ratio(self) -> float:
        if self.bid_volume == 0:
            return float("inf") if self.ask_volume > 0 else 0
        return self.ask_volume / self.bid_volume

    @property
    def reverse_imbalance_ratio(self) -> float:
        if self.ask_volume == 0:
            return float("inf") if self.bid_volume > 0 else 0
        return self.bid_volume / self.ask_volume


class ValueArea(BaseModel):
    high: float
    low: float
    poc: float
    volume_at_poc: float


class FootprintCandle(BaseModel):
    timestamp: int
    open: float = 0.0
    high: float = 0.0
    low: float = float("inf")
    close: float = 0.0
    levels: Dict[float, FootprintLevel] = Field(default_factory=dict)
    total_bid: float = 0.0
    total_ask: float = 0.0
    total_trades: int = 0

    @property
    def delta(self) -> float:
        return self.total_ask - self.total_bid

    @property
    def total_volume(self) -> float:
        return self.total_bid + self.total_ask

    @property
    def poc(self) -> float:
        if not self.levels:
            return self.close
        return max(self.levels.values(), key=lambda l: l.total_volume).price

    def get_value_area(self, pct: float = 0.70) -> ValueArea:
        if not self.levels:
            return ValueArea(high=self.close, low=self.close, poc=self.close, volume_at_poc=0)

        sorted_levels = sorted(self.levels.values(), key=lambda l: l.total_volume, reverse=True)
        poc_level = sorted_levels[0]
        target_volume = self.total_volume * pct
        accumulated = poc_level.total_volume
        included_prices = [poc_level.price]

        for level in sorted_levels[1:]:
            if accumulated >= target_volume:
                break
            accumulated += level.total_volume
            included_prices.append(level.price)

        return ValueArea(
            high=max(included_prices),
            low=min(included_prices),
            poc=poc_level.price,
            volume_at_poc=poc_level.total_volume,
        )


# --- Signals ---

class OrderFlowSignal(BaseModel):
    type: SignalType
    strength: float = Field(ge=0, le=100)
    price: float
    timestamp: int
    description: str = ""


class TradeSignal(BaseModel):
    side: Side
    strategy: StrategyType
    entry_price: float
    stop_loss: float
    take_profit: float
    confluence_score: float
    signals: List[OrderFlowSignal] = Field(default_factory=list)
    timestamp: int = Field(default_factory=lambda: int(datetime.now().timestamp() * 1000))


# --- Positions & Trades ---

class Position(BaseModel):
    id: str = ""
    symbol: str
    side: Side
    entry_price: float
    size: float
    stop_loss: float
    take_profit: float
    leverage: int = 1
    strategy: StrategyType = StrategyType.REVERSAL
    status: PositionStatus = PositionStatus.OPEN
    opened_at: int = Field(default_factory=lambda: int(datetime.now().timestamp() * 1000))
    closed_at: Optional[int] = None
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    order_ids: List[str] = Field(default_factory=list)

    @property
    def notional_value(self) -> float:
        return self.entry_price * self.size

    def calculate_pnl(self, current_price: float) -> float:
        if self.side == Side.BUY:
            return (current_price - self.entry_price) * self.size
        return (self.entry_price - current_price) * self.size

    def calculate_pnl_pct(self, current_price: float) -> float:
        if self.entry_price == 0:
            return 0.0
        if self.side == Side.BUY:
            return ((current_price - self.entry_price) / self.entry_price) * 100 * self.leverage
        return ((self.entry_price - current_price) / self.entry_price) * 100 * self.leverage


class DailyStats(BaseModel):
    date: str
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0

    @property
    def is_loss_limit_hit(self) -> bool:
        return False  # checked by risk_manager with config
