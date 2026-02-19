import logging
import math
from typing import Dict, List
from collections import deque

logger = logging.getLogger("correlation")


class CorrelationFilter:
    """
    Tracks price correlation between markets.
    Blocks trades on correlated pairs to avoid hidden double-exposure.
    """

    def __init__(self, lookback: int = 60, block_threshold: float = 0.85):
        self.lookback = lookback
        self.block_threshold = block_threshold
        self._price_history: Dict[str, deque] = {}
        self._correlations: Dict[str, float] = {}

    def record_price(self, symbol: str, price: float):
        if symbol not in self._price_history:
            self._price_history[symbol] = deque(maxlen=self.lookback)
        self._price_history[symbol].append(price)

    def update_correlations(self):
        """Calculate pairwise correlations between all tracked symbols."""
        symbols = list(self._price_history.keys())
        self._correlations.clear()

        for i in range(len(symbols)):
            for j in range(i + 1, len(symbols)):
                sym_a, sym_b = symbols[i], symbols[j]
                corr = self._pearson(
                    list(self._price_history[sym_a]),
                    list(self._price_history[sym_b]),
                )
                pair_key = f"{sym_a}|{sym_b}"
                self._correlations[pair_key] = corr

    def _pearson(self, x: list, y: list) -> float:
        n = min(len(x), len(y))
        if n < 10:
            return 0.0

        x, y = x[-n:], y[-n:]

        rx = self._returns(x)
        ry = self._returns(y)
        n = min(len(rx), len(ry))
        if n < 5:
            return 0.0

        rx, ry = rx[-n:], ry[-n:]
        mx = sum(rx) / n
        my = sum(ry) / n

        cov = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
        sx = math.sqrt(sum((r - mx) ** 2 for r in rx))
        sy = math.sqrt(sum((r - my) ** 2 for r in ry))

        if sx == 0 or sy == 0:
            return 0.0
        return cov / (sx * sy)

    def _returns(self, prices: list) -> list:
        return [(prices[i] - prices[i-1]) / prices[i-1]
                for i in range(1, len(prices)) if prices[i-1] != 0]

    def should_block_trade(self, symbol: str, side: str, open_positions: list) -> Dict:
        """
        Check if opening a trade on `symbol` would create
        double-exposure with an existing correlated position.
        """
        for pos in open_positions:
            if pos.symbol == symbol:
                continue

            corr = self._get_correlation(symbol, pos.symbol)
            same_direction = (side == pos.side.value)
            high_corr = abs(corr) >= self.block_threshold

            if high_corr and same_direction and corr > 0:
                return {
                    "blocked": True,
                    "reason": f"Correlated with open {pos.side.value} {pos.symbol} "
                              f"(r={corr:.2f} > {self.block_threshold})",
                }

            if high_corr and not same_direction and corr < 0:
                return {
                    "blocked": True,
                    "reason": f"Inverse-correlated with {pos.side.value} {pos.symbol} "
                              f"(r={corr:.2f}), same effective direction",
                }

        return {"blocked": False, "reason": ""}

    def _get_correlation(self, sym_a: str, sym_b: str) -> float:
        key1 = f"{sym_a}|{sym_b}"
        key2 = f"{sym_b}|{sym_a}"
        return self._correlations.get(key1, self._correlations.get(key2, 0.0))

    def get_all_correlations(self) -> Dict[str, float]:
        return dict(self._correlations)
