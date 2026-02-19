import logging
import aiohttp
from typing import Optional
from config import Config

logger = logging.getLogger("llm")

SYSTEM_PROMPT = """You are an expert crypto futures trader specializing in order flow analysis.
You receive a full market context snapshot and must decide: should this trade be taken?

Respond in EXACTLY this JSON format (nothing else):
{"decision": "take" or "skip", "confidence": 0-100, "reasoning": "one sentence"}

Rules:
- If signals conflict strongly, say "skip"
- If funding rate is extreme and against the trade direction, say "skip"
- Weight absorption and delta divergence heavily — they indicate institutional activity
- A negative CVD with a BUY signal is very risky unless there's strong absorption support
- Be conservative: when in doubt, skip"""


class LLMAnalyzer:
    """
    Uses GPT-4o-mini to synthesize all market data into a contextual
    trade decision. Acts as a final "human-like" filter before execution.
    """

    def __init__(self, config: Config):
        self.api_key = config.openai_api_key
        self.enabled = bool(self.api_key)
        self._session: Optional[aiohttp.ClientSession] = None
        self._call_count = 0

        if not self.enabled:
            logger.info("LLM analyzer disabled (no OpenAI key)")

    async def _ensure_session(self):
        if not self._session:
            self._session = aiohttp.ClientSession()

    async def analyze_trade(self, context: dict) -> dict:
        """
        Analyze a trade opportunity with full market context.
        Returns {"decision": "take"/"skip", "confidence": 0-100, "reasoning": str}
        """
        if not self.enabled:
            return {"decision": "take", "confidence": 50, "reasoning": "LLM disabled"}

        await self._ensure_session()

        prompt = self._build_prompt(context)

        try:
            async with self._session.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 150,
                    "temperature": 0.1,
                },
                timeout=aiohttp.ClientTimeout(total=8),
            ) as r:
                if r.status != 200:
                    return {"decision": "take", "confidence": 50, "reasoning": f"API error {r.status}"}

                data = await r.json()
                text = data["choices"][0]["message"]["content"].strip()
                self._call_count += 1

                import json
                try:
                    result = json.loads(text)
                    decision = result.get("decision", "take")
                    confidence = int(result.get("confidence", 50))
                    reasoning = result.get("reasoning", "")

                    logger.info(
                        f"LLM #{self._call_count}: {decision} ({confidence}%) — {reasoning}"
                    )
                    return {"decision": decision, "confidence": confidence, "reasoning": reasoning}
                except json.JSONDecodeError:
                    logger.warning(f"LLM returned non-JSON: {text[:100]}")
                    return {"decision": "take", "confidence": 50, "reasoning": "Parse error"}

        except Exception as e:
            logger.warning(f"LLM call failed: {e}")
            return {"decision": "take", "confidence": 50, "reasoning": str(e)}

    def _build_prompt(self, ctx: dict) -> str:
        lines = [
            f"## Trade Opportunity: {ctx.get('side', '?').upper()} {ctx.get('symbol', '?')}",
            f"Strategy: {ctx.get('strategy', '?')} | Confluence score: {ctx.get('score', 0):.0f}/100",
            f"Entry: ${ctx.get('entry', 0):,.2f} | SL: ${ctx.get('sl', 0):,.2f} | TP: ${ctx.get('tp', 0):,.2f}",
            "",
            "## Order Flow Signals",
        ]

        for sig in ctx.get("signals", []):
            lines.append(f"- {sig}")

        lines.extend([
            "",
            "## Market Context",
            f"Regime: {ctx.get('regime', '?')}",
            f"CVD trend: {ctx.get('cvd_direction', '?')} (strength {ctx.get('cvd_strength', 0):.0f})",
            f"Cumulative delta: {ctx.get('cvd', 0):+.2f}",
            f"Session: {ctx.get('session', '?')} (quality: {ctx.get('session_quality', '?')})",
            "",
            "## Derivatives",
            f"Funding rate: {ctx.get('funding_rate', 0)*100:.4f}%",
            f"Open interest: {ctx.get('open_interest', 0):,.0f}",
            f"Long/Short ratio: {ctx.get('ls_ratio', 0):.2f}",
            "",
            "## Sentiment & AI",
            f"News sentiment: {ctx.get('sentiment_label', '?')} (score {ctx.get('sentiment_score', 0):.2f})",
            f"AI prediction: {ctx.get('ai_direction', '?')} ({ctx.get('ai_confidence', 0)}%)",
            "",
            "## Volume Profile",
            f"Daily POC: ${ctx.get('daily_poc', 0):,.2f}",
            f"Price vs POC: {ctx.get('price_vs_poc', '?')}",
            "",
            "## Order Book",
            f"Book imbalance: {ctx.get('book_imbalance', 0):+.3f}",
            f"Bid walls: {ctx.get('bid_walls', 0)} | Ask walls: {ctx.get('ask_walls', 0)}",
            "",
            "## Trade History on this symbol",
            f"Win rate: {ctx.get('symbol_winrate', 0):.0f}% over {ctx.get('symbol_trades', 0)} trades",
            f"This signal combo win rate: {ctx.get('combo_winrate', 0):.0f}% over {ctx.get('combo_trades', 0)} trades",
        ])

        sweep = ctx.get("sweep")
        if sweep:
            lines.extend(["", f"## Liquidity Sweep Detected", f"{sweep}"])

        return "\n".join(lines)

    async def close(self):
        if self._session:
            await self._session.close()
