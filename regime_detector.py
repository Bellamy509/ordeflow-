import logging
import math
from typing import List, Optional
from enum import Enum
from collections import deque
from models import FootprintCandle

logger = logging.getLogger("regime")


class MarketRegime(str, Enum):
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    VOLATILE = "volatile"
    LOW_VOLUME = "low_volume"


REGIME_STRATEGY_MAP = {
    MarketRegime.TRENDING_UP: {"favor": "breakout", "avoid": "poc_reversion", "size_mult": 1.0},
    MarketRegime.TRENDING_DOWN: {"favor": "breakout", "avoid": "poc_reversion", "size_mult": 1.0},
    MarketRegime.RANGING: {"favor": "poc_reversion", "avoid": "breakout", "size_mult": 0.8},
    MarketRegime.VOLATILE: {"favor": "reversal", "avoid": "breakout", "size_mult": 0.5},
    MarketRegime.LOW_VOLUME: {"favor": None, "avoid": "all", "size_mult": 0.0},
}


class RegimeDetector:
    """
    Detects the current market regime using volatility, trend strength,
    and volume analysis from footprint data.
    """

    def __init__(self, lookback: int = 20):
        self.lookback = lookback
        self._regimes: deque = deque(maxlen=100)
        self.current_regime: MarketRegime = MarketRegime.RANGING

    def analyze(self, candles: List[FootprintCandle]) -> MarketRegime:
        if len(candles) < 5:
            return MarketRegime.RANGING

        recent = candles[-min(self.lookback, len(candles)):]

        volatility = self._calc_volatility(recent)
        trend = self._calc_trend_strength(recent)
        volume_health = self._calc_volume_health(recent)

        regime = self._classify(volatility, trend, volume_health, recent)
        self.current_regime = regime
        self._regimes.append(regime)

        logger.info(
            f"Regime: {regime.value} | volatility={volatility:.4f} "
            f"trend={trend:+.4f} vol_health={volume_health:.2f}"
        )
        return regime

    def _calc_volatility(self, candles: List[FootprintCandle]) -> float:
        if len(candles) < 2:
            return 0.0
        returns = []
        for i in range(1, len(candles)):
            if candles[i - 1].close > 0:
                r = (candles[i].close - candles[i - 1].close) / candles[i - 1].close
                returns.append(r)
        if not returns:
            return 0.0
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / len(returns)
        return math.sqrt(variance)

    def _calc_trend_strength(self, candles: List[FootprintCandle]) -> float:
        """Positive = uptrend, negative = downtrend, near 0 = no trend."""
        if len(candles) < 5:
            return 0.0

        closes = [c.close for c in candles]
        n = len(closes)
        x_mean = (n - 1) / 2
        y_mean = sum(closes) / n
        numerator = sum((i - x_mean) * (closes[i] - y_mean) for i in range(n))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        if denominator == 0:
            return 0.0
        slope = numerator / denominator
        return slope / y_mean if y_mean != 0 else 0.0

    def _calc_volume_health(self, candles: List[FootprintCandle]) -> float:
        if len(candles) < 3:
            return 1.0
        volumes = [c.total_volume for c in candles]
        avg_vol = sum(volumes) / len(volumes)
        recent_avg = sum(volumes[-3:]) / 3
        return recent_avg / avg_vol if avg_vol > 0 else 1.0

    def _classify(self, volatility: float, trend: float, vol_health: float,
                  candles: List[FootprintCandle]) -> MarketRegime:
        if vol_health < 0.3:
            return MarketRegime.LOW_VOLUME

        if volatility > 0.008:
            return MarketRegime.VOLATILE

        if abs(trend) > 0.0005:
            if trend > 0:
                return MarketRegime.TRENDING_UP
            return MarketRegime.TRENDING_DOWN

        return MarketRegime.RANGING

    def get_strategy_guidance(self) -> dict:
        return REGIME_STRATEGY_MAP.get(self.current_regime, REGIME_STRATEGY_MAP[MarketRegime.RANGING])

    def should_trade(self) -> bool:
        guidance = self.get_strategy_guidance()
        return guidance["size_mult"] > 0
