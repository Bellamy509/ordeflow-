import logging
import math
from typing import Dict, List, Optional
from collections import defaultdict
from models import FootprintCandle

logger = logging.getLogger("vprofile")


class CompositeVolumeProfile:
    """
    Builds composite volume profiles across multiple candles (24h, 7d)
    to identify institutional levels: POC, Value Area, HVN, LVN.
    """

    def __init__(self, scale: float = 0.5):
        self.scale = scale
        self._levels: Dict[float, float] = defaultdict(float)
        self._candle_count = 0

    def _price_to_level(self, price: float) -> float:
        return math.floor(price / self.scale) * self.scale

    def add_candle(self, candle: FootprintCandle):
        """Add a candle's volume data to the composite profile."""
        for price, level in candle.levels.items():
            rounded = self._price_to_level(price)
            self._levels[rounded] += level.total_volume
        self._candle_count += 1

    def reset(self):
        self._levels.clear()
        self._candle_count = 0

    @property
    def total_volume(self) -> float:
        return sum(self._levels.values())

    def get_poc(self) -> Optional[float]:
        if not self._levels:
            return None
        return max(self._levels, key=self._levels.get)

    def get_value_area(self, pct: float = 0.70) -> dict:
        if not self._levels:
            return {"high": 0, "low": 0, "poc": 0}

        sorted_levels = sorted(self._levels.items(), key=lambda x: x[1], reverse=True)
        poc = sorted_levels[0][0]
        target = self.total_volume * pct
        accumulated = 0
        included = []

        for price, vol in sorted_levels:
            if accumulated >= target:
                break
            accumulated += vol
            included.append(price)

        return {
            "high": max(included) if included else 0,
            "low": min(included) if included else 0,
            "poc": poc,
            "poc_volume": self._levels.get(poc, 0),
        }

    def get_hvn_lvn(self, top_n: int = 3) -> dict:
        """
        High Volume Nodes = levels with most volume (support/resistance).
        Low Volume Nodes = gaps with least volume (price moves fast through these).
        """
        if len(self._levels) < 5:
            return {"hvn": [], "lvn": []}

        sorted_levels = sorted(self._levels.items(), key=lambda x: x[1], reverse=True)
        avg_vol = self.total_volume / len(self._levels)

        hvn = [{"price": p, "volume": v, "ratio": v / avg_vol}
               for p, v in sorted_levels[:top_n]]

        lvn_sorted = sorted(self._levels.items(), key=lambda x: x[1])
        lvn = [{"price": p, "volume": v, "ratio": v / avg_vol}
               for p, v in lvn_sorted[:top_n] if v < avg_vol * 0.3]

        return {"hvn": hvn, "lvn": lvn}

    def get_signal_bias(self, current_price: float) -> dict:
        """
        Returns bias based on price position relative to composite profile.
        Price below POC = bullish magnet pull up. Above = bearish pull down.
        Near HVN = strong S/R. Near LVN = breakout zone.
        """
        va = self.get_value_area()
        poc = va["poc"]
        if poc == 0 or current_price == 0:
            return {"bias": 0, "reason": "No profile data", "level_type": "none"}

        distance_pct = (current_price - poc) / poc * 100

        if abs(distance_pct) < 0.05:
            return {"bias": 0, "reason": f"At POC ({poc:.2f})", "level_type": "poc"}

        if current_price < va["low"]:
            return {
                "bias": 8,
                "reason": f"Below Value Area Low ({va['low']:.2f}), mean reversion up likely",
                "level_type": "below_va",
            }

        if current_price > va["high"]:
            return {
                "bias": -8,
                "reason": f"Above Value Area High ({va['high']:.2f}), mean reversion down likely",
                "level_type": "above_va",
            }

        if distance_pct < -0.2:
            return {"bias": 5, "reason": f"Below POC ({poc:.2f}) — pull up", "level_type": "below_poc"}

        if distance_pct > 0.2:
            return {"bias": -5, "reason": f"Above POC ({poc:.2f}) — pull down", "level_type": "above_poc"}

        return {"bias": 0, "reason": "Inside value area", "level_type": "inside_va"}

    def get_analysis(self) -> dict:
        va = self.get_value_area()
        hvn_lvn = self.get_hvn_lvn()
        return {
            "poc": va["poc"],
            "va_high": va["high"],
            "va_low": va["low"],
            "total_volume": round(self.total_volume, 2),
            "candles": self._candle_count,
            "levels": len(self._levels),
            "hvn": hvn_lvn["hvn"],
            "lvn": hvn_lvn["lvn"],
        }


class MultiPeriodProfile:
    """Manages composite profiles for multiple time periods (session, daily, weekly)."""

    def __init__(self, scale: float = 0.5):
        self.session = CompositeVolumeProfile(scale)
        self.daily = CompositeVolumeProfile(scale)
        self.weekly = CompositeVolumeProfile(scale)
        self._session_candles = 0
        self._daily_candles = 0

    def add_candle(self, candle: FootprintCandle):
        self.session.add_candle(candle)
        self.daily.add_candle(candle)
        self.weekly.add_candle(candle)

        self._session_candles += 1
        self._daily_candles += 1

        # Reset session every 12 candles (1h for 5min TF)
        if self._session_candles >= 12:
            self.session.reset()
            self._session_candles = 0

        # Reset daily every 288 candles (24h for 5min TF)
        if self._daily_candles >= 288:
            self.daily.reset()
            self._daily_candles = 0

    def get_combined_bias(self, current_price: float) -> dict:
        daily_bias = self.daily.get_signal_bias(current_price)
        weekly_bias = self.weekly.get_signal_bias(current_price)

        # Weekly profile carries more weight
        combined = daily_bias["bias"] + weekly_bias["bias"] * 1.5
        combined = max(-15, min(15, int(combined)))

        return {
            "bias": combined,
            "daily": daily_bias,
            "weekly": weekly_bias,
        }

    def get_analysis(self) -> dict:
        return {
            "session": self.session.get_analysis(),
            "daily": self.daily.get_analysis(),
            "weekly": self.weekly.get_analysis(),
        }
