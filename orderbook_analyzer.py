import logging
import asyncio
import json
from typing import Dict, Optional
import websockets
from config import Config

logger = logging.getLogger("orderbook")


class OrderBookAnalyzer:
    """
    Analyzes L2 order book depth to detect:
    - Large bid/ask walls (support/resistance)
    - Iceberg orders (large hidden liquidity)
    - Book imbalance (directional bias)
    """

    def __init__(self, config: Config):
        self.config = config
        self._books: Dict[str, dict] = {}
        self._analysis: Dict[str, dict] = {}
        self._running = False

    async def start(self):
        self._running = True
        streams = []
        for symbol in self.config.symbol_list:
            ws_sym = Config.ccxt_to_ws(symbol)
            streams.append(f"{ws_sym}@depth20@500ms")

        if len(streams) == 1:
            url = f"{self.config.binance_ws_base}/{streams[0]}"
        else:
            url = f"wss://fstream.binance.com/stream?streams={'/'.join(streams)}"

        logger.info(f"Order book analyzer: streaming {len(streams)} L2 books")

        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    async for message in ws:
                        if not self._running:
                            break
                        await self._process(message, len(streams) > 1)
            except Exception as e:
                if self._running:
                    logger.warning(f"Order book WS error: {e}")
                    await asyncio.sleep(5)

    async def _process(self, raw: str, combined: bool):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        data = msg.get("data", msg) if combined else msg
        symbol_ws = data.get("s", "")

        if not symbol_ws:
            stream = msg.get("stream", "")
            symbol_ws = stream.split("@")[0].upper() if stream else ""

        ccxt_sym = None
        for sym in self.config.symbol_list:
            if Config.ccxt_to_ws(sym).upper() == symbol_ws:
                ccxt_sym = sym
                break

        if not ccxt_sym:
            return

        bids = [(float(p), float(q)) for p, q in data.get("b", data.get("bids", []))]
        asks = [(float(p), float(q)) for p, q in data.get("a", data.get("asks", []))]

        if not bids or not asks:
            return

        self._books[ccxt_sym] = {"bids": bids, "asks": asks}
        self._analyze(ccxt_sym, bids, asks)

    def _analyze(self, symbol: str, bids: list, asks: list):
        total_bid = sum(q for _, q in bids)
        total_ask = sum(q for _, q in asks)
        total = total_bid + total_ask

        if total == 0:
            return

        imbalance = (total_bid - total_ask) / total

        bid_walls = []
        ask_walls = []
        avg_bid = total_bid / max(len(bids), 1)
        avg_ask = total_ask / max(len(asks), 1)

        for price, qty in bids:
            if qty > avg_bid * 4:
                bid_walls.append({"price": price, "quantity": qty, "ratio": qty / avg_bid})
        for price, qty in asks:
            if qty > avg_ask * 4:
                ask_walls.append({"price": price, "quantity": qty, "ratio": qty / avg_ask})

        spread = asks[0][0] - bids[0][0] if asks and bids else 0
        mid_price = (asks[0][0] + bids[0][0]) / 2 if asks and bids else 0

        self._analysis[symbol] = {
            "imbalance": round(imbalance, 4),
            "total_bid": round(total_bid, 2),
            "total_ask": round(total_ask, 2),
            "spread": round(spread, 4),
            "mid_price": round(mid_price, 2),
            "bid_walls": bid_walls[:3],
            "ask_walls": ask_walls[:3],
            "bid_wall_count": len(bid_walls),
            "ask_wall_count": len(ask_walls),
        }

    def get_analysis(self, symbol: str) -> dict:
        return self._analysis.get(symbol, {})

    def get_signal_bias(self, symbol: str) -> int:
        """Positive = more bids (bullish), negative = more asks (bearish)."""
        data = self._analysis.get(symbol, {})
        imb = data.get("imbalance", 0)
        bid_walls = data.get("bid_wall_count", 0)
        ask_walls = data.get("ask_wall_count", 0)

        bias = 0
        if imb > 0.15:
            bias += 5
        elif imb < -0.15:
            bias -= 5

        if bid_walls > ask_walls + 1:
            bias += 3
        elif ask_walls > bid_walls + 1:
            bias -= 3

        return bias

    async def stop(self):
        self._running = False
