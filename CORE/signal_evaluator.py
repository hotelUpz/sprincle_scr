# # ============================================================
# # File: CORE/signal_evaluator.py
# # Role: Вычисление спредов и оценка торговых сигналов (Тип 1 и Тип 2)
# # ============================================================

# import json
# from dataclasses import dataclass
# from pathlib import Path
# from typing import Dict, Optional, List
# from c_log import UnifiedLogger
# from consts import ENABLED_EXCHANGES

# @dataclass
# class PriceSpreadConfig:
#     enabled: bool
#     min_spread: float

# @dataclass
# class FundingSpreadConfig:
#     enabled: bool
#     min_spread: float
#     still_one: bool

# @dataclass
# class RuleConfig:
#     dominanta: str
#     sliver: str
#     price1_spread: PriceSpreadConfig
#     price2_spread: PriceSpreadConfig
#     funding_spread: FundingSpreadConfig
#     across_funding: str
#     ttl_sec_control: Optional[float]

# class SignalEvaluator:
#     def __init__(self, rules_path: str, logger: UnifiedLogger):
#         self.logger = logger
#         self.rules_path = Path(rules_path)
#         self.rules: Dict[str, Dict[str, RuleConfig]] = {"metall_assets": {}, "action_assets": {}, "other_assets": {}}
#         self._load_rules()

#     def _load_rules(self):
#         enabled_exchanges = ENABLED_EXCHANGES
#         try:
#             data = json.loads(self.rules_path.read_text(encoding="utf-8"))
#             for category in ["metall_assets", "action_assets", "other_assets"]:
#                 if category in data:
#                     for pair_key, cfg in data[category].items():
#                         if pair_key == "_comment":
#                             continue
#                         parts = pair_key.split("-")
#                         if len(parts) != 2:
#                             continue
                        
#                         dominanta = cfg.get("dominanta", parts[0]).lower()
#                         sliver = parts[1] if parts[0].lower() == dominanta else parts[0]
#                         sliver = sliver.lower()

#                         if dominanta not in enabled_exchanges or sliver not in enabled_exchanges:
#                             continue
                        
#                         p1 = cfg.get("price1_spread", {"enabled": False, "min_spread": 0})
#                         p2 = cfg.get("price2_spread", {"enabled": False, "min_spread": 0})
#                         fc = cfg.get("funding_spread", {"enabled": False, "min_spread": 0, "still_one": False})
                        
#                         self.rules[category][f"{dominanta}-{sliver}"] = RuleConfig(
#                             dominanta=dominanta,
#                             sliver=sliver,
#                             price1_spread=PriceSpreadConfig(enabled=bool(p1["enabled"]), min_spread=float(p1["min_spread"])),
#                             price2_spread=PriceSpreadConfig(enabled=bool(p2["enabled"]), min_spread=float(p2["min_spread"])),
#                             funding_spread=FundingSpreadConfig(enabled=bool(fc["enabled"]), min_spread=float(fc["min_spread"]), still_one=bool(fc["still_one"])),
#                             across_funding=str(cfg.get("across_funding", "3")),
#                             ttl_sec_control=cfg.get("ttl_sec_control")
#                         )
#             self.logger.info(f"[EVALUATOR] Loaded rules from {self.rules_path.name}")
#         except Exception as e:
#             self.logger.error(f"[EVALUATOR] Error loading rules: {e}")

#     def evaluate(self, symbol: str, category: str, 
#                  ask_d: float, bid_d: float, fund_d: float, ttf_d: float, 
#                  ask_s: float, bid_s: float, fund_s: float, ttf_s: float,
#                  interval_d: str, interval_s: str,
#                  rule: RuleConfig) -> List[dict]:
        
#         af = rule.across_funding
#         try:
#             val_d = float(interval_d)
#             val_s = float(interval_s)
#         except ValueError:
#             return []

#         if af == "1" and val_d != val_s:
#             return []
#         elif af == "2" and val_d == val_s:
#             return []

