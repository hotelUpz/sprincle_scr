# ============================================================
# File: CORE/bot.py
# Role: Ядро бота, управляет жизненным циклом потоков и фильтрацией сигналов
# ============================================================

import asyncio
import time
from pathlib import Path
from typing import Dict, Set

from c_log import UnifiedLogger
from CORE.symbols import SymbolsCoordinator
from CORE.market_streams import MarketStreams
from CORE.dedup import SignalDeduper
from API.special_assets import SpecialAssetsRegistry
from CORE.signal_evaluator import SignalEvaluator
from TG.notificator import TelegramSender
from TG.messages import build_funding_signal_message
from consts import APP_CFG, ROOT_DIR

from API.BINANCE.client import BinanceClient
from API.BITGET.client import BitgetClient
from API.PHEMEX.client import PhemexClient

class ASB:
    def __init__(self, logger: UnifiedLogger):
        self.logger = logger
        self.config = APP_CFG
        self.quote = self.config["runtime"]["quote"]
        self.blacklist = set(t.upper() for t in self.config.get("blacklist", []))
        
        # Init Exchanges
        self.exchanges = []
        enabled = self.config["exchanges"]["enabled"]
        if "binance" in enabled:
            self.exchanges.append(BinanceClient(logger=self.logger))
        if "phemex" in enabled:
            self.exchanges.append(PhemexClient(logger=self.logger))
        if "bitget" in enabled:
            self.exchanges.append(BitgetClient(logger=self.logger))

        # Core modules
        self.symbols_coordinator = SymbolsCoordinator(logger=self.logger)
        self.market_streams = MarketStreams(
            logger=self.logger,
            stakan_spread_pct_threshold=0.0,
            stakan_ttl_sec=0.0
        )
        self.market_streams.orderbook_enabled = True # FORCE orderbook on for arbitrage
        self.special_assets = SpecialAssetsRegistry(logger=self.logger)
        self.signal_evaluator = SignalEvaluator(
            rules_path=str(ROOT_DIR / "CONFIG" / "rules1.json"), 
            logger=self.logger
        )
        
        # Deduplication and Telegram
        self.deduper = SignalDeduper(state_path=ROOT_DIR / "logs" / "dedup.json", logger=self.logger)
        tg_cfg = self.config["telegram"]
        self.tg_enabled = tg_cfg.get("enabled", True)
        import os
        # Инициализируем отправителя телеграм
        tg_interval = float(self.config["telegram"]["min_send_interval_sec"])
        self.tg_sender = TelegramSender(
            token=os.getenv("TG_BOT_TOKEN", ""),
            chat_id=os.getenv("TG_CHAT_ID", ""),
            min_send_interval_sec=tg_interval
        )
        self.dedup_flush_sec = tg_cfg["dedup_flush_sec"]

        # State
        self.stop_bot = False
        self.bg_tasks = set()
        self.active_canonical_symbols = set()
        self.per_exchange_maps = {}

    async def _symbols_state_updater(self):
        poll_sec = self.config["runtime"]["symbols_update_loop_poll_sec"]
        while not self.stop_bot:
            try:
                _, _, per_maps = await self.symbols_coordinator.compute_common_symbols(
                    exchanges=self.exchanges, quote=self.quote
                )
                self.per_exchange_maps = per_maps
                
                ensure_dict = {}
                active_symbols = set()
                
                # We need to tell MarketStreams to ensure WS for these symbols
                for category, rules in self.signal_evaluator.rules.items():
                    for pair_key, rule in rules.items():
                        dom = rule.dominanta.upper()
                        slv = rule.sliver.upper()
                        
                        if dom in per_maps and slv in per_maps:
                            # Intersection for this pair
                            pair_common = set(per_maps[dom].keys()) & set(per_maps[slv].keys())
                            
                            # Filter out blacklisted tickers
                            pair_common = {c for c in pair_common if c.split("_")[0] not in self.blacklist}
                            
                            active_symbols.update(pair_common)
                            
                            ensure_dict.setdefault(dom, set()).update(
                                [per_maps[dom][c] for c in pair_common]
                            )
                            ensure_dict.setdefault(slv, set()).update(
                                [per_maps[slv][c] for c in pair_common]
                            )
                
                self.active_canonical_symbols = active_symbols
                await self.market_streams.ensure(ensure_dict)
            except Exception as e:
                self.logger.error(f"[BOT] Symbols update error: {e}")
            
            await asyncio.sleep(poll_sec)

    async def _funding_updater(self):
        poll_sec = self.config["runtime"]["funding_loop_poll_sec"]
        while not self.stop_bot:
            try:
                tasks = []
                for ex in self.exchanges:
                    if hasattr(ex, "funding") and hasattr(ex.funding, "refresh"):
                        tasks.append(ex.funding.refresh())
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
            except Exception as e:
                self.logger.error(f"[BOT] Funding update error: {e}")
            await asyncio.sleep(poll_sec)

    async def _signal_scanner(self):
        poll_sec = self.config["runtime"]["main_loop_poll_sec"]
        while not self.stop_bot:
            try:
                await self._scan_signals()
            except Exception as e:
                self.logger.error(f"[BOT] Signal scan error: {e}")
            await asyncio.sleep(poll_sec)

    async def _scan_signals(self):
        now_ms = int(time.time() * 1000)
        # Отдаем управление event loop'у на каждой итерации, чтобы не блокировать потоки WS (стаканы/цены)
        for canon in self.active_canonical_symbols:
            await asyncio.sleep(0)
            
            base = canon.split("_")[0]
            if base in self.blacklist:
                continue
            
            for category, rules in self.signal_evaluator.rules.items():
                for pair_key, rule in rules.items():
                    # Check if symbol category matches
                    # First, identify the category of this base asset
                    base = canon.split("_")[0]
                    asset_kind = self.special_assets.classify_base(base, ex1=rule.dominanta, ex2=rule.sliver)
                    
                    if category == "metall_assets" and asset_kind != "METAl":
                        continue
                    if category == "action_assets" and asset_kind != "ACTION":
                        continue
                    if category == "other_assets" and asset_kind != "USUAL":
                        continue

                    # Get data for dominanta
                    raw_d = self.per_exchange_maps.get(rule.dominanta.upper(), {}).get(canon)
                    raw_s = self.per_exchange_maps.get(rule.sliver.upper(), {}).get(canon)
                    if not raw_d or not raw_s:
                        continue

                    book_d = self.market_streams.get_book_info(rule.dominanta.upper(), raw_d, now_ms)
                    book_s = self.market_streams.get_book_info(rule.sliver.upper(), raw_s, now_ms)
                    if not book_d or not book_s:
                        continue

                    ex_d = next((x for x in self.exchanges if x.name.upper() == rule.dominanta.upper()), None)
                    ex_s = next((x for x in self.exchanges if x.name.upper() == rule.sliver.upper()), None)
                    if not ex_d or not ex_s:
                        continue
                        
                    fund_d_pt = ex_d.funding.get(raw_d) if hasattr(ex_d, "funding") else None
                    fund_s_pt = ex_s.funding.get(raw_s) if hasattr(ex_s, "funding") else None
                    if not fund_d_pt or not fund_s_pt:
                        continue

                    fund_d = fund_d_pt.funding_rate_pct
                    fund_s = fund_s_pt.funding_rate_pct
                    ttf_d = max(0, (fund_d_pt.next_funding_time_ms - now_ms) / 1000.0)
                    ttf_s = max(0, (fund_s_pt.next_funding_time_ms - now_ms) / 1000.0)

                    interval_d_str = str(ex_d.funding.interval_hours(raw_d)) if hasattr(ex_d, "funding") and hasattr(ex_d.funding, "interval_hours") else "?"
                    interval_s_str = str(ex_s.funding.interval_hours(raw_s)) if hasattr(ex_s, "funding") and hasattr(ex_s.funding, "interval_hours") else "?"

                    signals = self.signal_evaluator.evaluate(
                        symbol=canon,
                        category=asset_kind,
                        ask_d=book_d["ask"], bid_d=book_d["bid"], fund_d=fund_d, ttf_d=ttf_d,
                        ask_s=book_s["ask"], bid_s=book_s["bid"], fund_s=fund_s, ttf_s=ttf_s,
                        interval_d=interval_d_str, interval_s=interval_s_str,
                        rule=rule
                    )

                    if signals:
                        for signal in signals:
                            await self._process_signal(signal)

    async def _process_signal(self, signal):
        canon = signal["symbol"]
        rule = signal["rule"]
        dedup_key = f"{canon}_{rule.dominanta}_{rule.sliver}_signal"
        if self.deduper.is_seen(dedup_key):
            return
            
        self.deduper.mark(dedup_key, expires_at_ms=int(time.time() * 1000) + self.dedup_flush_sec * 1000)

        template_id = str(self.config["telegram"]["message_template_id"])
        templates = self.config["telegram"]["templates"]
        template_str = templates[template_id]

        req_ps = f"{rule.price_spread.min_spread:.2f}%" if rule.price_spread.enabled else "Off"
        req_fs = f"{rule.funding_spread.min_spread:.2f}%" if rule.funding_spread.enabled else "Off"

        msg = build_funding_signal_message(
            template_str=template_str,
            symbol=canon,
            kind=signal["category"],
            dom=rule.dominanta,
            slv=rule.sliver,
            price_spread=signal["price_spread"],
            funding_spread=signal["funding_spread"],
            req_price_spread=req_ps,
            req_funding_spread=req_fs,
            fund_d=signal["fund_d"],
            fund_s=signal["fund_s"],
            ttf_d=signal["ttf_d"],
            ttf_s=signal["ttf_s"],
            interval_d=signal["interval_d"],
            interval_s=signal["interval_s"]
        )
        self.logger.info(f"[SIGNAL] {dedup_key}")
        if getattr(self, "tg_enabled", True):
            asyncio.create_task(self.tg_sender.send_message(msg))

    async def run_forever(self):
        self.logger.info("[BOT] Starting ASB...")
        
        if getattr(self, "tg_enabled", True):
            startup_msg = self.config["telegram"].get("startup_message", "🤖 <b>ASB</b> started successfully!")
            asyncio.create_task(self.tg_sender.send_message(startup_msg))
        
        for ex in self.exchanges:
            if hasattr(ex, "bootstrap"):
                await ex.bootstrap()

        self.special_assets.start_background_refresh()
        await self.special_assets.ensure_fresh()

        tasks = [
            asyncio.create_task(self._symbols_state_updater()),
            asyncio.create_task(self._funding_updater()),
            asyncio.create_task(self._signal_scanner())
        ]
        self.bg_tasks.update(tasks)
        
        try:
            await asyncio.gather(*tasks)
        finally:
            self.stop_bot = True
            await self.market_streams.close()
            for ex in self.exchanges:
                if hasattr(ex, "shutdown"):
                    await ex.shutdown()
            await self.tg_sender.aclose()
            await self.special_assets.aclose()
