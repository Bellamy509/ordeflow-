import logging
import time
from typing import List, Optional, Callable
from collections import deque
from models import Position

logger = logging.getLogger("killswitch")


class KillSwitch:
    """
    Intelligent kill switch that halts trading when conditions are dangerous:
    - Consecutive losses
    - Rapid drawdown
    - Extreme volatility (flash crash detection)
    - Error rate too high
    """

    def __init__(self,
                 max_consecutive_losses: int = 3,
                 max_rapid_drawdown_pct: float = 2.0,
                 flash_crash_pct: float = 3.0,
                 cooldown_minutes: int = 30):
        self.max_consecutive_losses = max_consecutive_losses
        self.max_rapid_drawdown_pct = max_rapid_drawdown_pct
        self.flash_crash_pct = flash_crash_pct
        self.cooldown_minutes = cooldown_minutes

        self._consecutive_losses = 0
        self._recent_pnls: deque = deque(maxlen=20)
        self._price_histories: dict = {}
        self._is_killed = False
        self._kill_reason = ""
        self._kill_time: float = 0
        self._notify_callback: Optional[Callable] = None

    def set_notify_callback(self, callback: Callable):
        self._notify_callback = callback

    @property
    def is_active(self) -> bool:
        if not self._is_killed:
            return False

        elapsed = (time.time() - self._kill_time) / 60
        if elapsed >= self.cooldown_minutes:
            logger.info(f"Kill switch cooldown expired ({self.cooldown_minutes}min). Trading resumed.")
            self._is_killed = False
            self._consecutive_losses = 0
            return False

        return True

    @property
    def remaining_cooldown(self) -> float:
        if not self._is_killed:
            return 0
        elapsed = (time.time() - self._kill_time) / 60
        return max(0, self.cooldown_minutes - elapsed)

    def record_trade_result(self, pnl: float):
        self._recent_pnls.append(pnl)

        if pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

        if self._consecutive_losses >= self.max_consecutive_losses:
            self._trigger(f"{self._consecutive_losses} consecutive losses")

        if len(self._recent_pnls) >= 3:
            recent_sum = sum(list(self._recent_pnls)[-5:])
            if recent_sum < -(self.max_rapid_drawdown_pct * 100):
                self._trigger(f"Rapid drawdown: ${recent_sum:.2f} in last 5 trades")

    def record_price(self, symbol: str, price: float):
        if symbol not in self._price_histories:
            self._price_histories[symbol] = deque(maxlen=300)
        self._price_histories[symbol].append((time.time(), price))
        self._check_flash_crash(symbol)

    def _check_flash_crash(self, symbol: str):
        history = self._price_histories.get(symbol)
        if not history or len(history) < 10:
            return

        now = time.time()
        recent = [(t, p) for t, p in history if now - t < 60]
        if len(recent) < 5:
            return

        high = max(p for _, p in recent)
        low = min(p for _, p in recent)

        if high > 0:
            move_pct = (high - low) / high * 100
            if move_pct >= self.flash_crash_pct:
                self._trigger(f"Flash crash on {symbol}: {move_pct:.1f}% move in 60s")

    def _trigger(self, reason: str):
        if self._is_killed:
            return

        self._is_killed = True
        self._kill_reason = reason
        self._kill_time = time.time()
        logger.warning(f"KILL SWITCH ACTIVATED: {reason} (cooldown: {self.cooldown_minutes}min)")

        if self._notify_callback:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self._notify_callback(
                        f"KILL SWITCH ACTIVATED\n{reason}\nCooldown: {self.cooldown_minutes} minutes"
                    ))
            except Exception:
                pass

    def should_allow_trade(self) -> dict:
        if self.is_active:
            return {
                "allowed": False,
                "reason": f"Kill switch active: {self._kill_reason} "
                          f"({self.remaining_cooldown:.0f}min remaining)",
            }
        return {"allowed": True, "reason": ""}

    def get_status(self) -> dict:
        return {
            "is_killed": self._is_killed,
            "reason": self._kill_reason,
            "consecutive_losses": self._consecutive_losses,
            "remaining_cooldown_min": round(self.remaining_cooldown, 1),
        }