#         # ТИП 1 (Перекрестные спреды: bid/ask и ask/bid)
#         ps_1a = (ask_d / bid_s - 1.0) * 100.0 if bid_s > 0 else 0.0
#         ps_1b = (bid_d / ask_s - 1.0) * 100.0 if ask_s > 0 else 0.0

#         # ТИП 2 (Прямые спреды: ask/ask и bid/bid)
#         ps_2a = (ask_d / ask_s - 1.0) * 100.0 if ask_s > 0 else 0.0
#         ps_2b = (bid_d / bid_s - 1.0) * 100.0 if bid_s > 0 else 0.0

#         fund_delta = fund_d - fund_s
#         fc = rule.funding_spread
        
#         # Проверка фандинга (если включена)
#         fund_ok = True
#         if fc.enabled:
#             if fc.still_one:
#                 fund_ok = (abs(fund_d) >= fc.min_spread) or (abs(fund_s) >= fc.min_spread)
#             else:
#                 fund_ok = abs(fund_delta) >= fc.min_spread

#         if not rule.price1_spread.enabled and not rule.price2_spread.enabled:
#             return []

#         # Если фандинг обязателен, но не прошел — сигнала нет
#         if fc.enabled and not fund_ok:
#             return []

#         base_signal = {
#             "symbol": symbol,
#             "category": category,
#             "fund_d": fund_d,
#             "fund_s": fund_s,
#             "ttf_d": ttf_d,
#             "ttf_s": ttf_s,
#             "interval_d": interval_d,
#             "interval_s": interval_s,
#             "rule": rule,
#             "funding_spread": fund_delta
#         }

#         results = []

#         # Оценка Тип 1
#         if rule.price1_spread.enabled:
#             if abs(ps_1a) >= rule.price1_spread.min_spread:
#                 results.append({**base_signal, "signal_type": "Тип 1", "comparison": "ask_d/bid_s", "price_spread": ps_1a})
#             if abs(ps_1b) >= rule.price1_spread.min_spread:
#                 results.append({**base_signal, "signal_type": "Тип 1", "comparison": "bid_d/ask_s", "price_spread": ps_1b})

#         # Оценка Тип 2
#         if rule.price2_spread.enabled:
#             if abs(ps_2a) >= rule.price2_spread.min_spread:
#                 results.append({**base_signal, "signal_type": "Тип 2", "comparison": "ask_d/ask_s", "price_spread": ps_2a})
#             if abs(ps_2b) >= rule.price2_spread.min_spread:
#                 results.append({**base_signal, "signal_type": "Тип 2", "comparison": "bid_d/bid_s", "price_spread": ps_2b})

#         return results


# # ============================================================
# # File: CORE/signal_evaluator.py
# # Role: Вычисление спредов и оценка торговых сигналов (Тип 1 и Тип 2)
# # ============================================================

# import json
# from dataclasses import dataclass
# from pathlib import Path
# from typing import Dict, Optional, List
# from c_log import UnifiedLogger
# from consts import ENABLED_EXCHANGES

# @dataclass
# class PriceSpreadConfig:
#     enabled: bool
#     min_spread: float

# @dataclass
# class FundingSpreadConfig:
#     enabled: bool
#     min_spread: float
#     still_one: bool

# @dataclass
# class RuleConfig:
#     dominanta: str
#     sliver: str
#     price1_spread: PriceSpreadConfig
#     price2_spread: PriceSpreadConfig
#     funding_spread: FundingSpreadConfig
#     across_funding: str
#     ttl_sec_control: Optional[float]

# class SignalEvaluator:
#     def __init__(self, rules_path: str, logger: UnifiedLogger):
#         self.logger = logger
#         self.rules_path = Path(rules_path)
#         self.rules: Dict[str, Dict[str, RuleConfig]] = {"metall_assets": {}, "action_assets": {}, "other_assets": {}}
#         self._load_rules()

