import asyncio
import signal
import logging
import time
import uvicorn
from typing import Dict
from rich.logging import RichHandler

from config import Config
from models import RawTick, FootprintCandle
from database import DatabaseManager
from footprint_engine import FootprintEngine
from signal_detector import SignalDetector
from strategy_engine import StrategyEngine
from risk_manager import RiskManager
from execution_engine import ExecutionEngine
from data_collector import DataCollector
from telegram_notifier import TelegramNotifier
from derivatives_analyzer import DerivativesAnalyzer
from regime_detector import RegimeDetector
from trailing_stop import TrailingStopManager
from sentiment_analyzer import SentimentAnalyzer
from health_monitor import HealthMonitor
from daily_report import DailyReporter
from multi_timeframe import MultiTimeframeAnalyzer
from ai_predictor import LSTMPredictor
from liquidity_sweep import LiquiditySweepDetector
from correlation_filter import CorrelationFilter
from orderbook_analyzer import OrderBookAnalyzer
from kill_switch import KillSwitch
from dynamic_sizing import DynamicSizer
from session_filter import SessionFilter
from trade_learner import TradeLearner
from volume_profile import MultiPeriodProfile
from llm_analyzer import LLMAnalyzer
from dashboard import app, init_dashboard

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(rich_tracebacks=True)],
)
logger = logging.getLogger("bot")


class MarketPipeline:
    """One independent analysis pipeline per symbol with all AI modules."""

    def __init__(self, symbol: str, config: Config):
        self.symbol = symbol
        self.config = config
        self.footprint = FootprintEngine(config, symbol)
        self.signals = SignalDetector(self.footprint)
        self.strategy = StrategyEngine(config, self.footprint)
        self.regime = RegimeDetector()
        self.mtf = MultiTimeframeAnalyzer(config, symbol)
        self.predictor = LSTMPredictor()
        self.liquidity = LiquiditySweepDetector()
        sym_cfg = config.get_symbol_config(symbol)
        self.volume_profile = MultiPeriodProfile(sym_cfg.get("scale", 0.5))
        self.candle_count = 0
        self.last_trade_candle = 0


