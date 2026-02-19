import logging
import asyncio
import aiohttp
from typing import Optional, List, Dict
from config import Config

logger = logging.getLogger("telegram")


class TelegramNotifier:
    """Envoie des alertes detaillees en francais sur Telegram."""

    def __init__(self, config: Config):
        self.bot_token = config.telegram_bot_token
        self.chat_id = config.telegram_chat_id
        self.enabled = bool(self.bot_token and self.chat_id)
        self._session: Optional[aiohttp.ClientSession] = None
        self._queue: asyncio.Queue = asyncio.Queue()

        if not self.enabled:
            logger.info("Telegram notifications disabled (no token/chat_id)")

    async def start(self):
        if not self.enabled:
            return
        self._session = aiohttp.ClientSession()
        await self.send(
            "*OrderFlow Trading Bot v3.0*\n"
            "Bot demarre avec succes.\n"
            "Tous les modules sont actifs.\n"
            "Surveillance des marches en cours..."
        )
        logger.info("Telegram notifier active")
        while True:
            msg = await self._queue.get()
            await self._do_send(msg)

    async def send(self, text: str, silent: bool = False):
        if not self.enabled:
            return
        await self._queue.put((text, silent))

    async def _do_send(self, item):
        text, silent = item
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        if len(text) > 4000:
            text = text[:4000] + "\n..."
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_notification": silent,
        }
        try:
            async with self._session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    body = await r.text()
                    logger.warning(f"Telegram send failed ({r.status}): {body}")
        except Exception as e:
            logger.warning(f"Telegram error: {e}")

    async def notify_trade_open(self, symbol: str, side: str, size: float,
                                entry: float, sl: float, tp: float, strategy: str,
                                context: Dict = None):
        icon = "📈" if side == "buy" else "📉"
        side_fr = "ACHAT" if side == "buy" else "VENTE"
        ctx = context or {}

        signals_text = ""
        for sig in ctx.get("signals", []):
            signals_text += f"  - {sig}\n"

        biases_text = ""
        for name, val in ctx.get("biases", {}).items():
            if val != 0:
                biases_text += f"  {name}: {val:+d}\n"

        llm_text = ""
        llm = ctx.get("llm")
        if llm and llm.get("reasoning"):
            llm_text = f"\n*Analyse IA:* {llm['reasoning']}"

        risk_reward = abs(tp - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0

        strat_fr = {"reversal": "Retournement", "breakout": "Cassure", "poc_reversion": "Retour au POC"}.get(strategy, strategy)
        regime_fr = {
            "ranging": "Range (lateral)", "trending_up": "Tendance haussiere",
            "trending_down": "Tendance baissiere", "volatile": "Volatile", "low_volume": "Faible volume"
        }.get(ctx.get("regime", ""), ctx.get("regime", "?"))

        cvd_dir = ctx.get("cvd_direction", "?")
        cvd_fr = {"up": "haussier", "down": "baissier", "neutral": "neutre"}.get(cvd_dir, cvd_dir)

        msg = (
            f"{icon} *TRADE OUVERT* `{symbol}`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Direction: *{side_fr}* | Strategie: *{strat_fr}*\n"
            f"Entree: `${entry:,.2f}`\n"
            f"Stop-Loss: `${sl:,.2f}` | Take-Profit: `${tp:,.2f}`\n"
            f"Taille: `{size:.6f}` | Risque/Recompense: `1:{risk_reward:.1f}`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"*Pourquoi ce trade:*\n"
            f"Score de confluence: *{ctx.get('score', 0):.0f}/100*\n"
            f"Regime de marche: {regime_fr}\n"
            f"CVD: {cvd_fr} (force {ctx.get('cvd_strength', 0):.0f})\n"
        )

        if signals_text:
            msg += f"\n*Signaux detectes:*\n{signals_text}"

        if biases_text:
            msg += f"\n*Ajustements du score:*\n{biases_text}"

        derivatives = ctx.get("derivatives", {})
        if derivatives:
            sent = ctx.get("sentiment", "?")
            sent_fr = {"bullish": "haussier", "bearish": "baissier", "neutral": "neutre"}.get(sent, sent)
            msg += (
                f"\n*Contexte du marche:*\n"
                f"  Taux de financement: {derivatives.get('funding_rate', 0)*100:.4f}%\n"
                f"  Ratio Long/Short: {derivatives.get('long_short_ratio', 0):.2f}\n"
                f"  Sentiment des news: {sent_fr}\n"
            )

        if llm_text:
            msg += llm_text

        await self.send(msg)

    async def notify_trade_close(self, symbol: str, side: str, entry: float,
                                 exit_price: float, pnl: float, pnl_pct: float,
                                 reason: str, context: Dict = None):
        won = pnl > 0
        icon = "✅" if won else "❌"
        side_fr = "ACHAT" if side == "buy" else "VENTE"
        ctx = context or {}

        duration = ctx.get("duration", "?")
        strat = ctx.get("strategy", "?")
        strat_fr = {"reversal": "Retournement", "breakout": "Cassure", "poc_reversion": "Retour au POC"}.get(strat, strat)

        reason_fr = {
            "Stop-loss hit": "Stop-Loss touche",
            "Take-profit hit": "Take-Profit atteint",
            "EMERGENCY_CLOSE": "Fermeture d'urgence",
            "Exchange SL/TP": "Ferme par l'exchange",
        }.get(reason, reason)

        msg = (
            f"{icon} *TRADE {'GAGNANT' if won else 'PERDANT'}* `{symbol}`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Direction: *{side_fr}* | Strategie: *{strat_fr}*\n"
            f"Entree: `${entry:,.2f}` → Sortie: `${exit_price:,.2f}`\n"
            f"Resultat: *${pnl:+,.2f}* ({pnl_pct:+.2f}%)\n"
            f"Raison de fermeture: *{reason_fr}*\n"
            f"Duree: {duration}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
        )

        if won:
            msg += (
                f"*Pourquoi ca a marche:*\n"
                f"  Les signaux de {strat_fr.lower()} etaient corrects.\n"
                f"  Le prix a bouge de ${abs(exit_price - entry):,.2f} en notre faveur.\n"
            )
            if "Take-profit" in reason:
                msg += f"  L'objectif de profit a ete atteint.\n"
            elif "trailing" in reason.lower():
                msg += f"  Le trailing stop a securise les profits.\n"
        else:
            msg += f"*Pourquoi ca a echoue:*\n"

            if "Stop-loss" in reason:
                msg += f"  Le prix s'est retourne contre nous et a touche le SL.\n"

            original_signals = ctx.get("original_signals", [])
            if original_signals:
                sigs_str = ", ".join(original_signals[:3])
                msg += f"  Signaux a l'entree: {sigs_str}\n"

            msg += f"  Les conditions du marche ont probablement change apres l'entree.\n"

        balance = ctx.get("balance", 0)
        if balance:
            msg += f"\n*Solde:* ${balance:,.2f}"

        daily_pnl = ctx.get("daily_pnl")
        if daily_pnl is not None:
            msg += f" | *PnL du jour:* ${daily_pnl:+,.2f}"

        await self.send(msg)

    async def notify_signal(self, symbol: str, signal_type: str, strength: float,
                            price: float, description: str):
        icon = "🟢" if "buy" in signal_type or "bull" in signal_type or "support" in signal_type else "🔴"
        await self.send(
            f"{icon} *Signal* `{symbol}`\n"
            f"`{signal_type}` ({strength:.0f}/100) @ ${price:,.2f}\n"
            f"_{description}_",
            silent=True,
        )

    async def notify_risk_alert(self, message: str):
        await self.send(f"⚠️ *ALERTE RISQUE*\n{message}")

    async def notify_daily_report(self, report: str):
        await self.send(f"📊 *Rapport Journalier*\n{report}")

    async def notify_health(self, message: str):
        await self.send(f"🔧 *Alerte Systeme*\n{message}")

    async def close(self):
        if self._session:
            await self._session.close()
