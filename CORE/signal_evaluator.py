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
class StakanPatternConfig:
    enabled: bool
    min_spread_to_distdenom_rate: float

@dataclass
class RuleConfig:
    dominanta: str
    sliver: str
    price1_spread: PriceSpreadConfig
    price2_spread: PriceSpreadConfig
    funding_spread: FundingSpreadConfig
    across_funding: str
    ttl_sec_control: Optional[float]
    stakan_pattern: StakanPatternConfig

class SignalEvaluator:
    def __init__(self, rules_path: str, logger: UnifiedLogger):
        self.logger = logger
        self.rules_path = Path(rules_path)
        self.rules: Dict[str, Dict[str, RuleConfig]] = {"metall_assets": {}, "action_assets": {}, "other_assets": {}}
        self._last_mtime = 0
        self._last_check = 0
        # Словарь для хранения времени (в секундах) первого пересечения порога
        self._condition_state: Dict[str, float] = {}
        self._load_rules()

    def _check_reload(self):
        """Горячая перезагрузка: проверяем файл не чаще раза в 5 секунд"""
        now = time.time()
        if now - self._last_check < 5.0:
            return
        self._last_check = now
        try:
            mtime = self.rules_path.stat().st_mtime
            if mtime > self._last_mtime:
                self.logger.info(f"[EVALUATOR] Изменение в {self.rules_path.name}. Перезагрузка правил на лету...")
                self.rules = {"metall_assets": {}, "action_assets": {}, "other_assets": {}}
                self._load_rules()
                # При смене правил очищаем кэш состояний, чтобы не было фантомных срабатываний
                self._condition_state.clear()
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
                        
                        if not cfg["enabled"]:
                            continue
                            
                        parts = pair_key.split("-")
                        if len(parts) != 2:
                            continue
                        
                        dominanta = cfg["dominanta"].lower()
                        sliver = parts[1] if parts[0].lower() == dominanta else parts[0]
                        sliver = sliver.lower()

                        if dominanta not in enabled_exchanges or sliver not in enabled_exchanges:
                            continue
                        
                        p1 = cfg["price1_spread"]
                        p2 = cfg["price2_spread"]
                        fc = cfg["funding_spread"]
                        st = cfg["stakan_pattern"]
                        
                        self.rules[category][f"{dominanta}-{sliver}"] = RuleConfig(
                            dominanta=dominanta,
                            sliver=sliver,
                            price1_spread=PriceSpreadConfig(enabled=bool(p1["enabled"]), min_spread=p1["min_spread"]),
                            price2_spread=PriceSpreadConfig(enabled=bool(p2["enabled"]), min_spread=p2["min_spread"]),
                            funding_spread=FundingSpreadConfig(enabled=bool(fc["enabled"]), min_spread=fc["min_spread"], still_one=bool(fc["still_one"])),
                            across_funding=str(cfg["across_funding"]),
                            ttl_sec_control=cfg["ttl_sec_control"],
                            stakan_pattern=StakanPatternConfig(enabled=bool(st["enabled"]), min_spread_to_distdenom_rate=st["min_spread_to_distdenom_rate"])
                        )
            self.logger.info(f"[EVALUATOR] Loaded rules from {self.rules_path.name}")
        except Exception as e:
            self.logger.error(f"[EVALUATOR] Error loading rules: {e}")

    def evaluate(self, symbol: str, category: str, 
                 ask_d: float, bid_d: float, fund_d: float, ttf_d: float, 
                 ask_s: float, bid_s: float, fund_s: float, ttf_s: float,
                 interval_d: str, interval_s: str,
                 rule: RuleConfig) -> List[dict]:
        
        self._check_reload()
        now = time.time()
        
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

        # ТИП 1 (Перекрестные спреды)
        ps_1a = (ask_d / bid_s - 1.0) * 100.0 if bid_s > 0 else 0.0
        ps_1b = (bid_d / ask_s - 1.0) * 100.0 if ask_s > 0 else 0.0

        # ТИП 2 (Прямые спреды)
        ps_2a = (ask_d / ask_s - 1.0) * 100.0 if ask_s > 0 else 0.0
        ps_2b = (bid_d / bid_s - 1.0) * 100.0 if bid_s > 0 else 0.0

        fund_delta = fund_d - fund_s
        fc = rule.funding_spread
        
        fund_ok = True
        if fc.enabled:
            if fc.still_one:
                fund_ok = (abs(fund_d) >= fc.min_spread) or (abs(fund_s) >= fc.min_spread)
            else:
                fund_ok = abs(fund_delta) >= fc.min_spread

        if not rule.price1_spread.enabled and not rule.price2_spread.enabled:
            return []

        if fc.enabled and not fund_ok:
            return []

        if rule.stakan_pattern.enabled:
            min_spread_to_distdenom_rate = rule.stakan_pattern.min_spread_to_distdenom_rate

            distdenom_rate = bid_s - ask_s
            ps_1a_delta = abs(ask_d - bid_s)
            ps_1b_delta = abs(bid_d - ask_s)
            spread_to_distdenom_rate1 = abs(ps_1a_delta / distdenom_rate)
            spread_to_distdenom_rate2 = abs(ps_1b_delta / distdenom_rate)
            spread_to_distdenom_rate_max = max(spread_to_distdenom_rate1, spread_to_distdenom_rate2)

            skip_distdenom_rat = (
                min_spread_to_distdenom_rate is not None and
                spread_to_distdenom_rate_max < min_spread_to_distdenom_rate
            )

            if skip_distdenom_rat: return []

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
        req_ttl = rule.ttl_sec_control or 0.0

        def evaluate_condition(key: str, spread: float, is_enabled: bool, min_spread: float, sig_type: str, comp: str):
            # Если выключено или спред упал ниже нормы — удаляем из стейта (сброс таймера)
            if not is_enabled or abs(spread) < min_spread:
                self._condition_state.pop(key, None)
                return
            
            # Если пересечение произошло впервые — запоминаем время
            if key not in self._condition_state:
                self._condition_state[key] = now
            
            elapsed = now - self._condition_state[key]
            
            # Выдаем сигнал только если выдержали нужное время TTL
            if elapsed >= req_ttl:
                results.append({
                    **base_signal, 
                    "signal_type": sig_type, 
                    "comparison": comp, 
                    "price_spread": spread,
                    "elapsed_sec": elapsed
                })

        # Уникальные ключи для каждой комбинации
        evaluate_condition(f"{symbol}_{rule.dominanta}_{rule.sliver}_1a", ps_1a, rule.price1_spread.enabled, rule.price1_spread.min_spread, "Тип 1", "ask_d/bid_s")
        evaluate_condition(f"{symbol}_{rule.dominanta}_{rule.sliver}_1b", ps_1b, rule.price1_spread.enabled, rule.price1_spread.min_spread, "Тип 1", "bid_d/ask_s")
        evaluate_condition(f"{symbol}_{rule.dominanta}_{rule.sliver}_2a", ps_2a, rule.price2_spread.enabled, rule.price2_spread.min_spread, "Тип 2", "ask_d/ask_s")
        evaluate_condition(f"{symbol}_{rule.dominanta}_{rule.sliver}_2b", ps_2b, rule.price2_spread.enabled, rule.price2_spread.min_spread, "Тип 2", "bid_d/bid_s")

        return results