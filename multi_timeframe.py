import logging
from typing import Dict, List, Optional
from models import RawTick, FootprintCandle
from footprint_engine import FootprintEngine
from signal_detector import SignalDetector
from config import Config

logger = logging.getLogger("mtf")


class MultiTimeframeAnalyzer:
    """
    Runs parallel footprint engines at different timeframes (1m, 5m, 15m, 1h)
    and provides higher-timeframe confirmation for trade signals.
    """

    TIMEFRAMES = [1, 5, 15, 60]

    def __init__(self, config: Config, symbol: str):
        self.config = config
        self.symbol = symbol
        self.engines: Dict[int, FootprintEngine] = {}
        self.detectors: Dict[int, SignalDetector] = {}
        self.last_candles: Dict[int, Optional[FootprintCandle]] = {}

        for tf in self.TIMEFRAMES:
            cfg_copy = Config()
            cfg_copy.timeframe_minutes = tf
            engine = FootprintEngine(cfg_copy, symbol)
            self.engines[tf] = engine
            self.detectors[tf] = SignalDetector(engine)
            self.last_candles[tf] = None

        logger.info(f"[{symbol}] MTF initialized: {self.TIMEFRAMES}")

    def process_tick(self, tick: RawTick) -> Dict[int, Optional[FootprintCandle]]:
        """Process tick across all timeframes. Returns completed candles."""
        completed = {}
        for tf, engine in self.engines.items():
            result = engine.process_tick(tick)
            if result is not None:
                self.last_candles[tf] = result
                completed[tf] = result
        return completed

    def get_htf_bias(self, base_tf: int = 5) -> dict:
        """
        Check if higher timeframes confirm the direction.
        Returns bias score and analysis.
        """
        base_candle = self.last_candles.get(base_tf)
        if not base_candle:
            return {"bias": 0, "confirmation": "no_data", "details": {}}

        base_bullish = base_candle.delta > 0 and base_candle.close > base_candle.open

        confirmations = 0
        contradictions = 0
        details = {}

        for tf in self.TIMEFRAMES:
            if tf <= base_tf:
                continue

            engine = self.engines[tf]
            candle = self.last_candles.get(tf)
            if not candle:
                continue

            tf_bullish = candle.delta > 0 and candle.close > candle.open
            cvd_trend = engine.get_cvd_trend(5)

            if base_bullish and tf_bullish:
                confirmations += 1
            elif not base_bullish and not tf_bullish:
                confirmations += 1
            else:
                contradictions += 1

            details[f"{tf}m"] = {
                "delta": round(candle.delta, 2),
                "direction": "bull" if tf_bullish else "bear",
                "cvd_direction": cvd_trend["direction"],
            }

        total = confirmations + contradictions
        if total == 0:
            return {"bias": 0, "confirmation": "no_data", "details": details}

        bias = ((confirmations - contradictions) / total) * 15

        if confirmations > contradictions:
            confirmation = "confirmed"
        elif contradictions > confirmations:
            confirmation = "divergent"
        else:
            confirmation = "mixed"

        return {"bias": round(bias), "confirmation": confirmation, "details": details}

    def get_htf_signals(self, tf: int) -> list:
        candle = self.last_candles.get(tf)
        if not candle:
            return []
        return self.detectors[tf].analyze(candle)
