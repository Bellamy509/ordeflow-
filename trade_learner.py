import logging
import json
from typing import Dict, List, Tuple
from collections import defaultdict
from database import DatabaseManager

logger = logging.getLogger("learner")

MIN_SAMPLES = 5


class TradeLearner:
    """
    Learns from past trades to dynamically adjust signal quality scores.
    Tracks win rate per signal combination, per market, per strategy,
    and produces bias adjustments that improve over time.
    """

    def __init__(self, database: DatabaseManager):
        self.db = database
        self._combo_stats: Dict[str, dict] = {}
        self._symbol_stats: Dict[str, dict] = {}
        self._strategy_stats: Dict[str, dict] = {}
        self._loaded = False

    async def load_history(self):
        """Load closed trades from DB and build statistics."""
        positions = await self.db.get_recent_positions(200)
        closed = [p for p in positions if p.status.value == "closed" and p.pnl is not None]

        self._combo_stats.clear()
        self._symbol_stats.clear()
        self._strategy_stats.clear()

        for pos in closed:
            won = pos.pnl > 0
            pnl = pos.pnl

            sym = pos.symbol
            strat = pos.strategy.value

            self._update_stat(self._symbol_stats, sym, won, pnl)
            self._update_stat(self._strategy_stats, strat, won, pnl)

        self._loaded = True
        logger.info(
            f"Trade learner loaded: {len(closed)} trades | "
            f"{len(self._symbol_stats)} symbols | {len(self._strategy_stats)} strategies"
        )

    def record_trade(self, symbol: str, strategy: str, signal_types: List[str],
                     won: bool, pnl: float):
        """Record a completed trade for learning."""
        self._update_stat(self._symbol_stats, symbol, won, pnl)
        self._update_stat(self._strategy_stats, strategy, won, pnl)

        combo_key = "+".join(sorted(set(signal_types)))
        self._update_stat(self._combo_stats, combo_key, won, pnl)

        combo_sym_key = f"{symbol}|{combo_key}"
        self._update_stat(self._combo_stats, combo_sym_key, won, pnl)

    def _update_stat(self, store: dict, key: str, won: bool, pnl: float):
        if key not in store:
            store[key] = {"wins": 0, "losses": 0, "total_pnl": 0.0, "trades": 0}
        s = store[key]
        s["trades"] += 1
        s["total_pnl"] += pnl
        if won:
            s["wins"] += 1
        else:
            s["losses"] += 1

    def get_signal_combo_bias(self, symbol: str, signal_types: List[str]) -> dict:
        """
        Returns a score adjustment based on historical performance
        of this signal combination.
        """
        combo_key = "+".join(sorted(set(signal_types)))

        combo_sym_key = f"{symbol}|{combo_key}"
        stat = self._combo_stats.get(combo_sym_key)
        if not stat or stat["trades"] < MIN_SAMPLES:
            stat = self._combo_stats.get(combo_key)

        if not stat or stat["trades"] < MIN_SAMPLES:
            return {"bias": 0, "reason": "Not enough data", "win_rate": 0, "trades": 0}

        win_rate = stat["wins"] / stat["trades"]
        avg_pnl = stat["total_pnl"] / stat["trades"]

        if win_rate >= 0.65:
            bias = 10
            reason = f"Strong combo: {win_rate*100:.0f}% WR over {stat['trades']} trades"
        elif win_rate >= 0.55:
            bias = 5
            reason = f"Good combo: {win_rate*100:.0f}% WR over {stat['trades']} trades"
        elif win_rate <= 0.35:
            bias = -15
            reason = f"Weak combo: {win_rate*100:.0f}% WR over {stat['trades']} trades"
        elif win_rate <= 0.45:
            bias = -8
            reason = f"Below average: {win_rate*100:.0f}% WR over {stat['trades']} trades"
        else:
            bias = 0
            reason = f"Neutral: {win_rate*100:.0f}% WR over {stat['trades']} trades"

        return {"bias": bias, "reason": reason,
                "win_rate": round(win_rate * 100, 1), "trades": stat["trades"]}

    def get_strategy_bias(self, strategy: str) -> int:
        stat = self._strategy_stats.get(strategy)
        if not stat or stat["trades"] < MIN_SAMPLES:
            return 0

        win_rate = stat["wins"] / stat["trades"]
        if win_rate >= 0.60:
            return 5
        elif win_rate <= 0.35:
            return -10
        return 0

    def get_symbol_bias(self, symbol: str) -> int:
        stat = self._symbol_stats.get(symbol)
        if not stat or stat["trades"] < MIN_SAMPLES:
            return 0

        win_rate = stat["wins"] / stat["trades"]
        avg_pnl = stat["total_pnl"] / stat["trades"]

        if win_rate <= 0.35 and avg_pnl < 0:
            return -12
        elif win_rate >= 0.60:
            return 5
        return 0

    def should_skip_symbol(self, symbol: str) -> dict:
        """If a symbol consistently loses, recommend skipping it."""
        stat = self._symbol_stats.get(symbol)
        if not stat or stat["trades"] < 10:
            return {"skip": False, "reason": ""}

        win_rate = stat["wins"] / stat["trades"]
        if win_rate <= 0.30 and stat["total_pnl"] < -50:
            return {
                "skip": True,
                "reason": f"{symbol} has {win_rate*100:.0f}% WR and ${stat['total_pnl']:.2f} PnL over {stat['trades']} trades"
            }
        return {"skip": False, "reason": ""}

    def get_summary(self) -> dict:
        summary = {"combos": {}, "symbols": {}, "strategies": {}}

        for key, stat in self._combo_stats.items():
            if stat["trades"] >= MIN_SAMPLES and "|" not in key:
                wr = stat["wins"] / stat["trades"] * 100
                summary["combos"][key] = {
                    "win_rate": round(wr, 1),
                    "trades": stat["trades"],
                    "pnl": round(stat["total_pnl"], 2),
                }

        for key, stat in self._symbol_stats.items():
            wr = stat["wins"] / stat["trades"] * 100 if stat["trades"] > 0 else 0
            summary["symbols"][key] = {
                "win_rate": round(wr, 1),
                "trades": stat["trades"],
                "pnl": round(stat["total_pnl"], 2),
            }

        for key, stat in self._strategy_stats.items():
            wr = stat["wins"] / stat["trades"] * 100 if stat["trades"] > 0 else 0
            summary["strategies"][key] = {
                "win_rate": round(wr, 1),
                "trades": stat["trades"],
                "pnl": round(stat["total_pnl"], 2),
            }

        return summary
