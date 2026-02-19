import logging
import asyncio
import time
import os
from typing import Optional, Callable, Awaitable

logger = logging.getLogger("health")


class HealthMonitor:
    """Monitors system health: WebSocket, memory, latency, errors."""

    def __init__(self):
        self._running = False
        self._last_tick_time: float = 0
        self._error_count: int = 0
        self._ws_disconnects: int = 0
        self._notify_callback: Optional[Callable] = None
        self.status = "starting"
        self._alerts_sent: set = set()

    def set_notify_callback(self, callback: Callable):
        self._notify_callback = callback

    def record_tick(self):
        self._last_tick_time = time.time()

    def record_error(self, component: str, error: str):
        self._error_count += 1
        logger.warning(f"[HEALTH] Error in {component}: {error}")

    def record_ws_disconnect(self):
        self._ws_disconnects += 1

    async def start(self):
        self._running = True
        self.status = "healthy"
        logger.info("Health monitor started")

        while self._running:
            await asyncio.sleep(15)
            await self._check_health()

    async def _check_health(self):
        now = time.time()
        issues = []

        if self._last_tick_time > 0:
            tick_age = now - self._last_tick_time
            if tick_age > 30:
                issues.append(f"No ticks for {tick_age:.0f}s — WebSocket may be down")
            if tick_age > 120:
                issues.append(f"CRITICAL: No data for {tick_age:.0f}s")

        try:
            import resource
            mem_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)
            if mem_mb > 500:
                issues.append(f"High memory usage: {mem_mb:.0f}MB")
        except Exception:
            pass

        if self._error_count > 20:
            issues.append(f"High error count: {self._error_count}")

        if issues:
            self.status = "degraded"
            for issue in issues:
                alert_key = issue[:30]
                if alert_key not in self._alerts_sent:
                    self._alerts_sent.add(alert_key)
                    logger.warning(f"[HEALTH] {issue}")
                    if self._notify_callback:
                        await self._notify_callback(issue)
        else:
            self.status = "healthy"
            self._alerts_sent.clear()

    def get_status(self) -> dict:
        tick_age = time.time() - self._last_tick_time if self._last_tick_time > 0 else -1
        return {
            "status": self.status,
            "last_tick_age_s": round(tick_age, 1),
            "error_count": self._error_count,
            "ws_disconnects": self._ws_disconnects,
        }

    async def stop(self):
        self._running = False
