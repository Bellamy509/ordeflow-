import logging
import asyncio
from datetime import datetime, time as dtime
from typing import Optional, Callable
from database import DatabaseManager

logger = logging.getLogger("report")


class DailyReporter:
    """Generates and sends daily performance reports."""

    def __init__(self, database: DatabaseManager, report_hour: int = 0):
        self.db = database
        self.report_hour = report_hour
        self._running = False
        self._last_report_date: Optional[str] = None
        self._notify_callback: Optional[Callable] = None

    def set_notify_callback(self, callback: Callable):
        self._notify_callback = callback

    async def start(self):
        self._running = True
        logger.info(f"Daily reporter started (sends at {self.report_hour:02d}:00 UTC)")
        while self._running:
            await asyncio.sleep(60)
            now = datetime.utcnow()
            today = now.strftime("%Y-%m-%d")
            if now.hour == self.report_hour and self._last_report_date != today:
                self._last_report_date = today
                report = await self.generate_report()
                logger.info(f"Daily report generated:\n{report}")
                if self._notify_callback:
                    await self._notify_callback(report)

    async def generate_report(self) -> str:
        stats_list = await self.db.get_all_daily_stats(7)
        today_stats = await self.db.get_today_stats()
        recent = await self.db.get_recent_positions(20)

        closed_today = [p for p in recent if p.status.value == "closed"]
        winners = [p for p in closed_today if p.pnl and p.pnl > 0]
        losers = [p for p in closed_today if p.pnl and p.pnl <= 0]

        best_trade = max(closed_today, key=lambda p: p.pnl or 0) if closed_today else None
        worst_trade = min(closed_today, key=lambda p: p.pnl or 0) if closed_today else None

        lines = [
            f"Date: {datetime.utcnow().strftime('%Y-%m-%d')}",
            f"Trades: {today_stats.total_trades} ({today_stats.winning_trades} gagnants / {today_stats.losing_trades} perdants)",
            f"Taux de reussite: {today_stats.win_rate:.1f}%",
            f"PnL: ${today_stats.total_pnl:+.2f}",
            f"Drawdown max: ${today_stats.max_drawdown:.2f}",
        ]

        if best_trade and best_trade.pnl:
            side_fr = "ACHAT" if best_trade.side.value == "buy" else "VENTE"
            lines.append(f"Meilleur trade: {best_trade.symbol} {side_fr} ${best_trade.pnl:+.2f}")
        if worst_trade and worst_trade.pnl:
            side_fr = "ACHAT" if worst_trade.side.value == "buy" else "VENTE"
            lines.append(f"Pire trade: {worst_trade.symbol} {side_fr} ${worst_trade.pnl:+.2f}")

        if len(stats_list) > 1:
            week_pnl = sum(s.total_pnl for s in stats_list)
            week_trades = sum(s.total_trades for s in stats_list)
            lines.append(f"\n7 derniers jours: ${week_pnl:+.2f} sur {week_trades} trades")

        return "\n".join(lines)

    async def stop(self):
        self._running = False
