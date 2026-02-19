import logging
import random
import json
from typing import List, Dict
from models import RawTick
from backtester import Backtester, BacktestResult
from config import Config

logger = logging.getLogger("hyperopt")


class ParameterSpace:
    """Defines the parameter ranges to optimize."""

    RANGES = {
        "min_confluence_score": (50, 80, 5),
        "imbalance_ratio": (2.0, 5.0, 0.5),
        "stacked_imbalance_min": (2, 5, 1),
        "trailing_activation_pct": (0.1, 0.6, 0.1),
        "trailing_distance_pct": (0.1, 0.4, 0.05),
        "risk_per_trade_pct": (0.5, 2.0, 0.25),
    }

    @classmethod
    def random_params(cls) -> Dict:
        params = {}
        for key, (low, high, step) in cls.RANGES.items():
            steps = int((high - low) / step) + 1
            params[key] = round(low + random.randint(0, steps - 1) * step, 4)
        return params

    @classmethod
    def total_combinations(cls) -> int:
        total = 1
        for key, (low, high, step) in cls.RANGES.items():
            total *= int((high - low) / step) + 1
        return total


class HyperOptimizer:
    """
    Runs random search over parameter space using the backtester.
    Finds the optimal parameter combination for max Sharpe-like score.
    """

    def __init__(self, symbol: str, ticks: List[RawTick]):
        self.symbol = symbol
        self.ticks = ticks
        self.results: List[Dict] = []

    def _score(self, result: BacktestResult) -> float:
        """Combined score: profit factor * win rate, penalized by drawdown."""
        if result.total_trades < 5:
            return -999

        pf = min(result.profit_factor, 5)
        wr = result.win_rate / 100
        dd_penalty = result.max_drawdown / 10000
        trade_bonus = min(result.total_trades / 50, 1.0) * 5

        return pf * 20 + wr * 30 - dd_penalty * 10 + trade_bonus + result.total_pnl / 100

    def run(self, iterations: int = 100, initial_balance: float = 10000) -> Dict:
        """Run random search optimization."""
        logger.info(f"Starting hyperopt: {iterations} iterations on {len(self.ticks)} ticks")
        logger.info(f"Parameter space: {ParameterSpace.total_combinations()} total combinations")

        best_score = -999
        best_params = {}
        best_result = None

        for i in range(iterations):
            params = ParameterSpace.random_params()
            config = Config()

            config.min_confluence_score = params["min_confluence_score"]
            config.imbalance_ratio = params["imbalance_ratio"]
            config.stacked_imbalance_min = int(params["stacked_imbalance_min"])
            config.trailing_activation_pct = params["trailing_activation_pct"]
            config.trailing_distance_pct = params["trailing_distance_pct"]
            config.risk_per_trade_pct = params["risk_per_trade_pct"]

            bt = Backtester(config, self.symbol)
            result = bt.run(self.ticks, initial_balance, params["risk_per_trade_pct"])
            score = self._score(result)

            self.results.append({"params": params, "score": score, "trades": result.total_trades,
                                 "pnl": result.total_pnl, "win_rate": result.win_rate,
                                 "pf": result.profit_factor, "dd": result.max_drawdown})

            if score > best_score:
                best_score = score
                best_params = params
                best_result = result
                logger.info(
                    f"[{i+1}/{iterations}] NEW BEST: score={score:.1f} | "
                    f"PnL=${result.total_pnl:+.2f} WR={result.win_rate:.1f}% "
                    f"PF={result.profit_factor:.2f} DD=${result.max_drawdown:.2f} | "
                    f"params={params}"
                )
            elif (i + 1) % 25 == 0:
                logger.info(f"[{i+1}/{iterations}] Best so far: score={best_score:.1f}")

        logger.info(f"\n{'='*50}")
        logger.info(f"HYPEROPT COMPLETE — {iterations} iterations")
        logger.info(f"Best score: {best_score:.1f}")
        logger.info(f"Best params: {json.dumps(best_params, indent=2)}")
        if best_result:
            logger.info(best_result.summary())

        return {"best_params": best_params, "best_score": best_score,
                "best_result": best_result, "all_results": self.results}

    def save_results(self, filepath: str = "hyperopt_results.json"):
        with open(filepath, "w") as f:
            json.dump(self.results, f, indent=2)
        logger.info(f"Results saved to {filepath}")
