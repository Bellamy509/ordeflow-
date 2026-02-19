import logging
from datetime import datetime, timezone

logger = logging.getLogger("session")


class SessionFilter:
    """
    Filters trading based on market session timing.
    Crypto is 24/7 but liquidity varies significantly by time.

    Best times for order flow (high liquidity):
    - London open: 08:00-12:00 UTC
    - NY open overlap: 13:00-17:00 UTC
    - Asia open: 00:00-03:00 UTC

    Avoid:
    - Weekends (lower volume, unreliable signals)
    - Between sessions (06:00-08:00, 17:00-20:00 UTC)
    """

    SESSIONS = {
        "asia":       {"start": 0,  "end": 3,  "quality": "good"},
        "asia_late":  {"start": 3,  "end": 7,  "quality": "low"},
        "london":     {"start": 7,  "end": 12, "quality": "excellent"},
        "overlap":    {"start": 12, "end": 17, "quality": "excellent"},
        "ny_late":    {"start": 17, "end": 21, "quality": "medium"},
        "dead_zone":  {"start": 21, "end": 24, "quality": "low"},
    }

    QUALITY_MULTIPLIERS = {
        "excellent": 1.0,
        "good": 0.9,
        "medium": 0.7,
        "low": 0.4,
    }

    def __init__(self, block_weekends: bool = True, block_low_quality: bool = False):
        self.block_weekends = block_weekends
        self.block_low_quality = block_low_quality

    def get_current_session(self) -> dict:
        now = datetime.now(timezone.utc)
        hour = now.hour
        weekday = now.weekday()
        is_weekend = weekday >= 5

        session_name = "unknown"
        quality = "medium"

        for name, info in self.SESSIONS.items():
            if info["start"] <= hour < info["end"]:
                session_name = name
                quality = info["quality"]
                break

        if is_weekend:
            quality = "low"

        return {
            "session": session_name,
            "quality": quality,
            "hour_utc": hour,
            "weekday": weekday,
            "is_weekend": is_weekend,
            "size_multiplier": self.QUALITY_MULTIPLIERS.get(quality, 0.7),
        }

    def should_trade(self) -> dict:
        session = self.get_current_session()

        if self.block_weekends and session["is_weekend"]:
            return {
                "allowed": False,
                "reason": "Weekend — reduced liquidity",
                "session": session,
            }

        if self.block_low_quality and session["quality"] == "low":
            return {
                "allowed": False,
                "reason": f"Low quality session: {session['session']}",
                "session": session,
            }

        return {"allowed": True, "reason": "", "session": session}

    def get_size_multiplier(self) -> float:
        session = self.get_current_session()
        return session["size_multiplier"]