class OrderFlowBot:
    """Main orchestrator — manages all modules and market pipelines."""

    def __init__(self):
        self.config = Config()
        self.db = DatabaseManager()
        self.risk = RiskManager(self.config, self.db)
        self.execution = ExecutionEngine(self.config, self.db)
        self.collector = DataCollector(self.config)

        # Modules
        self.telegram = TelegramNotifier(self.config)
        self.derivatives = DerivativesAnalyzer(self.config)
        self.sentiment = SentimentAnalyzer(self.config)
        self.health = HealthMonitor()
        self.daily_report = DailyReporter(self.db)
        self.trailing = TrailingStopManager(
            self.config.trailing_activation_pct,
            self.config.trailing_distance_pct,
        )
        self.correlation = CorrelationFilter()
        self.orderbook = OrderBookAnalyzer(self.config)
        self.kill_switch = KillSwitch()
        self.dynamic_sizer = DynamicSizer(self.config.risk_per_trade_pct)
        self.session = SessionFilter()
        self.learner = TradeLearner(self.db)
        self.llm = LLMAnalyzer(self.config)

        self.pipelines: Dict[str, MarketPipeline] = {}
        self._running = False
        self._start_time = 0

        self.bot_state = {
            "running": False,
            "mode": self.config.trading_mode.value,
            "symbol": ", ".join(self.config.symbol_list),
            "last_price": 0, "tick_count": 0, "candles_count": 0,
            "cvd": 0, "last_delta": 0, "balance": 0, "uptime": "0s",
            "emergency_close": False, "markets": {},
            "derivatives": {}, "sentiment": {}, "regimes": {},
            "health": {}, "ai_predictions": {},
            "correlations": {}, "orderbook": {}, "kill_switch": {},
            "session": {}, "dynamic_sizing": {},
            "learner": {}, "volume_profiles": {}, "llm_calls": 0,
        }

    async def start(self):
        symbols = self.config.symbol_list

        logger.info("=" * 60)
        logger.info("   ORDERFLOW TRADING BOT v3.0 — STARTING")
        logger.info(f"   Port: {self.config.effective_port}")
        logger.info("=" * 60)

        # Start dashboard IMMEDIATELY for Railway healthcheck
        init_dashboard(self.db, self.config, self.bot_state)

        def _shutdown(sig, frame):
            self._running = False

        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

        tasks = [
            asyncio.create_task(self._run_dashboard()),
            asyncio.create_task(self._delayed_init(symbols)),
        ]

        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            logger.error(f"Fatal error: {e}")
        finally:
            await self._shutdown()

    async def _delayed_init(self, symbols):
        """Heavy initialization runs AFTER the web server is up."""
        await asyncio.sleep(2)

        logger.info(f"Initializing bot: {len(symbols)} markets, mode={self.config.trading_mode.value}")

        try:
            await self.db.initialize()
        except Exception as e:
            logger.error(f"DB init failed: {e}")

        try:
            await self.execution.initialize()
            balance = await self.execution.get_balance()
            await self.risk.update_balance(balance)
            self.bot_state["balance"] = balance
        except Exception as e:
            logger.error(f"Execution init failed: {e}")

        try:
            await self.learner.load_history()
        except Exception as e:
            logger.error(f"Learner init failed: {e}")

        try:
            open_positions = await self.db.get_open_positions()
            saved_contexts = await self.db.get_all_open_contexts()
            self.bot_state["_trade_contexts"] = saved_contexts
            if open_positions:
                logger.info(f"Restored {len(open_positions)} open position(s)")
        except Exception as e:
            logger.error(f"Position restore failed: {e}")

        for symbol in symbols:
            pipeline = MarketPipeline(symbol, self.config)
            self.pipelines[symbol] = pipeline
            self.collector.set_tick_callback(symbol, lambda tick, s=symbol: self._on_tick(s, tick))
            self.bot_state["markets"][symbol] = {"last_price": 0, "ticks": 0, "candles": 0, "delta": 0, "cvd": 0}

        self.health.set_notify_callback(self.telegram.notify_health)
        self.daily_report.set_notify_callback(self.telegram.notify_daily_report)
        self.kill_switch.set_notify_callback(self.telegram.notify_risk_alert)

        self._running = True
        self._start_time = time.time()
        self.bot_state["running"] = True

        logger.info(f"All modules ready — starting trading on {symbols}")

        bg_tasks = [
            asyncio.create_task(self.collector.start()),
            asyncio.create_task(self._position_monitor()),
            asyncio.create_task(self._state_updater()),
            asyncio.create_task(self.derivatives.start()),
            asyncio.create_task(self.sentiment.start()),
            asyncio.create_task(self.health.start()),
            asyncio.create_task(self.daily_report.start()),
            asyncio.create_task(self.telegram.start()),
            asyncio.create_task(self.orderbook.start()),
        ]

        await asyncio.gather(*bg_tasks, return_exceptions=True)

    async def _on_tick(self, symbol: str, tick: RawTick):
        pipeline = self.pipelines.get(symbol)
        if not pipeline:
            return

        self.health.record_tick()
        self.correlation.record_price(symbol, tick.price)

        pipeline.mtf.process_tick(tick)
        completed = pipeline.footprint.process_tick(tick)
        if completed is not None:
            await self._on_candle_close(symbol, pipeline, completed)

    async def _on_candle_close(self, symbol: str, pipeline: MarketPipeline,
                               candle: FootprintCandle):
        pipeline.candle_count += 1
        pipeline.volume_profile.add_candle(candle)
        logger.info(
            f"━━━ [{symbol}] CANDLE #{pipeline.candle_count} ━━━ "
            f"O:{candle.open:.2f} H:{candle.high:.2f} L:{candle.low:.2f} C:{candle.close:.2f} | "
            f"Δ:{candle.delta:+.2f} | Vol:{candle.total_volume:.2f}"
        )

        # 1. Kill switch check
        ks = self.kill_switch.should_allow_trade()
        if not ks["allowed"]:
            logger.warning(f"[{symbol}] {ks['reason']}")
            return

        # 2. Session filter check
        sess = self.session.should_trade()
        if not sess["allowed"]:
            logger.info(f"[{symbol}] Session blocked: {sess['reason']}")
            return

        # 3. Cooldown between trades (3 candles = 15 min)
        candles_since_trade = pipeline.candle_count - pipeline.last_trade_candle
        if pipeline.last_trade_candle > 0 and candles_since_trade < 3:
            logger.info(f"[{symbol}] Cooldown: {3 - candles_since_trade} candle(s) remaining")
            return

        # 4. Regime detection
        all_candles = pipeline.footprint.get_last_n_candles(30)
        regime = pipeline.regime.analyze(all_candles)
        guidance = pipeline.regime.get_strategy_guidance()

        if not pipeline.regime.should_trade():
            logger.info(f"[{symbol}] Regime: {regime.value} — NO TRADE")
            return

        # 4. Update liquidity levels and detect sweeps
        pipeline.liquidity.update_swings(all_candles)
        prev = all_candles[-2] if len(all_candles) >= 2 else None
        sweeps = pipeline.liquidity.detect(candle, prev)
        for sw in sweeps:
            logger.info(f"[{symbol}] SWEEP: {sw['description']}")
            await self.db.log_signal(
                signal_type=sw["type"], strength=sw["strength"],
                price=sw["level"], timestamp=candle.timestamp,
                description=f"[{symbol}] {sw['description']}",
            )

        # 5. Detect order flow signals
        detected_signals = pipeline.signals.analyze(candle)

        for sig in detected_signals:
            await self.db.log_signal(
                signal_type=sig.type.value, strength=sig.strength,
                price=sig.price, timestamp=sig.timestamp,
                description=f"[{symbol}] {sig.description}",
            )

        if not detected_signals and not sweeps:
            return

        # 6. Strategy evaluation
        trade_signal = pipeline.strategy.evaluate(candle, detected_signals)
        if trade_signal is None:
            return

        # 6b. Block strategy if regime says avoid it
        avoided = guidance.get("avoid")
        if avoided and trade_signal.strategy.value == avoided:
            logger.info(f"[{symbol}] Strategy '{trade_signal.strategy.value}' blocked by regime '{regime.value}'")
            return

        # 6c. Block trades against strong CVD trend
        cvd_trend = pipeline.footprint.get_cvd_trend(10)
        if trade_signal.side.value == "buy" and cvd_trend["direction"] == "down" and cvd_trend["strength"] > 50:
            logger.info(f"[{symbol}] BUY blocked: CVD strongly bearish (strength={cvd_trend['strength']:.0f})")
            return
        if trade_signal.side.value == "sell" and cvd_trend["direction"] == "up" and cvd_trend["strength"] > 50:
            logger.info(f"[{symbol}] SELL blocked: CVD strongly bullish (strength={cvd_trend['strength']:.0f})")
            return

        # 7. Apply biases from ALL modules
        original_score = trade_signal.confluence_score
        signal_types = [s.type.value for s in detected_signals]

        deriv_bias = self.derivatives.get_signal_bias(symbol)
        sent_bias = self.sentiment.get_signal_bias(symbol)
        htf_data = pipeline.mtf.get_htf_bias()
        ai_bias = pipeline.predictor.get_signal_bias(all_candles)
        ob_bias = self.orderbook.get_signal_bias(symbol)
        vp_data = pipeline.volume_profile.get_combined_bias(candle.close)
        learner_data = self.learner.get_signal_combo_bias(symbol, signal_types)
        strat_bias = self.learner.get_strategy_bias(trade_signal.strategy.value)
        sym_bias = self.learner.get_symbol_bias(symbol)

        sweep_bias = 0
        for sw in sweeps:
            if sw["type"] == "stop_hunt_bull" and trade_signal.side.value == "buy":
                sweep_bias += 12
            elif sw["type"] == "stop_hunt_bear" and trade_signal.side.value == "sell":
                sweep_bias += 12

        total_bias = (
            deriv_bias.get("bias", 0) + sent_bias + htf_data.get("bias", 0)
            + ai_bias + ob_bias + sweep_bias
            + vp_data.get("bias", 0) + learner_data.get("bias", 0)
            + strat_bias + sym_bias
        )

        if trade_signal.side.value == "buy":
            trade_signal.confluence_score = max(0, min(100, original_score + total_bias))
        else:
            trade_signal.confluence_score = max(0, min(100, original_score - total_bias))

        logger.info(
            f"[{symbol}] Score: {original_score:.0f} → {trade_signal.confluence_score:.0f} "
            f"(deriv={deriv_bias.get('bias', 0):+d} sent={sent_bias:+d} "
            f"htf={htf_data.get('bias', 0):+d} ai={ai_bias:+d} "
            f"ob={ob_bias:+d} sweep={sweep_bias:+d} "
            f"vp={vp_data.get('bias', 0):+d} learn={learner_data.get('bias', 0):+d} "
            f"strat={strat_bias:+d} sym={sym_bias:+d})"
        )

        if trade_signal.confluence_score < self.config.min_confluence_score:
            return

        # 7b. LLM final filter
        skip_check = self.learner.should_skip_symbol(symbol)
        if skip_check["skip"]:
            logger.warning(f"[{symbol}] Learner recommends skipping: {skip_check['reason']}")
            return

        cvd_trend = pipeline.footprint.get_cvd_trend(10)
        sent_data = self.sentiment.get_data(symbol)
        deriv_data = self.derivatives.get_data(symbol)
        pred = pipeline.predictor.predict(all_candles)
        ob_data = self.orderbook.get_analysis(symbol)
        vp_analysis = pipeline.volume_profile.daily.get_value_area()
        sym_stats = self.learner._symbol_stats.get(symbol, {})
        combo_stats = learner_data

        llm_ctx = {
            "symbol": symbol, "side": trade_signal.side.value,
            "strategy": trade_signal.strategy.value,
            "score": trade_signal.confluence_score,
            "entry": trade_signal.entry_price, "sl": trade_signal.stop_loss, "tp": trade_signal.take_profit,
            "signals": [s.description for s in detected_signals],
            "regime": regime.value,
            "cvd_direction": cvd_trend["direction"], "cvd_strength": cvd_trend["strength"],
            "cvd": pipeline.footprint.cumulative_delta,
            "session": self.session.get_current_session().get("session", "?"),
            "session_quality": self.session.get_current_session().get("quality", "?"),
            "funding_rate": deriv_data.get("funding_rate", 0),
            "open_interest": deriv_data.get("open_interest", 0),
            "ls_ratio": deriv_data.get("long_short_ratio", 0),
            "sentiment_label": sent_data.get("label", "?"),
            "sentiment_score": sent_data.get("score", 0),
            "ai_direction": pred.get("direction", "?"), "ai_confidence": pred.get("confidence", 0),
            "daily_poc": vp_analysis.get("poc", 0),
            "price_vs_poc": vp_data.get("daily", {}).get("reason", "?"),
            "book_imbalance": ob_data.get("imbalance", 0),
            "bid_walls": ob_data.get("bid_wall_count", 0),
            "ask_walls": ob_data.get("ask_wall_count", 0),
            "symbol_winrate": sym_stats.get("wins", 0) / max(sym_stats.get("trades", 1), 1) * 100,
            "symbol_trades": sym_stats.get("trades", 0),
            "combo_winrate": combo_stats.get("win_rate", 0),
            "combo_trades": combo_stats.get("trades", 0),
            "sweep": sweeps[0]["description"] if sweeps else None,
        }
        llm_result = await self.llm.analyze_trade(llm_ctx)
        if llm_result["decision"] == "skip" and llm_result["confidence"] >= 85:
            logger.info(f"[{symbol}] LLM says SKIP ({llm_result['confidence']}%): {llm_result['reasoning']}")
            return
        elif llm_result["decision"] == "skip":
            logger.info(f"[{symbol}] LLM advises caution ({llm_result['confidence']}%) but allowing trade")

        # 8. Correlation filter
        open_positions = await self.execution.sync_positions()
        self.correlation.update_correlations()
        corr_check = self.correlation.should_block_trade(symbol, trade_signal.side.value, open_positions)
        if corr_check["blocked"]:
            logger.warning(f"[{symbol}] Blocked by correlation: {corr_check['reason']}")
            return

        # 9. Dynamic sizing + regime + session multipliers
        size_mult = guidance.get("size_mult", 1.0)
        session_mult = self.session.get_size_multiplier()
        kelly_data = self.dynamic_sizer.get_risk_multiplier()
        kelly_mult = kelly_data["multiplier"]

        total_mult = size_mult * session_mult * kelly_mult
        logger.info(
            f"[{symbol}] Size mults: regime={size_mult} session={session_mult:.1f} "
            f"kelly={kelly_mult:.2f} → total={total_mult:.2f}"
        )

        # 10. Risk check
        validation = await self.risk.validate_trade(trade_signal, open_positions, symbol)
        if not validation["approved"]:
            logger.warning(f"[{symbol}] Rejected: {validation['reason']}")
            return

        adjusted_size = validation["size"] * total_mult

        # 11. Execute
        position = await self.execution.open_position(trade_signal, adjusted_size, symbol)
        if position:
            pipeline.last_trade_candle = pipeline.candle_count
            logger.info(f"[{symbol}] POSITION OPENED: {position.id} {position.side.value.upper()} {position.size:.6f}")

            open_context = {
                "score": trade_signal.confluence_score,
                "regime": regime.value,
                "cvd_direction": cvd_trend["direction"],
                "cvd_strength": cvd_trend["strength"],
                "signals": [s.description for s in detected_signals],
                "biases": {
                    "derivatives": deriv_bias.get("bias", 0),
                    "sentiment": sent_bias,
                    "multi-TF": htf_data.get("bias", 0),
                    "AI predict": ai_bias,
                    "orderbook": ob_bias,
                    "sweep": sweep_bias,
                    "vol profile": vp_data.get("bias", 0),
                    "learner": learner_data.get("bias", 0),
                    "strategy hist": strat_bias,
                    "symbol hist": sym_bias,
                },
                "derivatives": deriv_data,
                "sentiment": sent_data.get("label", "?"),
                "llm": llm_result,
            }

            # Store context for close notification — in memory AND in DB
            trade_ctx = {
                **open_context,
                "strategy": position.strategy.value,
                "original_signals": [s.type.value for s in detected_signals],
                "open_time": time.time(),
            }
            self.bot_state.setdefault("_trade_contexts", {})[position.id] = trade_ctx
            await self.db.save_trade_context(position.id, trade_ctx)

            await self.telegram.notify_trade_open(
                symbol, position.side.value, position.size,
                position.entry_price, position.stop_loss, position.take_profit,
                position.strategy.value, open_context,
            )

            balance = await self.execution.get_balance()
            await self.risk.update_balance(balance)
            self.bot_state["balance"] = balance

    async def _position_monitor(self):
        while self._running:
            await asyncio.sleep(1)

            if self.bot_state.get("emergency_close"):
                positions = await self.execution.sync_positions()
                for pos in positions:
                    price = self.collector.last_price(pos.symbol)
                    if price > 0:
                        await self.execution.close_position(pos, price, "EMERGENCY_CLOSE")
                self.bot_state["emergency_close"] = False
                await self.telegram.notify_risk_alert("Fermeture d'urgence de toutes les positions!")
                balance = await self.execution.get_balance()
                await self.risk.update_balance(balance)
                self.bot_state["balance"] = balance
                continue

            open_positions = await self.execution.sync_positions()

            if self.config.is_paper:
                for pos in open_positions:
                    current_price = self.collector.last_price(pos.symbol)
                    if current_price <= 0:
                        continue

                    # Trailing stop update
                    new_sl = self.trailing.update(pos, current_price)
                    if new_sl is not None:
                        pos.stop_loss = new_sl
                        await self.db.save_position(pos)

                    effective_sl = self.trailing.get_effective_sl(pos)
                    from models import Side
                    should_close = False
                    reason = ""
                    if pos.side == Side.BUY:
                        if current_price <= effective_sl:
                            should_close, reason = True, "Stop-loss hit"
                        elif current_price >= pos.take_profit:
                            should_close, reason = True, "Take-profit hit"
                    else:
                        if current_price >= effective_sl:
                            should_close, reason = True, "Stop-loss hit"
                        elif current_price <= pos.take_profit:
                            should_close, reason = True, "Take-profit hit"

                    if should_close:
                        pnl = pos.calculate_pnl(current_price)
                        pnl_pct = pos.calculate_pnl_pct(current_price)
                        await self.execution.close_position(pos, current_price, reason)
                        self.trailing.remove(pos.id)

                        self.kill_switch.record_trade_result(pnl)
                        self.dynamic_sizer.record_trade(pnl)

                        trade_ctx = self.bot_state.get("_trade_contexts", {}).pop(pos.id, {})
                        original_signals = trade_ctx.get("original_signals", [])
                        self.learner.record_trade(pos.symbol, pos.strategy.value, original_signals, pnl > 0, pnl)

                        balance = await self.execution.get_balance()
                        await self.risk.update_balance(balance)
                        self.bot_state["balance"] = balance

                        today_stats = await self.db.get_today_stats()
                        open_time = trade_ctx.get("open_time", time.time())
                        duration_s = int(time.time() - open_time)
                        dm, ds = divmod(duration_s, 60)
                        dh, dm = divmod(dm, 60)
                        duration_str = f"{dh}h {dm}m" if dh else f"{dm}m {ds}s"

                        close_context = {
                            **trade_ctx,
                            "balance": balance,
                            "daily_pnl": today_stats.total_pnl,
                            "duration": duration_str,
                        }

                        await self.telegram.notify_trade_close(
                            pos.symbol, pos.side.value, pos.entry_price,
                            current_price, pnl, pnl_pct, reason, close_context,
                        )
            else:
                await self._sync_live_positions(open_positions)

    async def _sync_live_positions(self, db_positions: list):
        if not self.execution.exchange:
            return
        try:
            all_symbols = list({p.symbol for p in db_positions})
            exchange_positions = self.execution.exchange.fetch_positions(all_symbols)
            open_on_exchange = {
                p["symbol"]: abs(float(p.get("contracts", 0)))
                for p in exchange_positions if abs(float(p.get("contracts", 0))) > 0
            }
            for pos in db_positions:
                if pos.symbol not in open_on_exchange:
                    price = self.collector.last_price(pos.symbol)
                    if price <= 0:
                        continue
                    pnl = pos.calculate_pnl(price)
                    pnl_pct = pos.calculate_pnl_pct(price)
                    await self.db.close_position(pos.id, price, pnl, pnl_pct)
                    self.trailing.remove(pos.id)
                    await self.telegram.notify_trade_close(
                        pos.symbol, pos.side.value, pos.entry_price, price, pnl, pnl_pct, "Exchange SL/TP"
                    )
            if db_positions:
                balance = await self.execution.get_balance()
                await self.risk.update_balance(balance)
                self.bot_state["balance"] = balance
        except Exception as e:
            logger.debug(f"Position sync: {e}")

    async def _state_updater(self):
        while self._running:
            await asyncio.sleep(2)
            elapsed = time.time() - self._start_time
            hours, remainder = divmod(int(elapsed), 3600)
            minutes, seconds = divmod(remainder, 60)

            total_ticks = self.collector.total_ticks
            total_candles = sum(p.candle_count for p in self.pipelines.values())
            first_sym = self.config.symbol_list[0] if self.config.symbol_list else ""
            first_pipe = self.pipelines.get(first_sym)

            for symbol, pipeline in self.pipelines.items():
                self.bot_state["markets"][symbol] = {
                    "last_price": self.collector.last_price(symbol),
                    "ticks": self.collector.tick_count(symbol),
                    "candles": pipeline.candle_count,
                    "delta": round(pipeline.footprint.last_candle.delta, 4) if pipeline.footprint.last_candle else 0,
                    "cvd": round(pipeline.footprint.cumulative_delta, 4),
                }
                self.bot_state["regimes"][symbol] = pipeline.regime.current_regime.value
                self.bot_state["derivatives"][symbol] = self.derivatives.get_data(symbol)
                self.bot_state["sentiment"][symbol] = self.sentiment.get_data(symbol)

                candles = pipeline.footprint.get_last_n_candles(10)
                pred = pipeline.predictor.predict(candles)
                self.bot_state["ai_predictions"][symbol] = pred

            self.bot_state.update({
                "running": self._running,
                "last_price": self.collector.last_price(first_sym),
                "tick_count": total_ticks,
                "candles_count": total_candles,
                "cvd": round(first_pipe.footprint.cumulative_delta, 4) if first_pipe else 0,
                "last_delta": round(first_pipe.footprint.last_candle.delta, 4) if first_pipe and first_pipe.footprint.last_candle else 0,
                "uptime": f"{hours}h {minutes}m {seconds}s",
                "health": self.health.get_status(),
                "correlations": self.correlation.get_all_correlations(),
                "kill_switch": self.kill_switch.get_status(),
                "session": self.session.get_current_session(),
                "dynamic_sizing": self.dynamic_sizer.get_risk_multiplier(),
            })

            for symbol, pipe in self.pipelines.items():
                self.bot_state["orderbook"][symbol] = self.orderbook.get_analysis(symbol)
                self.bot_state["volume_profiles"][symbol] = pipe.volume_profile.get_analysis()

            self.bot_state["learner"] = self.learner.get_summary()
            self.bot_state["llm_calls"] = self.llm._call_count

    async def _run_dashboard(self):
        config = uvicorn.Config(app, host=self.config.dashboard_host,
                                port=self.config.effective_port, log_level="warning")
        server = uvicorn.Server(config)
        logger.info(f"Dashboard: http://localhost:{self.config.effective_port}")
        await server.serve()

    async def _shutdown(self):
        logger.info("Shutting down all modules...")
        self._running = False
        self.bot_state["running"] = False
        await self.collector.stop()
        await self.derivatives.stop()
        await self.sentiment.stop()
        await self.health.stop()
        await self.daily_report.stop()
        await self.orderbook.stop()
        await self.llm.close()
        await self.telegram.send("*Bot stopped.*")
        await self.telegram.close()
        await self.db.close()
        logger.info("Bot stopped cleanly.")


if __name__ == "__main__":
    bot = OrderFlowBot()
    asyncio.run(bot.start())