#     def _load_rules(self):
#         enabled_exchanges = ENABLED_EXCHANGES
#         try:
#             data = json.loads(self.rules_path.read_text(encoding="utf-8"))
#             for category in ["metall_assets", "action_assets", "other_assets"]:
#                 if category in data:
#                     for pair_key, cfg in data[category].items():
#                         if pair_key == "_comment":
#                             continue
                        
#                         # Проверка флага включения самой связки
#                         if not cfg.get("enabled", True):
#                             continue
                            
#                         parts = pair_key.split("-")
#                         if len(parts) != 2:
#                             continue
                        
#                         dominanta = cfg.get("dominanta", parts[0]).lower()
#                         sliver = parts[1] if parts[0].lower() == dominanta else parts[0]
#                         sliver = sliver.lower()

#                         if dominanta not in enabled_exchanges or sliver not in enabled_exchanges:
#                             continue
                        
#                         p1 = cfg.get("price1_spread", {"enabled": False, "min_spread": 0})
#                         p2 = cfg.get("price2_spread", {"enabled": False, "min_spread": 0})
#                         fc = cfg.get("funding_spread", {"enabled": False, "min_spread": 0, "still_one": False})
                        
#                         self.rules[category][f"{dominanta}-{sliver}"] = RuleConfig(
#                             dominanta=dominanta,
#                             sliver=sliver,
#                             price1_spread=PriceSpreadConfig(enabled=bool(p1["enabled"]), min_spread=float(p1["min_spread"])),
#                             price2_spread=PriceSpreadConfig(enabled=bool(p2["enabled"]), min_spread=float(p2["min_spread"])),
#                             funding_spread=FundingSpreadConfig(enabled=bool(fc["enabled"]), min_spread=float(fc["min_spread"]), still_one=bool(fc["still_one"])),
#                             across_funding=str(cfg.get("across_funding", "3")),
#                             ttl_sec_control=cfg.get("ttl_sec_control")
#                         )
#             self.logger.info(f"[EVALUATOR] Loaded rules from {self.rules_path.name}")
#         except Exception as e:
#             self.logger.error(f"[EVALUATOR] Error loading rules: {e}")

#     def evaluate(self, symbol: str, category: str, 
#                  ask_d: float, bid_d: float, fund_d: float, ttf_d: float, 
#                  ask_s: float, bid_s: float, fund_s: float, ttf_s: float,
#                  interval_d: str, interval_s: str,
#                  rule: RuleConfig) -> List[dict]:
        
#         af = rule.across_funding
#         try:
#             val_d = float(interval_d)
#             val_s = float(interval_s)
#         except ValueError:
#             return []

#         if af == "1" and val_d != val_s:
#             return []
#         elif af == "2" and val_d == val_s:
#             return []

#         # ТИП 1 (Перекрестные спреды: bid/ask и ask/bid)
#         ps_1a = (ask_d / bid_s - 1.0) * 100.0 if bid_s > 0 else 0.0
#         ps_1b = (bid_d / ask_s - 1.0) * 100.0 if ask_s > 0 else 0.0

#         # ТИП 2 (Прямые спреды: ask/ask и bid/bid)
#         ps_2a = (ask_d / ask_s - 1.0) * 100.0 if ask_s > 0 else 0.0
#         ps_2b = (bid_d / bid_s - 1.0) * 100.0 if bid_s > 0 else 0.0

#         fund_delta = fund_d - fund_s
#         fc = rule.funding_spread
        
#         # Проверка фандинга (если включена)
#         fund_ok = True
#         if fc.enabled:
#             if fc.still_one:
#                 fund_ok = (abs(fund_d) >= fc.min_spread) or (abs(fund_s) >= fc.min_spread)
#             else:
#                 fund_ok = abs(fund_delta) >= fc.min_spread

#         if not rule.price1_spread.enabled and not rule.price2_spread.enabled:
#             return []

#         # Если фандинг обязателен, но не прошел — сигнала нет
#         if fc.enabled and not fund_ok:
#             return []

