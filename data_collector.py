import json
import asyncio
import logging
from typing import Optional, Dict, Callable, Awaitable
import websockets
from models import RawTick
from config import Config

logger = logging.getLogger("collector")


class DataCollector:
    """
    Connects to Binance Futures combined WebSocket for real-time aggTrade data
    across multiple symbols simultaneously via a single connection.
    """

    def __init__(self, config: Config):
        self.config = config
        self.ws = None
        self._running = False
        self._reconnect_delay = 1
        self._max_reconnect_delay = 60
        self._tick_counts: Dict[str, int] = {}
        self._last_prices: Dict[str, float] = {}
        self._on_tick_callbacks: Dict[str, Callable] = {}
        self._ws_to_ccxt: Dict[str, str] = {}

        for symbol in config.symbol_list:
            ws_sym = Config.ccxt_to_ws(symbol)
            self._tick_counts[symbol] = 0
            self._last_prices[symbol] = 0.0
            self._ws_to_ccxt[ws_sym.upper()] = symbol

    def last_price(self, symbol: str) -> float:
        return self._last_prices.get(symbol, 0.0)

    def tick_count(self, symbol: str) -> int:
        return self._tick_counts.get(symbol, 0)

    @property
    def total_ticks(self) -> int:
        return sum(self._tick_counts.values())

    def set_tick_callback(self, symbol: str, callback: Callable):
        self._on_tick_callbacks[symbol] = callback

    async def start(self):
        self._running = True

        streams = []
        for symbol in self.config.symbol_list:
            ws_sym = Config.ccxt_to_ws(symbol)
            streams.append(f"{ws_sym}@aggTrade")

        if len(streams) == 1:
            url = f"{self.config.binance_ws_base}/{streams[0]}"
        else:
            combined = "/".join(streams)
            url = f"wss://fstream.binance.com/stream?streams={combined}"

        logger.info(f"Connecting to WebSocket for {len(streams)} market(s): {self.config.symbol_list}")

        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    self.ws = ws
                    self._reconnect_delay = 1
                    logger.info(f"WebSocket connected — streaming {len(streams)} market(s)")

                    async for message in ws:
                        if not self._running:
                            break
                        await self._process_message(message, use_combined=len(streams) > 1)

            except websockets.ConnectionClosed as e:
                logger.warning(f"WebSocket disconnected: {e}")
            except Exception as e:
                logger.error(f"WebSocket error: {e}")

            if self._running:
                logger.info(f"Reconnecting in {self._reconnect_delay}s...")
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)

    async def _process_message(self, raw: str, use_combined: bool = False):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        if use_combined:
            data = msg.get("data", {})
        else:
            data = msg

        if data.get("e") != "aggTrade":
            return

        ws_symbol = data.get("s", "")
        ccxt_symbol = self._ws_to_ccxt.get(ws_symbol)
        if not ccxt_symbol:
            return

        tick = RawTick(
            timestamp=int(data["T"]),
            price=float(data["p"]),
            quantity=float(data["q"]),
            is_buyer_maker=data["m"],
        )

        self._last_prices[ccxt_symbol] = tick.price
        self._tick_counts[ccxt_symbol] = self._tick_counts.get(ccxt_symbol, 0) + 1

        total = self._tick_counts[ccxt_symbol]
        if total % 5000 == 0:
            logger.info(f"[{ccxt_symbol}] {total} ticks | price: {tick.price}")

        callback = self._on_tick_callbacks.get(ccxt_symbol)
        if callback:
            await callback(tick)

    async def stop(self):
        self._running = False
        if self.ws:
            await self.ws.close()
        for sym, count in self._tick_counts.items():
            logger.info(f"[{sym}] Total ticks: {count}")
        logger.info("WebSocket closed")
