import logging
from typing import Optional, List, Set, Tuple
from models import (
    FootprintCandle, OrderFlowSignal, SignalType,
    TradeSignal, Side, StrategyType,
)
from footprint_engine import FootprintEngine
from config import Config

logger = logging.getLogger("strategy")

BULLISH_SIGNALS = {
    SignalType.STACKED_IMBALANCE_BUY,
    SignalType.DELTA_DIVERGENCE_BULL,
    SignalType.ABSORPTION_SUPPORT,
    SignalType.EXHAUSTION_BEAR,       # bears exhausted → bullish
    SignalType.CVD_CONFIRMS_UP,
    SignalType.POC_MAGNET_LONG,
}

BEARISH_SIGNALS = {
    SignalType.STACKED_IMBALANCE_SELL,
    SignalType.DELTA_DIVERGENCE_BEAR,
    SignalType.ABSORPTION_RESISTANCE,
    SignalType.EXHAUSTION_BULL,       # bulls exhausted → bearish
    SignalType.CVD_CONFIRMS_DOWN,
    SignalType.POC_MAGNET_SHORT,
}

SIGNAL_WEIGHTS = {
    SignalType.STACKED_IMBALANCE_BUY: 1.3,
    SignalType.STACKED_IMBALANCE_SELL: 1.3,
    SignalType.DELTA_DIVERGENCE_BULL: 1.2,
    SignalType.DELTA_DIVERGENCE_BEAR: 1.2,
    SignalType.ABSORPTION_SUPPORT: 1.1,
    SignalType.ABSORPTION_RESISTANCE: 1.1,
    SignalType.EXHAUSTION_BULL: 1.0,
    SignalType.EXHAUSTION_BEAR: 1.0,
    SignalType.CVD_CONFIRMS_UP: 0.8,
    SignalType.CVD_CONFIRMS_DOWN: 0.8,
    SignalType.POC_MAGNET_LONG: 0.7,
    SignalType.POC_MAGNET_SHORT: 0.7,
}


