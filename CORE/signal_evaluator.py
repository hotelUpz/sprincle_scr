# ============================================================
# File: CORE/signal_evaluator.py
# Role: Вычисление спредов и оценка торговых сигналов по правилам
# ============================================================

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple, List
import time
from c_log import UnifiedLogger
from consts import ENABLED_EXCHANGES

@dataclass
class PriceSpreadConfig:
    enabled: bool
    min_spread: float

@dataclass
class FundingSpreadConfig:
    enabled: bool
    min_spread: float
    still_one: bool

@dataclass
class RuleConfig:
    dominanta: str
    sliver: str
    price_spread: PriceSpreadConfig
    funding_spread: FundingSpreadConfig
    across_funding: str
    ttl_sec_control: Optional[float]
    source_of_trus: str

class SignalEvaluator:
    def __init__(self, rules_path: str, logger: UnifiedLogger):
        self.logger = logger
        self.rules_path = Path(rules_path)
        self.rules: Dict[str, Dict[str, RuleConfig]] = {"metall_assets": {}, "action_assets": {}, "other_assets": {}}
        self._load_rules()

    def _load_rules(self):
        enabled_exchanges = ENABLED_EXCHANGES
        try:
            data = json.loads(self.rules_path.read_text(encoding="utf-8"))
            for category in ["metall_assets", "action_assets", "other_assets"]:
                if category in data:
                    for pair_key, cfg in data[category].items():
                        if pair_key == "_comment":
                            continue
                        parts = pair_key.split("-")
                        if len(parts) != 2:
                            continue
                        
                        dominanta = cfg["dominanta"].lower()
                        sliver = parts[1] if parts[0].lower() == dominanta else parts[0]
                        sliver = sliver.lower()

                        if dominanta not in enabled_exchanges or sliver not in enabled_exchanges:
                            self.logger.warning(f"[EVALUATOR] Skipping pair {pair_key} because {dominanta} or {sliver} is not enabled in app.json")
                            continue
                        
                        pc = cfg["price_spread"]
                        fc = cfg["funding_spread"]
                        
                        self.rules[category][f"{dominanta}-{sliver}"] = RuleConfig(
                            dominanta=dominanta,
                            sliver=sliver,
                            price_spread=PriceSpreadConfig(
                                enabled=bool(pc["enabled"]),
                                min_spread=float(pc["min_spread"])
                            ),
                            funding_spread=FundingSpreadConfig(
                                enabled=bool(fc["enabled"]),
                                min_spread=float(fc["min_spread"]),
                                still_one=bool(fc["still_one"])
                            ),
                            across_funding=str(cfg["across_funding"]),
                            ttl_sec_control=cfg["ttl_sec_control"],
                            source_of_trus=cfg["source_of_trus"]
                        )
            self.logger.info(f"[EVALUATOR] Loaded rules from {self.rules_path.name}")
        except Exception as e:
            self.logger.error(f"[EVALUATOR] Error loading rules: {e}")

    def evaluate(self, symbol: str, category: str, 
                 ask_d: float, bid_d: float, fund_d: float, ttf_d: float, 
                 ask_s: float, bid_s: float, fund_s: float, ttf_s: float,
                 interval_d: str, interval_s: str,
                 rule: RuleConfig) -> Optional[dict]:
        
        af = rule.across_funding
        
        try:
            val_d = float(interval_d)
        except ValueError:
            return
            
        try:
            val_s = float(interval_s)
        except ValueError:
            return

        if af == "1":
            if val_d != val_s:
                return None
        elif af == "2":
            if val_d == val_s:
                return None
        
        ps_long = (ask_d / ask_s - 1.0) * 100.0 if ask_s > 0 else 0.0
        ps_short = (bid_d / bid_s - 1.0) * 100.0 if bid_s > 0 else 0.0

        pc = rule.price_spread
        price_long_ok = not pc.enabled or (pc.enabled and ps_long >= pc.min_spread)
        price_short_ok = not pc.enabled or (pc.enabled and ps_short <= -pc.min_spread)

        fc = rule.funding_spread
        fund_delta = fund_d - fund_s
        
        if fc.still_one:
            fund_long_ok = not fc.enabled or (fc.enabled and (fund_d <= -fc.min_spread or fund_s <= - fc.min_spread))
            fund_short_ok = not fc.enabled or (fc.enabled and (fund_d >= fc.min_spread or fund_s >= fc.min_spread))
        else:
            fund_long_ok = not fc.enabled or (fc.enabled and fund_delta <= -fc.min_spread)
            fund_short_ok = not fc.enabled or (fc.enabled and fund_delta >= fc.min_spread)

        if not pc.enabled and not fc.enabled:
            return None

        base_signal = {
            "symbol": symbol,
            "category": category,
            "fund_d": fund_d,
            "fund_s": fund_s,
            "ttf_d": ttf_d,
            "ttf_s": ttf_s,
            "interval_d": interval_d,
            "interval_s": interval_s,
            "rule": rule
        }

        results = []

        if price_long_ok and fund_long_ok:
            results.append({
                **base_signal,
                "price_spread": ps_long,
                "funding_spread": fund_delta
            })

        if price_short_ok and fund_short_ok:
            results.append({
                **base_signal,
                "price_spread": ps_short,
                "funding_spread": fund_delta
            })
        
        return results
