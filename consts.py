# ============================================================
# File: consts.py
# Role: Глобальные константы и пути
# ============================================================

import json
import os
from pathlib import Path

ROOT_DIR = Path(__file__).parent
CONFIG_DIR = ROOT_DIR / "CONFIG"

with open(CONFIG_DIR / "app.json", "r", encoding="utf-8") as f:
    APP_CFG = json.load(f)

with open(CONFIG_DIR / "special_assets.json", "r", encoding="utf-8") as f:
    SA_CFG = json.load(f)

# LOGGING
LOG_DEBUG = True
LOG_INFO = True
LOG_WARNING = True
LOG_ERROR = True
LOG_TO_CONSOLE = APP_CFG["runtime"].get("console_log_enabled", True)
LOG_TO_FILE = APP_CFG["runtime"].get("file_log_enabled", True)
MAX_LOG_LINES = 20000
TIME_ZONE = "UTC"

# FORMATTING
DECIMAL_CONTEXT_PREC = 28
PRECISION = {"default": 6, "price": 4, "funding_rate": 4, "spread_pct": 2}

# SPECIAL ASSETS
ASSET_KIND_METAL = "METAl"
ASSET_KIND_ACTION = "ACTION"
ASSET_KIND_OTHER = "USUAL"
ENABLED_EXCHANGES = APP_CFG["exchanges"]["enabled"]

# Мы удалили base_url, stock_categories и metal_categories, так как они относились к CoinGecko
SPECIAL_ASSETS_CACHE_FILE = SA_CFG.get("cache_file", "special_assets_cache.json")
SPECIAL_ASSETS_FALLBACK_ACTION_BASES = SA_CFG.get("fallback_actions_bases", [])
SPECIAL_ASSETS_FALLBACK_METAL_BASES = SA_CFG.get("fallback_metal_bases", [])
SPECIAL_ASSETS_FORCE_USUAL_BASES = SA_CFG.get("force_usual_bases", [])
SPECIAL_ASSETS_REFRESH_EVERY_SEC = SA_CFG.get("refresh_every_sec", 10800)
SPECIAL_ASSETS_TIMEOUT_SEC = SA_CFG.get("request_timeout_sec", 10)

def canonical_pair_rule_key(ex1: str, ex2: str) -> str:
    parts = sorted([ex1.lower(), ex2.lower()])
    return f"{parts[0]}-{parts[1]}"