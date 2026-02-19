import logging
import math
from typing import List
from collections import deque

logger = logging.getLogger("sizing")


class DynamicSizer:
    """
    Adjusts position size using a modified Kelly Criterion
    based on recent trading performance.

    Kelly fraction = (win_rate * avg_win/avg_loss - (1-win_rate)) / (avg_win/avg_loss)
    Capped at half-Kelly for safety.
    """

    def __init__(self, base_risk_pct: float = 1.0, min_trades: int = 10,
                 min_multiplier: float = 0.3, max_multiplier: float = 2.0):
        self.base_risk_pct = base_risk_pct
        self.min_trades = min_trades
        self.min_mult = min_multiplier
        self.max_mult = max_multiplier
        self._results: deque = deque(maxlen=100)

    def record_trade(self, pnl: float):
        self._results.append(pnl)

    def get_risk_multiplier(self) -> dict:
        """Returns a multiplier to apply to the base risk percentage."""
        if len(self._results) < self.min_trades:
            return {"multiplier": 1.0, "reason": f"Not enough trades ({len(self._results)}/{self.min_trades})",
                    "kelly": 0, "win_rate": 0, "profit_factor": 0}

        results = list(self._results)
        wins = [r for r in results if r > 0]
        losses = [r for r in results if r <= 0]

        win_rate = len(wins) / len(results) if results else 0
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = abs(sum(losses) / len(losses)) if losses else 1

        if avg_loss == 0:
            avg_loss = 0.01

        win_loss_ratio = avg_win / avg_loss
        profit_factor = sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else 99

        kelly = 0
        if win_loss_ratio > 0:
            kelly = (win_rate * win_loss_ratio - (1 - win_rate)) / win_loss_ratio

        half_kelly = kelly / 2

        if half_kelly <= 0:
            multiplier = self.min_mult
            reason = f"Negative Kelly ({kelly:.2f}) — reducing size"
        elif half_kelly > 1:
            multiplier = min(half_kelly, self.max_mult)
            reason = f"Strong edge (Kelly={kelly:.2f}) — increasing size"
        else:
            multiplier = max(half_kelly, self.min_mult)
            reason = f"Moderate edge (Kelly={kelly:.2f})"

        multiplier = max(self.min_mult, min(self.max_mult, multiplier))

        # Streak adjustment
        recent = results[-5:]
        streak_losses = sum(1 for r in recent if r <= 0)
        if streak_losses >= 3:
            multiplier *= 0.5
            reason += f" — losing streak ({streak_losses}/5), halved"

        multiplier = max(self.min_mult, min(self.max_mult, multiplier))

        return {
            "multiplier": round(multiplier, 3),
            "reason": reason,
            "kelly": round(kelly, 3),
            "half_kelly": round(half_kelly, 3),
            "win_rate": round(win_rate * 100, 1),
            "profit_factor": round(profit_factor, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
        }

    def get_adjusted_risk_pct(self) -> float:
        data = self.get_risk_multiplier()
        return self.base_risk_pct * data["multiplier"]
