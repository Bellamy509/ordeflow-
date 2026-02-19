import logging
from typing import List, Optional
from collections import deque
from models import FootprintCandle, OrderFlowSignal, SignalType

logger = logging.getLogger("liquidity")


class LiquiditySweepDetector:
    """
    Detects liquidity sweeps and stop hunts:
    - Price breaks a recent swing high/low (triggering stops)
    - Then immediately reverses (institutions collected the liquidity)
    """

    def __init__(self, swing_lookback: int = 10, min_sweep_pct: float = 0.05):
        self.swing_lookback = swing_lookback
        self.min_sweep_pct = min_sweep_pct
        self._swing_highs: deque = deque(maxlen=50)
        self._swing_lows: deque = deque(maxlen=50)

    def update_swings(self, candles: List[FootprintCandle]):
        """Identify swing highs and lows from recent candles."""
        if len(candles) < 5:
            return

        self._swing_highs.clear()
        self._swing_lows.clear()

        for i in range(2, len(candles) - 2):
            c = candles[i]
            left1, left2 = candles[i - 1], candles[i - 2]
            right1, right2 = candles[i + 1], candles[i + 2]

            if c.high > left1.high and c.high > left2.high and c.high > right1.high and c.high > right2.high:
                self._swing_highs.append({"price": c.high, "index": i, "timestamp": c.timestamp})

            if c.low < left1.low and c.low < left2.low and c.low < right1.low and c.low < right2.low:
                self._swing_lows.append({"price": c.low, "index": i, "timestamp": c.timestamp})

    def detect(self, candle: FootprintCandle, prev_candle: Optional[FootprintCandle]) -> List[dict]:
        """
        Detect if the current candle swept liquidity and reversed.
        A sweep = wick through a swing level but close back inside.
        """
        if not prev_candle:
            return []

        signals = []

        for swing in self._swing_highs:
            level = swing["price"]
            # Wick above the swing high but closed below it
            if candle.high > level and candle.close < level and prev_candle.close < level:
                sweep_size = (candle.high - level) / level * 100
                if sweep_size >= self.min_sweep_pct:
                    # Price swept above, rejected = bearish stop hunt
                    rejection_strength = (candle.high - candle.close) / max(candle.high - candle.low, 0.01) * 100
                    signals.append({
                        "type": "stop_hunt_bear",
                        "level": level,
                        "wick_high": candle.high,
                        "sweep_pct": sweep_size,
                        "rejection": rejection_strength,
                        "strength": min(sweep_size * 200 + rejection_strength * 0.5, 100),
                        "description": f"Bearish stop hunt: swept {level:.2f} high by {sweep_size:.3f}%, rejected back",
                    })

        for swing in self._swing_lows:
            level = swing["price"]
            # Wick below the swing low but closed above it
            if candle.low < level and candle.close > level and prev_candle.close > level:
                sweep_size = (level - candle.low) / level * 100
                if sweep_size >= self.min_sweep_pct:
                    rejection_strength = (candle.close - candle.low) / max(candle.high - candle.low, 0.01) * 100
                    signals.append({
                        "type": "stop_hunt_bull",
                        "level": level,
                        "wick_low": candle.low,
                        "sweep_pct": sweep_size,
                        "rejection": rejection_strength,
                        "strength": min(sweep_size * 200 + rejection_strength * 0.5, 100),
                        "description": f"Bullish stop hunt: swept {level:.2f} low by {sweep_size:.3f}%, rejected back",
                    })

        return signals

    def get_nearby_levels(self, price: float, distance_pct: float = 0.5) -> dict:
        """Get nearby swing levels for SL/TP placement."""
        above = []
        below = []

        for swing in self._swing_highs:
            dist = (swing["price"] - price) / price * 100
            if 0 < dist < distance_pct:
                above.append(swing["price"])

        for swing in self._swing_lows:
            dist = (price - swing["price"]) / price * 100
            if 0 < dist < distance_pct:
                below.append(swing["price"])

        return {"resistance": sorted(above), "support": sorted(below, reverse=True)}