#         base_signal = {
#             "symbol": symbol,
#             "category": category,
#             "fund_d": fund_d,
#             "fund_s": fund_s,
#             "ttf_d": ttf_d,
#             "ttf_s": ttf_s,
#             "interval_d": interval_d,
#             "interval_s": interval_s,
#             "rule": rule,
#             "funding_spread": fund_delta
#         }

#         results = []

#         # Оценка Тип 1
#         if rule.price1_spread.enabled:
#             if abs(ps_1a) >= rule.price1_spread.min_spread:
#                 results.append({**base_signal, "signal_type": "Тип 1", "comparison": "ask_d/bid_s", "price_spread": ps_1a})
#             if abs(ps_1b) >= rule.price1_spread.min_spread:
#                 results.append({**base_signal, "signal_type": "Тип 1", "comparison": "bid_d/ask_s", "price_spread": ps_1b})

#         # Оценка Тип 2
#         if rule.price2_spread.enabled:
#             if abs(ps_2a) >= rule.price2_spread.min_spread:
#                 results.append({**base_signal, "signal_type": "Тип 2", "comparison": "ask_d/ask_s", "price_spread": ps_2a})
#             if abs(ps_2b) >= rule.price2_spread.min_spread:
#                 results.append({**base_signal, "signal_type": "Тип 2", "comparison": "bid_d/bid_s", "price_spread": ps_2b})

#         return results


# ============================================================
# File: CORE/signal_evaluator.py
# Role: Вычисление спредов, оценка торговых сигналов и Hot Reload
# ============================================================

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, List
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
    price1_spread: PriceSpreadConfig
    price2_spread: PriceSpreadConfig
    funding_spread: FundingSpreadConfig
    across_funding: str
    ttl_sec_control: Optional[float]