class StrategyEngine:
    """Combines order flow signals into trade decisions with SL/TP levels."""

    def __init__(self, config: Config, footprint: FootprintEngine):
        self.config = config
        self.fp = footprint
        self.min_score = config.min_confluence_score

    def evaluate(self, candle: FootprintCandle, signals: List[OrderFlowSignal]) -> Optional[TradeSignal]:
        if not signals:
            return None

        bull_score = self._compute_directional_score(signals, BULLISH_SIGNALS)
        bear_score = self._compute_directional_score(signals, BEARISH_SIGNALS)

        logger.info(f"Confluence scores — BULL: {bull_score:.1f} | BEAR: {bear_score:.1f} | min: {self.min_score}")

        if bull_score >= self.min_score and bull_score > bear_score:
            return self._build_long_signal(candle, signals, bull_score)

        if bear_score >= self.min_score and bear_score > bull_score:
            return self._build_short_signal(candle, signals, bear_score)

        return None

    def _compute_directional_score(self, signals: List[OrderFlowSignal], valid_types: set) -> float:
        relevant = [s for s in signals if s.type in valid_types]
        if len(relevant) < 2:
            return 0.0

        unique_types = len({s.type for s in relevant})
        if unique_types < 2:
            return 0.0

        weighted_sum = sum(s.strength * SIGNAL_WEIGHTS.get(s.type, 1.0) for s in relevant)
        avg_weighted = weighted_sum / len(relevant)
        max_single = 100 * max(SIGNAL_WEIGHTS.values())

        base_score = (avg_weighted / max_single) * 70

        confluence_bonus = min((unique_types - 1) * 12, 30)
        score = base_score + confluence_bonus

        return min(score, 100)

    def _build_long_signal(self, candle: FootprintCandle, signals: List[OrderFlowSignal],
                           score: float) -> TradeSignal:
        strategy = self._classify_strategy(signals, Side.BUY)
        sl, tp = self._compute_sl_tp(candle, Side.BUY, strategy)
        bull_signals = [s for s in signals if s.type in BULLISH_SIGNALS]

        trade = TradeSignal(
            side=Side.BUY,
            strategy=strategy,
            entry_price=candle.close,
            stop_loss=sl,
            take_profit=tp,
            confluence_score=score,
            signals=bull_signals,
        )
        logger.info(
            f">>> LONG SIGNAL | {strategy.value} | entry={trade.entry_price:.2f} "
            f"SL={sl:.2f} TP={tp:.2f} score={score:.1f}"
        )
        return trade

    def _build_short_signal(self, candle: FootprintCandle, signals: List[OrderFlowSignal],
                            score: float) -> TradeSignal:
        strategy = self._classify_strategy(signals, Side.SELL)
        sl, tp = self._compute_sl_tp(candle, Side.SELL, strategy)
        bear_signals = [s for s in signals if s.type in BEARISH_SIGNALS]

        trade = TradeSignal(
            side=Side.SELL,
            strategy=strategy,
            entry_price=candle.close,
            stop_loss=sl,
            take_profit=tp,
            confluence_score=score,
            signals=bear_signals,
        )
        logger.info(
            f">>> SHORT SIGNAL | {strategy.value} | entry={trade.entry_price:.2f} "
            f"SL={sl:.2f} TP={tp:.2f} score={score:.1f}"
        )
        return trade

    def _classify_strategy(self, signals: List[OrderFlowSignal], side: Side) -> StrategyType:
        types = {s.type for s in signals}

        has_absorption = (
            SignalType.ABSORPTION_SUPPORT in types or SignalType.ABSORPTION_RESISTANCE in types
        )
        has_divergence = (
            SignalType.DELTA_DIVERGENCE_BULL in types or SignalType.DELTA_DIVERGENCE_BEAR in types
        )
        has_exhaustion = (
            SignalType.EXHAUSTION_BULL in types or SignalType.EXHAUSTION_BEAR in types
        )
        has_imbalance = (
            SignalType.STACKED_IMBALANCE_BUY in types or SignalType.STACKED_IMBALANCE_SELL in types
        )
        has_cvd = (
            SignalType.CVD_CONFIRMS_UP in types or SignalType.CVD_CONFIRMS_DOWN in types
        )
        has_poc = (
            SignalType.POC_MAGNET_LONG in types or SignalType.POC_MAGNET_SHORT in types
        )

        if has_absorption or has_divergence or has_exhaustion:
            return StrategyType.REVERSAL
        # Imbalance + another confirming signal = reversal (not pure breakout)
        if has_imbalance and (has_cvd or has_poc):
            return StrategyType.REVERSAL
        if has_imbalance:
            return StrategyType.BREAKOUT
        if has_poc:
            return StrategyType.POC_REVERSION
        return StrategyType.REVERSAL

    def _compute_sl_tp(self, candle: FootprintCandle, side: Side,
                       strategy: StrategyType) -> Tuple[float, float]:
        va = candle.get_value_area()

        recent = self.fp.get_last_n_candles(10)
        if len(recent) >= 3:
            ranges = [c.high - c.low for c in recent if c.high - c.low > 0]
            atr_proxy = sum(ranges) / len(ranges) if ranges else candle.close * 0.002
        else:
            atr_proxy = candle.high - candle.low
        if atr_proxy == 0:
            atr_proxy = candle.close * 0.002

        if strategy == StrategyType.REVERSAL:
            sl_distance = atr_proxy * 1.2
            tp_distance = atr_proxy * 2.0
        elif strategy == StrategyType.BREAKOUT:
            sl_distance = atr_proxy * 0.8
            tp_distance = atr_proxy * 2.5
        else:  # POC reversion
            sl_distance = atr_proxy * 1.0
            # TP at POC
            poc_distance = abs(candle.close - va.poc)
            tp_distance = max(poc_distance, atr_proxy * 1.5)

        if side == Side.BUY:
            sl = candle.close - sl_distance
            tp = candle.close + tp_distance
        else:
            sl = candle.close + sl_distance
            tp = candle.close - tp_distance

        return round(sl, 2), round(tp, 2)
