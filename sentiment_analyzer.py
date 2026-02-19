import logging
import asyncio
import aiohttp
import re
from typing import Dict, Optional
from config import Config

logger = logging.getLogger("sentiment")

CRYPTO_NEWS_URL = "https://min-api.cryptocompare.com/data/v2/news/?lang=EN&sortOrder=latest"

BULLISH_WORDS = {
    "surge", "rally", "bullish", "breakout", "soar", "pump", "highs", "adoption",
    "institutional", "etf", "approval", "upgrade", "partnership", "accumulation",
    "buy", "moon", "growth", "gain", "profit", "recover", "support",
}
BEARISH_WORDS = {
    "crash", "dump", "bearish", "plunge", "sell", "hack", "ban", "regulation",
    "sec", "lawsuit", "fraud", "collapse", "liquidation", "fear", "decline",
    "drop", "loss", "risk", "warning", "concern", "investigation", "vulnerability",
}


class SentimentAnalyzer:
    """
    Analyzes crypto news sentiment using keyword scoring.
    Optional: integrate OpenAI/Claude for deeper analysis.
    """

    def __init__(self, config: Config):
        self.config = config
        self._session: Optional[aiohttp.ClientSession] = None
        self._cache: Dict[str, dict] = {}
        self._running = False
        self.openai_key = config.openai_api_key

    async def start(self):
        self._session = aiohttp.ClientSession()
        self._running = True
        logger.info("Sentiment analyzer started" + (" (with AI)" if self.openai_key else " (keyword mode)"))
        while self._running:
            await self._analyze_news()
            await asyncio.sleep(120)

    async def _analyze_news(self):
        try:
            async with self._session.get(CRYPTO_NEWS_URL, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200:
                    return
                data = await r.json()

            articles = data.get("Data", [])[:20]
            symbol_mentions = {}

            for article in articles:
                title = article.get("title", "").lower()
                body = article.get("body", "").lower()[:500]
                text = title + " " + body
                categories = article.get("categories", "").upper()

                for symbol in self.config.symbol_list:
                    coin = symbol.split("/")[0]
                    if coin.lower() in text or coin in categories:
                        if coin not in symbol_mentions:
                            symbol_mentions[coin] = []
                        score = self._score_text(text)
                        symbol_mentions[coin].append({
                            "title": article.get("title", ""),
                            "score": score,
                            "source": article.get("source", ""),
                        })

            for symbol in self.config.symbol_list:
                coin = symbol.split("/")[0]
                mentions = symbol_mentions.get(coin, [])
                if mentions:
                    avg_score = sum(m["score"] for m in mentions) / len(mentions)
                    self._cache[symbol] = {
                        "score": avg_score,
                        "mentions": len(mentions),
                        "latest_headline": mentions[0]["title"],
                        "label": "bullish" if avg_score > 0.2 else "bearish" if avg_score < -0.2 else "neutral",
                    }
                else:
                    self._cache[symbol] = {"score": 0, "mentions": 0, "latest_headline": "", "label": "neutral"}

        except Exception as e:
            logger.warning(f"Sentiment fetch error: {e}")

    def _score_text(self, text: str) -> float:
        words = set(re.findall(r'\w+', text.lower()))
        bull_count = len(words & BULLISH_WORDS)
        bear_count = len(words & BEARISH_WORDS)
        total = bull_count + bear_count
        if total == 0:
            return 0.0
        return (bull_count - bear_count) / total

    async def get_ai_sentiment(self, symbol: str, headlines: list) -> Optional[float]:
        """Optional: use OpenAI for deeper sentiment analysis."""
        if not self.openai_key:
            return None
        try:
            prompt = (
                f"Rate the overall crypto market sentiment for {symbol.split('/')[0]} "
                f"based on these headlines on a scale from -1.0 (very bearish) to +1.0 (very bullish). "
                f"Return ONLY a number.\n\nHeadlines:\n" +
                "\n".join(f"- {h}" for h in headlines[:10])
            )
            async with self._session.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.openai_key}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 10,
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    text = data["choices"][0]["message"]["content"].strip()
                    return float(text)
        except Exception as e:
            logger.debug(f"AI sentiment error: {e}")
        return None

    def get_data(self, symbol: str) -> dict:
        return self._cache.get(symbol, {"score": 0, "mentions": 0, "label": "neutral"})

    def get_signal_bias(self, symbol: str) -> int:
        data = self.get_data(symbol)
        score = data.get("score", 0)
        if score > 0.3:
            return 8
        elif score < -0.3:
            return -8
        return 0

    async def stop(self):
        self._running = False
        if self._session:
            await self._session.close()
