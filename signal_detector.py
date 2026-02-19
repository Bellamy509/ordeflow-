import logging
from typing import List
from models import (
    FootprintCandle, OrderFlowSignal, SignalType,
)
from footprint_engine import FootprintEngine

logger = logging.getLogger("signals")


class SignalDetector:
    """Analyzes footprint data to detect actionable order flow signals."""

    def __init__(self, footprint: FootprintEngine):
        self.fp = footprint

    def analyze(self, candle: FootprintCandle) -> List[OrderFlowSignal]:
        """Run all detectors on a completed candle and return signals."""
        signals: List[OrderFlowSignal] = []

        signals.extend(self._detect_stacked_imbalances(candle))
        signals.extend(self._detect_delta_divergence(candle))
        signals.extend(self._detect_absorption(candle))
        signals.extend(self._detect_exhaustion())
        signals.extend(self._detect_cvd_confirmation(candle))
        signals.extend(self._detect_poc_magnet(candle))

        for s in signals:
            logger.info(f"SIGNAL: {s.type.value} | strength={s.strength:.0f} | price={s.price} | {s.description}")

        return signals

    # ------------------------------------------------------------------
    # 1. Stacked imbalances
    # ------------------------------------------------------------------
    def _detect_stacked_imbalances(self, candle: FootprintCandle) -> List[OrderFlowSignal]:
        stacks = self.fp.get_stacked_imbalances(candle)
        signals = []

        for stack in stacks["buy"]:
            strength = min(len(stack) * 20 + 20, 100)
            signals.append(OrderFlowSignal(
                type=SignalType.STACKED_IMBALANCE_BUY,
                strength=strength,
                price=stack[0],
                timestamp=candle.timestamp,
                description=f"{len(stack)} stacked buy levels at {stack[0]:.2f}-{stack[-1]:.2f}",
            ))

        for stack in stacks["sell"]:
            strength = min(len(stack) * 20 + 20, 100)
            signals.append(OrderFlowSignal(
                type=SignalType.STACKED_IMBALANCE_SELL,
                strength=strength,
                price=stack[-1],
                timestamp=candle.timestamp,
                description=f"{len(stack)} stacked sell levels at {stack[0]:.2f}-{stack[-1]:.2f}",
            ))

        return signals

    # ------------------------------------------------------------------
    # 2. Delta divergence
    # ------------------------------------------------------------------
    def _detect_delta_divergence(self, candle: FootprintCandle) -> List[OrderFlowSignal]:
        prev_candles = self.fp.get_last_n_candles(3)
        if len(prev_candles) < 2:
            return []

        prev = prev_candles[-2]
        signals = []

        price_up = candle.close > prev.close and candle.high > prev.high
        price_down = candle.close < prev.close and candle.low < prev.low
        delta_negative = candle.delta < 0 and candle.delta < prev.delta
        delta_positive = candle.delta > 0 and candle.delta > prev.delta

        if price_up and delta_negative:
            div_strength = min(abs(candle.delta) / max(candle.total_volume, 1) * 200, 100)
            signals.append(OrderFlowSignal(
                type=SignalType.DELTA_DIVERGENCE_BEAR,
                strength=div_strength,
                price=candle.close,
                timestamp=candle.timestamp,
                description=f"Price up but delta falling ({candle.delta:+.2f}) — hidden selling",
            ))

        if price_down and delta_positive:
            div_strength = min(abs(candle.delta) / max(candle.total_volume, 1) * 200, 100)
            signals.append(OrderFlowSignal(
                type=SignalType.DELTA_DIVERGENCE_BULL,
                strength=div_strength,
                price=candle.close,
                timestamp=candle.timestamp,
                description=f"Price down but delta rising ({candle.delta:+.2f}) — hidden buying",
            ))

        return signals

    # ------------------------------------------------------------------
    # 3. Absorption
    # ------------------------------------------------------------------
    def _detect_absorption(self, candle: FootprintCandle) -> List[OrderFlowSignal]:
        absorption = self.fp.detect_absorption(candle)
        signals = []

        for zone in absorption["support"]:
            strength = min(zone["strength"] * 25, 100)
            signals.append(OrderFlowSignal(
                type=SignalType.ABSORPTION_SUPPORT,
                strength=strength,
                price=zone["price"],
                timestamp=candle.timestamp,
                description=f"Bid absorption at {zone['price']:.2f} — bid:{zone['bid_vol']:.1f} vs ask:{zone['ask_vol']:.1f}",
            ))

        for zone in absorption["resistance"]:
            strength = min(zone["strength"] * 25, 100)
            signals.append(OrderFlowSignal(
                type=SignalType.ABSORPTION_RESISTANCE,
                strength=strength,
                price=zone["price"],
                timestamp=candle.timestamp,
                description=f"Ask absorption at {zone['price']:.2f} — ask:{zone['ask_vol']:.1f} vs bid:{zone['bid_vol']:.1f}",
            ))

        return signals

    # ------------------------------------------------------------------
    # 4. Exhaustion
    # ------------------------------------------------------------------
    def _detect_exhaustion(self) -> List[OrderFlowSignal]:
        result = self.fp.detect_exhaustion()
        if result is None:
            return []

        candle = self.fp.last_candle
        ts = candle.timestamp if candle else 0

        if "bull" in result["type"]:
            return [OrderFlowSignal(
                type=SignalType.EXHAUSTION_BULL,
                strength=result["strength"],
                price=result["price"],
                timestamp=ts,
                description=f"Bull exhaustion — high buy delta but no price follow-through (Δ_ratio={result['delta_ratio']:.1f})",
            )]
        else:
            return [OrderFlowSignal(
                type=SignalType.EXHAUSTION_BEAR,
                strength=result["strength"],
                price=result["price"],
                timestamp=ts,
                description=f"Bear exhaustion — high sell delta but no price follow-through (Δ_ratio={result['delta_ratio']:.1f})",
            )]

    # ------------------------------------------------------------------
    # 5. CVD confirmation
    # ------------------------------------------------------------------
    def _detect_cvd_confirmation(self, candle: FootprintCandle) -> List[OrderFlowSignal]:
        trend = self.fp.get_cvd_trend()
        if trend["strength"] < 30:
            return []

        signals = []
        price_up = candle.close > candle.open
        price_down = candle.close < candle.open

        if trend["direction"] == "up" and price_up:
            signals.append(OrderFlowSignal(
                type=SignalType.CVD_CONFIRMS_UP,
                strength=trend["strength"],
                price=candle.close,
                timestamp=candle.timestamp,
                description=f"CVD trending up confirms bullish move (slope={trend['slope']:+.3f})",
            ))
        elif trend["direction"] == "down" and price_down:
            signals.append(OrderFlowSignal(
                type=SignalType.CVD_CONFIRMS_DOWN,
                strength=trend["strength"],
                price=candle.close,
                timestamp=candle.timestamp,
                description=f"CVD trending down confirms bearish move (slope={trend['slope']:+.3f})",
            ))

        return signals

    # ------------------------------------------------------------------
    # 6. POC magnet
    # ------------------------------------------------------------------
    def _detect_poc_magnet(self, candle: FootprintCandle) -> List[OrderFlowSignal]:
        prev_candles = self.fp.get_last_n_candles(5)
        if len(prev_candles) < 2:
            return []

        prev = prev_candles[-2]
        prev_va = prev.get_value_area()
        price = candle.close
        poc = prev_va.poc

        if poc == 0:
            return []

        distance_pct = abs(price - poc) / poc * 100

        if distance_pct < 0.05:
            return []

        signals = []

        if distance_pct > 0.3 and price < poc:
            strength = min(distance_pct * 100, 80)
            signals.append(OrderFlowSignal(
                type=SignalType.POC_MAGNET_LONG,
                strength=strength,
                price=price,
                timestamp=candle.timestamp,
                description=f"Price {distance_pct:.2f}% below prev POC ({poc:.2f}) — magnet pull up",
            ))
        elif distance_pct > 0.3 and price > poc:
            strength = min(distance_pct * 100, 80)
            signals.append(OrderFlowSignal(
                type=SignalType.POC_MAGNET_SHORT,
                strength=strength,
                price=price,
                timestamp=candle.timestamp,
                description=f"Price {distance_pct:.2f}% above prev POC ({poc:.2f}) — magnet pull down",
            ))

        return signals
