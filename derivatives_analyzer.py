import logging
import asyncio
import aiohttp
from typing import Dict, Optional
from config import Config

logger = logging.getLogger("derivatives")

BINANCE_FAPI = "https://fapi.binance.com"


class DerivativesAnalyzer:
    """
    Fetches funding rate, open interest, and long/short ratios
    from Binance Futures to enrich order flow signals.
    """

    def __init__(self, config: Config):
        self.config = config
        self._session: Optional[aiohttp.ClientSession] = None
        self._cache: Dict[str, dict] = {}
        self._running = False

    async def start(self):
        self._session = aiohttp.ClientSession()
        self._running = True
        logger.info("Derivatives analyzer started")
        while self._running:
            for symbol in self.config.symbol_list:
                await self._fetch_all(symbol)
            await asyncio.sleep(60)

    async def _fetch_all(self, symbol: str):
        binance_sym = Config.ccxt_to_ws(symbol).upper()
        data = {}
        try:
            fr = await self._get(f"{BINANCE_FAPI}/fapi/v1/premiumIndex", {"symbol": binance_sym})
            if fr:
                data["funding_rate"] = float(fr.get("lastFundingRate", 0))
                data["mark_price"] = float(fr.get("markPrice", 0))
                data["index_price"] = float(fr.get("indexPrice", 0))
                data["next_funding_time"] = int(fr.get("nextFundingTime", 0))

            oi = await self._get(f"{BINANCE_FAPI}/fapi/v1/openInterest", {"symbol": binance_sym})
            if oi:
                data["open_interest"] = float(oi.get("openInterest", 0))

            ls = await self._get(f"{BINANCE_FAPI}/futures/data/globalLongShortAccountRatio",
                                 {"symbol": binance_sym, "period": "5m", "limit": 1})
            if ls and isinstance(ls, list) and ls:
                data["long_short_ratio"] = float(ls[0].get("longShortRatio", 1.0))
                data["long_account_pct"] = float(ls[0].get("longAccount", 0.5)) * 100
                data["short_account_pct"] = float(ls[0].get("shortAccount", 0.5)) * 100

            tls = await self._get(f"{BINANCE_FAPI}/futures/data/topLongShortPositionRatio",
                                  {"symbol": binance_sym, "period": "5m", "limit": 1})
            if tls and isinstance(tls, list) and tls:
                data["top_trader_ls_ratio"] = float(tls[0].get("longShortRatio", 1.0))

        except Exception as e:
            logger.warning(f"[{symbol}] Derivatives fetch error: {e}")

        if data:
            self._cache[symbol] = data

    async def _get(self, url: str, params: dict):
        try:
            async with self._session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    return await r.json()
                return None
        except Exception:
            return None

    def get_data(self, symbol: str) -> dict:
        return self._cache.get(symbol, {})

    def get_signal_bias(self, symbol: str) -> dict:
        """
        Returns a bias adjustment based on derivatives data.
        positive bias = bullish confirmation, negative = bearish.
        """
        data = self.get_data(symbol)
        if not data:
            return {"bias": 0, "reasons": []}

        bias = 0
        reasons = []

        fr = data.get("funding_rate", 0)
        if fr > 0.001:
            bias -= 10
            reasons.append(f"High funding rate ({fr*100:.3f}%) — longs overcrowded")
        elif fr < -0.001:
            bias += 10
            reasons.append(f"Negative funding ({fr*100:.3f}%) — shorts overcrowded")

        ls = data.get("long_short_ratio", 1.0)
        if ls > 2.0:
            bias -= 8
            reasons.append(f"L/S ratio extreme ({ls:.2f}) — too many longs")
        elif ls < 0.5:
            bias += 8
            reasons.append(f"L/S ratio low ({ls:.2f}) — too many shorts")

        tls = data.get("top_trader_ls_ratio", 1.0)
        if tls > 2.5:
            bias -= 5
            reasons.append(f"Top traders heavy long ({tls:.2f})")
        elif tls < 0.4:
            bias += 5
            reasons.append(f"Top traders heavy short ({tls:.2f})")

        return {"bias": bias, "reasons": reasons}

    async def stop(self):
        self._running = False
        if self._session:
            await self._session.close()
