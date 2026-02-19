import logging
import json
import asyncio
from typing import List, Dict
from datetime import datetime
from models import RawTick, FootprintCandle, TradeSignal, Side
from footprint_engine import FootprintEngine
from signal_detector import SignalDetector
from strategy_engine import StrategyEngine
from config import Config

logger = logging.getLogger("backtest")


class BacktestResult:
    def __init__(self):
        self.trades: List[dict] = []
        self.total_pnl: float = 0
        self.winning: int = 0
        self.losing: int = 0
        self.max_drawdown: float = 0
        self.peak_balance: float = 0
        self.balance_curve: List[float] = []

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def win_rate(self) -> float:
        return (self.winning / self.total_trades * 100) if self.total_trades > 0 else 0

    @property
    def avg_win(self) -> float:
        wins = [t["pnl"] for t in self.trades if t["pnl"] > 0]
        return sum(wins) / len(wins) if wins else 0

    @property
    def avg_loss(self) -> float:
        losses = [t["pnl"] for t in self.trades if t["pnl"] <= 0]
        return sum(losses) / len(losses) if losses else 0

    @property
    def profit_factor(self) -> float:
        gross_profit = sum(t["pnl"] for t in self.trades if t["pnl"] > 0)
        gross_loss = abs(sum(t["pnl"] for t in self.trades if t["pnl"] < 0))
        return gross_profit / gross_loss if gross_loss > 0 else float("inf")

    def summary(self) -> str:
        return (
            f"{'='*50}\n"
            f"  BACKTEST RESULTS\n"
            f"{'='*50}\n"
            f"  Total trades:   {self.total_trades}\n"
            f"  Win rate:       {self.win_rate:.1f}%\n"
            f"  Total PnL:      ${self.total_pnl:+.2f}\n"
            f"  Avg win:        ${self.avg_win:+.2f}\n"
            f"  Avg loss:       ${self.avg_loss:+.2f}\n"
            f"  Profit factor:  {self.profit_factor:.2f}\n"
            f"  Max drawdown:   ${self.max_drawdown:.2f}\n"
            f"{'='*50}"
        )


class Backtester:
    """
    Replays historical tick data through the full pipeline
    (footprint → signals → strategy) to evaluate performance.
    """

    def __init__(self, config: Config, symbol: str):
        self.config = config
        self.symbol = symbol
        self.fp = FootprintEngine(config, symbol)
        self.signals = SignalDetector(self.fp)
        self.strategy = StrategyEngine(config, self.fp)

    def run(self, ticks: List[RawTick], initial_balance: float = 10000,
            risk_pct: float = 1.0) -> BacktestResult:
        """Run backtest on a list of ticks."""
        result = BacktestResult()
        balance = initial_balance
        result.peak_balance = balance
        position = None

        logger.info(f"Starting backtest: {len(ticks)} ticks, ${initial_balance} initial balance")

        for tick in ticks:
            completed = self.fp.process_tick(tick)

            if position:
                if position["side"] == "buy":
                    if tick.price <= position["sl"]:
                        pnl = (tick.price - position["entry"]) * position["size"]
                        balance += pnl
                        result.trades.append({**position, "exit": tick.price, "pnl": pnl, "reason": "SL"})
                        if pnl > 0: result.winning += 1
                        else: result.losing += 1
                        result.total_pnl += pnl
                        position = None
                    elif tick.price >= position["tp"]:
                        pnl = (tick.price - position["entry"]) * position["size"]
                        balance += pnl
                        result.trades.append({**position, "exit": tick.price, "pnl": pnl, "reason": "TP"})
                        result.winning += 1
                        result.total_pnl += pnl
                        position = None
                else:
                    if tick.price >= position["sl"]:
                        pnl = (position["entry"] - tick.price) * position["size"]
                        balance += pnl
                        result.trades.append({**position, "exit": tick.price, "pnl": pnl, "reason": "SL"})
                        if pnl > 0: result.winning += 1
                        else: result.losing += 1
                        result.total_pnl += pnl
                        position = None
                    elif tick.price <= position["tp"]:
                        pnl = (position["entry"] - tick.price) * position["size"]
                        balance += pnl
                        result.trades.append({**position, "exit": tick.price, "pnl": pnl, "reason": "TP"})
                        result.winning += 1
                        result.total_pnl += pnl
                        position = None

            if completed and position is None:
                detected = self.signals.analyze(completed)
                trade = self.strategy.evaluate(completed, detected)

                if trade:
                    sl_dist = abs(trade.entry_price - trade.stop_loss)
                    if sl_dist > 0:
                        risk_usd = balance * (risk_pct / 100)
                        size = risk_usd / sl_dist
                        position = {
                            "side": trade.side.value,
                            "entry": trade.entry_price,
                            "sl": trade.stop_loss,
                            "tp": trade.take_profit,
                            "size": size,
                            "strategy": trade.strategy.value,
                            "score": trade.confluence_score,
                        }

            result.balance_curve.append(balance)
            if balance > result.peak_balance:
                result.peak_balance = balance
            dd = result.peak_balance - balance
            if dd > result.max_drawdown:
                result.max_drawdown = dd

        if position:
            last_price = ticks[-1].price if ticks else position["entry"]
            if position["side"] == "buy":
                pnl = (last_price - position["entry"]) * position["size"]
            else:
                pnl = (position["entry"] - last_price) * position["size"]
            result.trades.append({**position, "exit": last_price, "pnl": pnl, "reason": "END"})
            result.total_pnl += pnl

        logger.info(result.summary())
        return result

    @staticmethod
    def load_ticks_from_file(filepath: str) -> List[RawTick]:
        ticks = []
        with open(filepath, "r") as f:
            for line in f:
                data = json.loads(line)
                ticks.append(RawTick(
                    timestamp=int(data.get("T", data.get("timestamp", 0))),
                    price=float(data.get("p", data.get("price", 0))),
                    quantity=float(data.get("q", data.get("quantity", 0))),
                    is_buyer_maker=data.get("m", data.get("is_buyer_maker", False)),
                ))
        return ticks
