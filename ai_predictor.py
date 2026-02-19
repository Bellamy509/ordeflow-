import logging
import numpy as np
from typing import List, Optional
from collections import deque
from models import FootprintCandle

logger = logging.getLogger("ai")


class FeatureExtractor:
    """Extracts ML features from footprint candles."""

    @staticmethod
    def extract(candles: List[FootprintCandle], lookback: int = 20) -> Optional[np.ndarray]:
        if len(candles) < lookback:
            return None

        recent = candles[-lookback:]
        features = []

        for c in recent:
            va = c.get_value_area()
            features.extend([
                c.close,
                c.high - c.low,
                c.delta,
                c.total_volume,
                len(c.levels),
                c.total_ask / max(c.total_bid, 0.01),
                (c.close - va.poc) / max(c.close, 1) * 100,
                va.high - va.low,
            ])

        arr = np.array(features, dtype=np.float32)

        mean = arr.mean()
        std = arr.std()
        if std > 0:
            arr = (arr - mean) / std

        return arr


class LSTMPredictor:
    """
    LSTM-based price direction predictor.
    Requires PyTorch for training — falls back to statistical prediction.
    """

    def __init__(self, lookback: int = 20):
        self.lookback = lookback
        self._predictions: deque = deque(maxlen=100)
        self._torch_available = False
        self.model = None

        try:
            import torch
            import torch.nn as nn
            self._torch_available = True
            logger.info("PyTorch available — LSTM predictor enabled")
        except ImportError:
            logger.info("PyTorch not installed — using statistical predictor")

    def predict(self, candles: List[FootprintCandle]) -> dict:
        """
        Predict next candle direction.
        Returns {"direction": "up"/"down"/"neutral", "confidence": 0-100}
        """
        if len(candles) < 5:
            return {"direction": "neutral", "confidence": 0, "method": "insufficient_data"}

        if self._torch_available and self.model:
            return self._predict_lstm(candles)
        return self._predict_statistical(candles)

    def _predict_statistical(self, candles: List[FootprintCandle]) -> dict:
        """Statistical prediction using momentum, delta trend, and volume."""
        recent = candles[-min(10, len(candles)):]

        delta_sum = sum(c.delta for c in recent[-3:])
        price_momentum = (recent[-1].close - recent[0].close) / max(recent[0].close, 1)
        vol_trend = sum(c.total_volume for c in recent[-3:]) / max(sum(c.total_volume for c in recent[:3]), 0.01)

        score = 0

        if delta_sum > 0:
            score += min(abs(delta_sum) / max(sum(abs(c.delta) for c in recent) / len(recent), 0.01) * 20, 30)
        else:
            score -= min(abs(delta_sum) / max(sum(abs(c.delta) for c in recent) / len(recent), 0.01) * 20, 30)

        score += price_momentum * 5000

        if vol_trend > 1.3:
            score += 10 if score > 0 else -10

        confidence = min(abs(score), 80)

        if score > 5:
            direction = "up"
        elif score < -5:
            direction = "down"
        else:
            direction = "neutral"
            confidence = max(confidence, 20)

        result = {"direction": direction, "confidence": round(confidence), "method": "statistical"}
        self._predictions.append(result)
        return result

    def _predict_lstm(self, candles: List[FootprintCandle]) -> dict:
        """LSTM prediction (requires trained model)."""
        features = FeatureExtractor.extract(candles, self.lookback)
        if features is None:
            return self._predict_statistical(candles)

        try:
            import torch
            x = torch.FloatTensor(features).unsqueeze(0).unsqueeze(0)
            with torch.no_grad():
                output = self.model(x)
                prob = torch.sigmoid(output).item()

            if prob > 0.6:
                direction = "up"
            elif prob < 0.4:
                direction = "down"
            else:
                direction = "neutral"

            confidence = abs(prob - 0.5) * 200
            return {"direction": direction, "confidence": round(confidence), "method": "lstm"}
        except Exception as e:
            logger.warning(f"LSTM prediction error: {e}")
            return self._predict_statistical(candles)

    def get_signal_bias(self, candles: List[FootprintCandle]) -> int:
        pred = self.predict(candles)
        if pred["confidence"] < 40:
            return 0
        if pred["direction"] == "up":
            return min(pred["confidence"] // 10, 10)
        elif pred["direction"] == "down":
            return -min(pred["confidence"] // 10, 10)
        return 0
