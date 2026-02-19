import ccxt
import uuid
import logging
from typing import Optional, List
from datetime import datetime
from models import TradeSignal, Position, Side, PositionStatus
from database import DatabaseManager
from config import Config

logger = logging.getLogger("execution")


class ExecutionEngine:
    """
    Handles real order execution on Binance Futures via CCXT.
    Supports both paper trading (simulated) and live trading.
    """

    def __init__(self, config: Config, database: DatabaseManager):
        self.config = config
        self.db = database
        self.exchange: Optional[ccxt.binance] = None
        self._paper_balance = 10000.0
        self._paper_positions: List[Position] = []

    async def initialize(self):
        if self.config.is_paper:
            logger.info("=== PAPER TRADING MODE — no real orders will be placed ===")
            return

        self.exchange = ccxt.binance({
            "apiKey": self.config.exchange_api_key,
            "secret": self.config.exchange_api_secret,
            "options": {"defaultType": "future"},
            "enableRateLimit": True,
        })

        if self.config.exchange_testnet:
            self.exchange.set_sandbox_mode(True)
            logger.info("=== TESTNET MODE — using Binance Futures testnet ===")
        else:
            logger.warning("=== LIVE MODE — REAL MONEY — BE CAREFUL ===")

        try:
            await self._set_leverage()
            balance = await self.get_balance()
            logger.info(f"Exchange connected | Balance: ${balance:.2f} USDT")
        except Exception as e:
            logger.error(f"Exchange connection failed: {e}")
            raise

    async def _set_leverage(self):
        if not self.exchange:
            return
        for symbol in self.config.symbol_list:
            try:
                self.exchange.set_leverage(self.config.default_leverage, symbol)
                logger.info(f"Leverage set to {self.config.default_leverage}x for {symbol}")
            except Exception as e:
                logger.warning(f"Could not set leverage for {symbol}: {e}")

    async def get_balance(self) -> float:
        if self.config.is_paper:
            return self._paper_balance

        try:
            balance = self.exchange.fetch_balance()
            usdt_free = balance.get("USDT", {}).get("free", 0)
            return float(usdt_free)
        except Exception as e:
            logger.error(f"Failed to fetch balance: {e}")
            return 0.0

    async def get_current_price(self) -> float:
        if not self.exchange:
            return 0.0
        try:
            ticker = self.exchange.fetch_ticker(self.config.symbol)
            return float(ticker["last"])
        except Exception as e:
            logger.error(f"Failed to fetch price: {e}")
            return 0.0

    async def open_position(self, signal: TradeSignal, size: float,
                            symbol: str = "") -> Optional[Position]:
        target_symbol = symbol or (self.config.symbol_list[0] if self.config.symbol_list else "")
        position_id = str(uuid.uuid4())[:12]
        position = Position(
            id=position_id,
            symbol=target_symbol,
            side=signal.side,
            entry_price=signal.entry_price,
            size=size,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            leverage=self.config.default_leverage,
            strategy=signal.strategy,
        )

        if self.config.is_paper:
            return await self._paper_open(position)

        return await self._live_open(position, signal)

    async def _paper_open(self, position: Position) -> Position:
        notional = position.entry_price * position.size
        margin_required = notional / position.leverage
        self._paper_balance -= margin_required
        self._paper_positions.append(position)

        await self.db.save_position(position)
        logger.info(
            f"[PAPER] OPENED {position.side.value.upper()} | "
            f"size={position.size:.6f} @ {position.entry_price:.2f} | "
            f"SL={position.stop_loss:.2f} TP={position.take_profit:.2f} | "
            f"margin=${margin_required:.2f}"
        )
        return position

    async def _live_open(self, position: Position, signal: TradeSignal) -> Optional[Position]:
        try:
            ccxt_side = "buy" if signal.side == Side.BUY else "sell"

            order = self.exchange.create_order(
                symbol=position.symbol,
                type="market",
                side=ccxt_side,
                amount=position.size,
            )

            position.order_ids.append(order["id"])
            fill_price = float(order.get("average", order.get("price", signal.entry_price)))
            position.entry_price = fill_price

            await self._place_sl_tp(position)
            await self.db.save_position(position)

            logger.info(
                f"[LIVE] OPENED {position.side.value.upper()} | "
                f"order_id={order['id']} | fill={fill_price:.2f} | "
                f"size={position.size:.6f}"
            )
            return position

        except Exception as e:
            logger.error(f"[LIVE] Failed to open position: {e}")
            return None

    async def _place_sl_tp(self, position: Position):
        """Place stop-loss and take-profit orders on the exchange."""
        if not self.exchange:
            return

        close_side = "sell" if position.side == Side.BUY else "buy"

        try:
            sl_order = self.exchange.create_order(
                symbol=position.symbol,
                type="stop_market",
                side=close_side,
                amount=position.size,
                params={"stopPrice": position.stop_loss, "closePosition": False},
            )
            position.order_ids.append(sl_order["id"])
            logger.info(f"[{position.symbol}] SL order placed: {sl_order['id']} @ {position.stop_loss}")
        except Exception as e:
            logger.error(f"[{position.symbol}] Failed to place SL: {e}")

        try:
            tp_order = self.exchange.create_order(
                symbol=position.symbol,
                type="take_profit_market",
                side=close_side,
                amount=position.size,
                params={"stopPrice": position.take_profit, "closePosition": False},
            )
            position.order_ids.append(tp_order["id"])
            logger.info(f"[{position.symbol}] TP order placed: {tp_order['id']} @ {position.take_profit}")
        except Exception as e:
            logger.error(f"[{position.symbol}] Failed to place TP: {e}")

    async def close_position(self, position: Position, current_price: float, reason: str) -> bool:
        if self.config.is_paper:
            return await self._paper_close(position, current_price, reason)
        return await self._live_close(position, current_price, reason)

    async def _paper_close(self, position: Position, current_price: float, reason: str) -> bool:
        pnl = position.calculate_pnl(current_price)
        pnl_pct = position.calculate_pnl_pct(current_price)

        notional = position.entry_price * position.size
        margin_returned = notional / position.leverage
        self._paper_balance += margin_returned + pnl

        self._paper_positions = [p for p in self._paper_positions if p.id != position.id]

        await self.db.close_position(position.id, current_price, pnl, pnl_pct)
        logger.info(
            f"[PAPER] CLOSED {position.side.value.upper()} | "
            f"reason={reason} | exit={current_price:.2f} | "
            f"PnL=${pnl:+.2f} ({pnl_pct:+.2f}%) | balance=${self._paper_balance:.2f}"
        )
        return True

    async def _live_close(self, position: Position, current_price: float, reason: str) -> bool:
        try:
            close_side = "sell" if position.side == Side.BUY else "buy"

            order = self.exchange.create_order(
                symbol=position.symbol,
                type="market",
                side=close_side,
                amount=position.size,
            )

            fill_price = float(order.get("average", order.get("price", current_price)))
            pnl = position.calculate_pnl(fill_price)
            pnl_pct = position.calculate_pnl_pct(fill_price)

            await self._cancel_position_orders(position)
            await self.db.close_position(position.id, fill_price, pnl, pnl_pct)

            logger.info(
                f"[LIVE] CLOSED {position.side.value.upper()} | "
                f"reason={reason} | fill={fill_price:.2f} | "
                f"PnL=${pnl:+.2f} ({pnl_pct:+.2f}%)"
            )
            return True

        except Exception as e:
            logger.error(f"[LIVE] Failed to close position: {e}")
            return False

    async def _cancel_position_orders(self, position: Position):
        """Cancel SL/TP orders after manually closing."""
        if not self.exchange:
            return
        for order_id in position.order_ids:
            try:
                self.exchange.cancel_order(order_id, position.symbol)
            except Exception:
                pass

    async def emergency_close_all(self, current_market_price: float = 0):
        """Close all open positions immediately."""
        logger.warning("!!! EMERGENCY CLOSE ALL POSITIONS !!!")
        positions = await self.db.get_open_positions()
        for pos in positions:
            if not self.config.is_paper:
                price = await self.get_current_price()
            elif current_market_price > 0:
                price = current_market_price
            else:
                price = pos.entry_price
            await self.close_position(pos, price, "EMERGENCY_CLOSE")

    async def sync_positions(self) -> List[Position]:
        """Get currently open positions from DB (and exchange for live)."""
        return await self.db.get_open_positions()
