import aiosqlite
import json
import logging
from datetime import datetime, date
from models import Position, PositionStatus, DailyStats, Side, StrategyType
from typing import Optional, List

logger = logging.getLogger("database")

import os
DB_PATH = os.environ.get("DB_PATH", "trading_bot.db")


class DatabaseManager:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.db: Optional[aiosqlite.Connection] = None

    async def initialize(self):
        self.db = await aiosqlite.connect(self.db_path)
        self.db.row_factory = aiosqlite.Row
        await self._create_tables()
        logger.info(f"Database initialized: {self.db_path}")

    async def _create_tables(self):
        await self.db.executescript("""
            CREATE TABLE IF NOT EXISTS positions (
                id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_price REAL NOT NULL,
                size REAL NOT NULL,
                stop_loss REAL NOT NULL,
                take_profit REAL NOT NULL,
                leverage INTEGER DEFAULT 1,
                strategy TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                opened_at INTEGER NOT NULL,
                closed_at INTEGER,
                exit_price REAL,
                pnl REAL,
                pnl_pct REAL,
                order_ids TEXT DEFAULT '[]',
                signals_json TEXT DEFAULT '[]'
            );

            CREATE TABLE IF NOT EXISTS signals_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                signal_type TEXT NOT NULL,
                strength REAL NOT NULL,
                price REAL NOT NULL,
                description TEXT,
                acted_on INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS daily_stats (
                date TEXT PRIMARY KEY,
                total_trades INTEGER DEFAULT 0,
                winning_trades INTEGER DEFAULT 0,
                losing_trades INTEGER DEFAULT 0,
                total_pnl REAL DEFAULT 0.0,
                max_drawdown REAL DEFAULT 0.0,
                win_rate REAL DEFAULT 0.0
            );

            CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
            CREATE INDEX IF NOT EXISTS idx_positions_opened ON positions(opened_at);
            CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals_log(timestamp);
        """)
        await self.db.commit()

    async def save_position(self, position: Position, signals_json: str = "[]"):
        await self.db.execute("""
            INSERT OR REPLACE INTO positions
            (id, symbol, side, entry_price, size, stop_loss, take_profit, leverage,
             strategy, status, opened_at, closed_at, exit_price, pnl, pnl_pct, order_ids, signals_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            position.id, position.symbol, position.side.value,
            position.entry_price, position.size, position.stop_loss,
            position.take_profit, position.leverage, position.strategy.value,
            position.status.value, position.opened_at, position.closed_at,
            position.exit_price, position.pnl, position.pnl_pct,
            json.dumps(position.order_ids), signals_json,
        ))
        await self.db.commit()

    async def get_open_positions(self) -> List[Position]:
        cursor = await self.db.execute(
            "SELECT * FROM positions WHERE status = ?", (PositionStatus.OPEN.value,)
        )
        rows = await cursor.fetchall()
        return [self._row_to_position(row) for row in rows]

    async def get_position(self, position_id: str) -> Optional[Position]:
        cursor = await self.db.execute("SELECT * FROM positions WHERE id = ?", (position_id,))
        row = await cursor.fetchone()
        return self._row_to_position(row) if row else None

    async def save_trade_context(self, position_id: str, context: dict):
        ctx_json = json.dumps(context, default=str)
        await self.db.execute(
            "UPDATE positions SET signals_json = ? WHERE id = ?",
            (ctx_json, position_id)
        )
        await self.db.commit()

    async def get_trade_context(self, position_id: str) -> dict:
        cursor = await self.db.execute(
            "SELECT signals_json FROM positions WHERE id = ?", (position_id,)
        )
        row = await cursor.fetchone()
        if row and row[0]:
            try:
                return json.loads(row[0])
            except json.JSONDecodeError:
                return {}
        return {}

    async def get_all_open_contexts(self) -> dict:
        cursor = await self.db.execute(
            "SELECT id, signals_json FROM positions WHERE status = ?",
            (PositionStatus.OPEN.value,)
        )
        rows = await cursor.fetchall()
        contexts = {}
        for row in rows:
            if row[1]:
                try:
                    contexts[row[0]] = json.loads(row[1])
                except json.JSONDecodeError:
                    pass
        return contexts

    async def close_position(self, position_id: str, exit_price: float, pnl: float, pnl_pct: float):
        closed_at = int(datetime.now().timestamp() * 1000)
        await self.db.execute("""
            UPDATE positions SET status = ?, closed_at = ?, exit_price = ?, pnl = ?, pnl_pct = ?
            WHERE id = ?
        """, (PositionStatus.CLOSED.value, closed_at, exit_price, pnl, pnl_pct, position_id))
        await self.db.commit()
        await self._update_daily_stats(pnl)

    async def get_recent_positions(self, limit: int = 50) -> List[Position]:
        cursor = await self.db.execute(
            "SELECT * FROM positions ORDER BY opened_at DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [self._row_to_position(row) for row in rows]

    async def log_signal(self, signal_type: str, strength: float, price: float,
                         timestamp: int, description: str = "", acted_on: bool = False):
        await self.db.execute("""
            INSERT INTO signals_log (timestamp, signal_type, strength, price, description, acted_on)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (timestamp, signal_type, strength, price, description, int(acted_on)))
        await self.db.commit()

    async def get_recent_signals(self, limit: int = 100) -> List[dict]:
        cursor = await self.db.execute(
            "SELECT * FROM signals_log ORDER BY timestamp DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_today_stats(self) -> DailyStats:
        today = date.today().isoformat()
        cursor = await self.db.execute("SELECT * FROM daily_stats WHERE date = ?", (today,))
        row = await cursor.fetchone()
        if row:
            return DailyStats(**dict(row))
        return DailyStats(date=today)

    async def _update_daily_stats(self, pnl: float):
        today = date.today().isoformat()
        stats = await self.get_today_stats()
        stats.total_trades += 1
        stats.total_pnl += pnl
        if pnl > 0:
            stats.winning_trades += 1
        else:
            stats.losing_trades += 1
        if stats.total_pnl < stats.max_drawdown:
            stats.max_drawdown = stats.total_pnl
        stats.win_rate = (stats.winning_trades / stats.total_trades * 100) if stats.total_trades > 0 else 0

        await self.db.execute("""
            INSERT OR REPLACE INTO daily_stats (date, total_trades, winning_trades, losing_trades,
                                                total_pnl, max_drawdown, win_rate)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (today, stats.total_trades, stats.winning_trades, stats.losing_trades,
              stats.total_pnl, stats.max_drawdown, stats.win_rate))
        await self.db.commit()

    async def get_all_daily_stats(self, limit: int = 30) -> List[DailyStats]:
        cursor = await self.db.execute(
            "SELECT * FROM daily_stats ORDER BY date DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [DailyStats(**dict(row)) for row in rows]

    def _row_to_position(self, row) -> Position:
        d = dict(row)
        d["side"] = Side(d["side"])
        d["strategy"] = StrategyType(d["strategy"])
        d["status"] = PositionStatus(d["status"])
        d["order_ids"] = json.loads(d["order_ids"])
        d.pop("signals_json", None)
        return Position(**d)

    async def close(self):
        if self.db:
            await self.db.close()
            logger.info("Database connection closed")
