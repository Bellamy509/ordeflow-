import math
import logging
from typing import Optional, Dict, List
from collections import deque
from models import RawTick, FootprintLevel, FootprintCandle, ValueArea
from config import Config

logger = logging.getLogger("footprint")


class FootprintEngine:
    """Builds footprint candles from raw tick data in real-time."""

    def __init__(self, config: Config, symbol: str = ""):
        sym_cfg = config.get_symbol_config(symbol) if symbol else {}
        self.scale = sym_cfg.get("scale", config.footprint_scale)
        self.imbalance_ratio = sym_cfg.get("imbalance_ratio", config.imbalance_ratio)
        self.imbalance_volume = sym_cfg.get("imbalance_volume", config.imbalance_volume)
        self.stacked_min = sym_cfg.get("stacked_imbalance_min", config.stacked_imbalance_min)
        self.candle_ms = config.timeframe_minutes * 60 * 1000

        self.current_candle: Optional[FootprintCandle] = None
        self.completed_candles: deque[FootprintCandle] = deque(maxlen=500)
        self.cumulative_delta: float = 0.0
        self.cvd_history: deque[float] = deque(maxlen=500)

    def _price_to_level(self, price: float) -> float:
        return math.floor(price / self.scale) * self.scale

    def _candle_start(self, timestamp: int) -> int:
        return (timestamp // self.candle_ms) * self.candle_ms

    def process_tick(self, tick: RawTick) -> Optional[FootprintCandle]:
        """
        Process a single tick. Returns a completed FootprintCandle
        when a candle period closes, otherwise None.
        """
        candle_ts = self._candle_start(tick.timestamp)
        completed = None

        if self.current_candle is None:
            self.current_candle = FootprintCandle(timestamp=candle_ts, open=tick.price)
        elif candle_ts > self.current_candle.timestamp:
            completed = self._close_candle()
            self.current_candle = FootprintCandle(timestamp=candle_ts, open=tick.price)

        self._add_tick_to_candle(tick)
        return completed

    def _add_tick_to_candle(self, tick: RawTick):
        candle = self.current_candle
        level_price = self._price_to_level(tick.price)

        if level_price not in candle.levels:
            candle.levels[level_price] = FootprintLevel(price=level_price)

        level = candle.levels[level_price]
        level.trades += 1

        if tick.is_buy:
            level.ask_volume += tick.quantity
            candle.total_ask += tick.quantity
        else:
            level.bid_volume += tick.quantity
            candle.total_bid += tick.quantity

        candle.total_trades += 1
        candle.close = tick.price
        if tick.price > candle.high:
            candle.high = tick.price
        if tick.price < candle.low:
            candle.low = tick.price

    def _close_candle(self) -> FootprintCandle:
        candle = self.current_candle
        self.cumulative_delta += candle.delta
        self.cvd_history.append(self.cumulative_delta)
        self.completed_candles.append(candle)
        logger.debug(
            f"Candle closed | O:{candle.open} H:{candle.high} L:{candle.low} C:{candle.close} "
            f"Delta:{candle.delta:+.2f} CVD:{self.cumulative_delta:+.2f} "
            f"Levels:{len(candle.levels)} Trades:{candle.total_trades}"
        )
        return candle

    def get_stacked_imbalances(self, candle: FootprintCandle) -> dict:
        """Detect stacked buy and sell imbalances in a candle."""
        sorted_prices = sorted(candle.levels.keys())
        buy_stacks = []
        sell_stacks = []
        current_buy = []
        current_sell = []

        for price in sorted_prices:
            level = candle.levels[price]
            if level.total_volume < self.imbalance_volume:
                if len(current_buy) >= self.stacked_min:
                    buy_stacks.append(list(current_buy))
                if len(current_sell) >= self.stacked_min:
                    sell_stacks.append(list(current_sell))
                current_buy = []
                current_sell = []
                continue

            if level.imbalance_ratio >= self.imbalance_ratio:
                current_buy.append(price)
            else:
                if len(current_buy) >= self.stacked_min:
                    buy_stacks.append(list(current_buy))
                current_buy = []

            if level.reverse_imbalance_ratio >= self.imbalance_ratio:
                current_sell.append(price)
            else:
                if len(current_sell) >= self.stacked_min:
                    sell_stacks.append(list(current_sell))
                current_sell = []

        if len(current_buy) >= self.stacked_min:
            buy_stacks.append(list(current_buy))
        if len(current_sell) >= self.stacked_min:
            sell_stacks.append(list(current_sell))

        return {"buy": buy_stacks, "sell": sell_stacks}

    def detect_absorption(self, candle: FootprintCandle) -> dict:
        """
        Detect absorption: extremely high volume at a price level where one side
        dominates but price barely moved past that level — indicating passive
        orders absorbing the aggression.
        """
        if candle.total_volume == 0 or len(candle.levels) < 3:
            return {"support": [], "resistance": []}

        avg_volume = candle.total_volume / len(candle.levels)
        volume_threshold = avg_volume * 5

        support_absorptions = []
        resistance_absorptions = []

        sorted_prices = sorted(candle.levels.keys())
        low_zone = sorted_prices[:max(len(sorted_prices) // 4, 1)]
        high_zone = sorted_prices[-max(len(sorted_prices) // 4, 1):]

        for price, level in candle.levels.items():
            if level.total_volume < volume_threshold:
                continue

            # Support absorption: heavy selling (bid_vol) near lows but price held
            if price in low_zone and level.bid_volume > level.ask_volume * 1.5:
                if candle.close > price:
                    support_absorptions.append({
                        "price": price, "bid_vol": level.bid_volume,
                        "ask_vol": level.ask_volume,
                        "strength": level.total_volume / avg_volume,
                    })

            # Resistance absorption: heavy buying (ask_vol) near highs but price rejected
            if price in high_zone and level.ask_volume > level.bid_volume * 1.5:
                if candle.close < price:
                    resistance_absorptions.append({
                        "price": price, "bid_vol": level.bid_volume,
                        "ask_vol": level.ask_volume,
                        "strength": level.total_volume / avg_volume,
                    })

        support_absorptions = sorted(support_absorptions, key=lambda x: x["strength"], reverse=True)[:2]
        resistance_absorptions = sorted(resistance_absorptions, key=lambda x: x["strength"], reverse=True)[:2]
        return {"support": support_absorptions, "resistance": resistance_absorptions}

    def detect_exhaustion(self, lookback: int = 5) -> Optional[dict]:
        """
        Detect exhaustion: high delta without price follow-through.
        Compares the latest candle's delta with the price movement
        relative to recent candles.
        """
        if len(self.completed_candles) < lookback + 1:
            return None

        recent = list(self.completed_candles)[-lookback:]
        latest = recent[-1]
        prior = recent[:-1]

        avg_delta = sum(abs(c.delta) for c in prior) / len(prior)
        avg_range = sum(c.high - c.low for c in prior) / len(prior)
        latest_range = latest.high - latest.low

        if avg_delta == 0 or avg_range == 0:
            return None

        delta_ratio = abs(latest.delta) / avg_delta
        range_ratio = latest_range / avg_range

        # High effort (delta) but low result (price range)
        if delta_ratio > 1.5 and range_ratio < 0.5:
            direction = "bull" if latest.delta > 0 else "bear"
            return {
                "type": f"exhaustion_{direction}",
                "delta_ratio": delta_ratio,
                "range_ratio": range_ratio,
                "price": latest.close,
                "strength": min(delta_ratio / range_ratio * 20, 100),
            }

        return None

    def get_cvd_trend(self, lookback: int = 10) -> dict:
        """Analyze CVD trend direction and strength."""
        if len(self.cvd_history) < lookback:
            return {"direction": "neutral", "strength": 0, "values": list(self.cvd_history)}

        values = list(self.cvd_history)[-lookback:]
        slope = (values[-1] - values[0]) / lookback

        if abs(slope) < 0.1:
            direction = "neutral"
        elif slope > 0:
            direction = "up"
        else:
            direction = "down"

        max_val = max(abs(v) for v in values) if values else 1
        strength = min(abs(slope) / max(max_val * 0.01, 0.001) * 50, 100)

        return {"direction": direction, "strength": strength, "slope": slope, "values": values}

    def get_last_n_candles(self, n: int) -> list[FootprintCandle]:
        return list(self.completed_candles)[-n:]

    @property
    def last_candle(self) -> Optional[FootprintCandle]:
        return self.completed_candles[-1] if self.completed_candles else None
