import logging
from typing import List
from models import TradeSignal, Position, Side
from database import DatabaseManager
from config import Config

logger = logging.getLogger("risk")


class RiskManager:
    """
    Validates every trade against risk rules before execution.
    This is the last gate — no trade passes without approval.
    """

    def __init__(self, config: Config, database: DatabaseManager):
        self.config = config
        self.db = database
        self.account_balance: float = 0.0

    async def update_balance(self, balance: float):
        self.account_balance = balance
        logger.debug(f"Account balance updated: ${balance:.2f}")

    async def validate_trade(self, signal: TradeSignal, open_positions: List[Position],
                              symbol: str = "") -> dict:
        """
        Returns {"approved": True/False, "size": float, "reason": str}
        """
        checks = [
            self._check_daily_loss_limit(),
            self._check_max_positions(open_positions),
            self._check_same_direction(signal, open_positions, symbol),
            self._check_balance(),
        ]

        for check_coro in checks:
            result = await check_coro
            if not result["approved"]:
                logger.warning(f"TRADE REJECTED: {result['reason']}")
                return result

        size = self._calculate_position_size(signal, symbol)
        if size <= 0:
            return {"approved": False, "size": 0, "reason": "Calculated position size is zero or negative"}

        logger.info(
            f"TRADE APPROVED | {signal.side.value.upper()} {symbol} | "
            f"size={size:.6f} | risk=${self._risk_amount():.2f}"
        )
        return {"approved": True, "size": size, "reason": "All checks passed"}

    async def _check_daily_loss_limit(self) -> dict:
        stats = await self.db.get_today_stats()
        max_loss = self.account_balance * (self.config.max_daily_loss_pct / 100)

        if abs(stats.total_pnl) >= max_loss and stats.total_pnl < 0:
            return {
                "approved": False, "size": 0,
                "reason": f"Daily loss limit hit: ${stats.total_pnl:.2f} / -${max_loss:.2f}",
            }
        return {"approved": True, "size": 0, "reason": ""}

    async def _check_max_positions(self, open_positions: List[Position]) -> dict:
        if len(open_positions) >= self.config.max_open_positions:
            return {
                "approved": False, "size": 0,
                "reason": f"Max open positions reached: {len(open_positions)}/{self.config.max_open_positions}",
            }
        return {"approved": True, "size": 0, "reason": ""}

    async def _check_same_direction(self, signal: TradeSignal, open_positions: List[Position],
                                      symbol: str = "") -> dict:
        target = symbol or (self.config.symbol_list[0] if self.config.symbol_list else "")
        for pos in open_positions:
            if pos.symbol == target and pos.side == signal.side:
                return {
                    "approved": False, "size": 0,
                    "reason": f"Already have an open {signal.side.value} position on {target}",
                }
        return {"approved": True, "size": 0, "reason": ""}

    async def _check_balance(self) -> dict:
        if self.account_balance <= 0:
            return {"approved": False, "size": 0, "reason": "Account balance is zero or unavailable"}
        min_balance = 10.0
        if self.account_balance < min_balance:
            return {
                "approved": False, "size": 0,
                "reason": f"Balance too low: ${self.account_balance:.2f} < ${min_balance}",
            }
        return {"approved": True, "size": 0, "reason": ""}

    def _risk_amount(self) -> float:
        return self.account_balance * (self.config.risk_per_trade_pct / 100)

    def _calculate_position_size(self, signal: TradeSignal, symbol: str = "") -> float:
        risk_usd = self._risk_amount()

        if signal.side == Side.BUY:
            sl_distance = signal.entry_price - signal.stop_loss
        else:
            sl_distance = signal.stop_loss - signal.entry_price

        if sl_distance <= 0:
            logger.error(f"Invalid SL distance: {sl_distance}")
            return 0.0

        size = risk_usd / sl_distance

        max_notional = self.config.max_position_size_usd * self.config.default_leverage
        max_size = max_notional / signal.entry_price
        size = min(size, max_size)

        sym_cfg = self.config.get_symbol_config(symbol)
        min_size = sym_cfg.get("min_size", 0.001)
        if size < min_size:
            logger.warning(f"Position size {size} below minimum {min_size}")
            return 0.0

        return round(size, 6)

    async def should_close_position(self, position: Position, current_price: float) -> dict:
        """Check if a position should be closed (SL/TP hit)."""
        if position.side == Side.BUY:
            if current_price <= position.stop_loss:
                return {"close": True, "reason": "Stop-loss hit"}
            if current_price >= position.take_profit:
                return {"close": True, "reason": "Take-profit hit"}
        else:
            if current_price >= position.stop_loss:
                return {"close": True, "reason": "Stop-loss hit"}
            if current_price <= position.take_profit:
                return {"close": True, "reason": "Take-profit hit"}

        return {"close": False, "reason": ""}