class SignalEvaluator:
    def __init__(self, rules_path: str, logger: UnifiedLogger):
        self.logger = logger
        self.rules_path = Path(rules_path)
        self.rules: Dict[str, Dict[str, RuleConfig]] = {"metall_assets": {}, "action_assets": {}, "other_assets": {}}
        self._last_mtime = 0
        self._last_check = 0
        self._load_rules()

    def _check_reload(self):
        """Горячая перезагрузка: проверяем файл не чаще раза в 5 секунд"""
        now = time.time()
        if now - self._last_check < 60.0:
            return
        self._last_check = now
        try:
            mtime = self.rules_path.stat().st_mtime
            if mtime > self._last_mtime:
                self.logger.info(f"[EVALUATOR] Изменение в {self.rules_path.name}. Перезагрузка правил на лету...")
                self.rules = {"metall_assets": {}, "action_assets": {}, "other_assets": {}}
                self._load_rules()
        except Exception:
            pass

    def _load_rules(self):
        enabled_exchanges = ENABLED_EXCHANGES
        try:
            self._last_mtime = self.rules_path.stat().st_mtime
            data = json.loads(self.rules_path.read_text(encoding="utf-8"))
            for category in ["metall_assets", "action_assets", "other_assets"]:
                if category in data:
                    for pair_key, cfg in data[category].items():
                        if pair_key == "_comment":
                            continue
                        
                        # Проверка флага включения самой связки
                        if not cfg.get("enabled", True):
                            continue
                            
                        parts = pair_key.split("-")
                        if len(parts) != 2:
                            continue
                        
                        dominanta = cfg.get("dominanta", parts[0]).lower()
                        sliver = parts[1] if parts[0].lower() == dominanta else parts[0]
                        sliver = sliver.lower()

                        if dominanta not in enabled_exchanges or sliver not in enabled_exchanges:
                            continue
                        
                        p1 = cfg.get("price1_spread", {"enabled": False, "min_spread": 0})
                        p2 = cfg.get("price2_spread", {"enabled": False, "min_spread": 0})
                        fc = cfg.get("funding_spread", {"enabled": False, "min_spread": 0, "still_one": False})
                        
                        self.rules[category][f"{dominanta}-{sliver}"] = RuleConfig(
                            dominanta=dominanta,
                            sliver=sliver,
                            price1_spread=PriceSpreadConfig(enabled=bool(p1["enabled"]), min_spread=float(p1["min_spread"])),
                            price2_spread=PriceSpreadConfig(enabled=bool(p2["enabled"]), min_spread=float(p2["min_spread"])),
                            funding_spread=FundingSpreadConfig(enabled=bool(fc["enabled"]), min_spread=float(fc["min_spread"]), still_one=bool(fc["still_one"])),
                            across_funding=str(cfg.get("across_funding", "3")),
                            ttl_sec_control=cfg.get("ttl_sec_control")
                        )
            self.logger.info(f"[EVALUATOR] Loaded rules from {self.rules_path.name}")
        except Exception as e:
            self.logger.error(f"[EVALUATOR] Error loading rules: {e}")

    def evaluate(self, symbol: str, category: str, 
                 ask_d: float, bid_d: float, fund_d: float, ttf_d: float, 
                 ask_s: float, bid_s: float, fund_s: float, ttf_s: float,
                 interval_d: str, interval_s: str,
                 rule: RuleConfig) -> List[dict]:
        
        self._check_reload() # Триггерим проверку горячей перезагрузки
        
        af = rule.across_funding
        try:
            val_d = float(interval_d)
            val_s = float(interval_s)
        except ValueError:
            return []

        if af == "1" and val_d != val_s:
            return []
        elif af == "2" and val_d == val_s:
            return []

        # ТИП 1 (Перекрестные спреды: bid/ask и ask/bid)
        ps_1a = (ask_d / bid_s - 1.0) * 100.0 if bid_s > 0 else 0.0
        ps_1b = (bid_d / ask_s - 1.0) * 100.0 if ask_s > 0 else 0.0

        # ТИП 2 (Прямые спреды: ask/ask и bid/bid)
        ps_2a = (ask_d / ask_s - 1.0) * 100.0 if ask_s > 0 else 0.0
        ps_2b = (bid_d / bid_s - 1.0) * 100.0 if bid_s > 0 else 0.0

        fund_delta = fund_d - fund_s
        fc = rule.funding_spread
        
        # Проверка фандинга (если включена)
        fund_ok = True
        if fc.enabled:
            if fc.still_one:
                fund_ok = (abs(fund_d) >= fc.min_spread) or (abs(fund_s) >= fc.min_spread)
            else:
                fund_ok = abs(fund_delta) >= fc.min_spread

        if not rule.price1_spread.enabled and not rule.price2_spread.enabled:
            return []

        # Если фандинг обязателен, но не прошел — сигнала нет
        if fc.enabled and not fund_ok:
            return []

        base_signal = {
            "symbol": symbol,
            "category": category,
            "fund_d": fund_d,
            "fund_s": fund_s,
            "ttf_d": ttf_d,
            "ttf_s": ttf_s,
            "interval_d": interval_d,
            "interval_s": interval_s,
            "rule": rule,
            "funding_spread": fund_delta
        }

        results = []

        # Оценка Тип 1
        if rule.price1_spread.enabled:
            if abs(ps_1a) >= rule.price1_spread.min_spread:
                results.append({**base_signal, "signal_type": "Тип 1", "comparison": "ask_d/bid_s", "price_spread": ps_1a})
            if abs(ps_1b) >= rule.price1_spread.min_spread:
                results.append({**base_signal, "signal_type": "Тип 1", "comparison": "bid_d/ask_s", "price_spread": ps_1b})

        # Оценка Тип 2
        if rule.price2_spread.enabled:
            if abs(ps_2a) >= rule.price2_spread.min_spread:
                results.append({**base_signal, "signal_type": "Тип 2", "comparison": "ask_d/ask_s", "price_spread": ps_2a})
            if abs(ps_2b) >= rule.price2_spread.min_spread:
                results.append({**base_signal, "signal_type": "Тип 2", "comparison": "bid_d/bid_s", "price_spread": ps_2b})

        return results