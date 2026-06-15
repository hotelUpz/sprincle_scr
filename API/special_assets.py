# # ============================================================
# # File: API/special_assets.py
# # Role: Кэширование и получение списков специальных активов (акции, металлы)
# # ============================================================

# from __future__ import annotations

# """Special assets registry: metals / tokenized stocks / usual assets.
# """

# import asyncio
# import json
# import os
# import time
# import contextlib
# from dataclasses import dataclass
# from pathlib import Path
# from typing import Any, Dict, Iterable, Optional, Set

# import aiohttp

# from consts import (
#     ASSET_KIND_ACTION,
#     ASSET_KIND_METAL,
#     ASSET_KIND_OTHER,
#     ENABLED_EXCHANGES,
#     ROOT_DIR,
#     SPECIAL_ASSETS_BASE_URL,
#     SPECIAL_ASSETS_CACHE_FILE,
#     SPECIAL_ASSETS_FALLBACK_ACTION_BASES,
#     SPECIAL_ASSETS_FALLBACK_METAL_BASES,
#     SPECIAL_ASSETS_FORCE_USUAL_BASES,
#     SPECIAL_ASSETS_METAL_CATEGORIES,
#     SPECIAL_ASSETS_REFRESH_EVERY_SEC,
#     SPECIAL_ASSETS_STOCK_CATEGORIES,
#     SPECIAL_ASSETS_TIMEOUT_SEC,
#     canonical_pair_rule_key,
# )


# EXCHANGE_ENDPOINTS = {
#     "binance": "https://fapi.binance.com/fapi/v1/exchangeInfo",
#     "bitget": "https://api.bitget.com/api/v2/mix/market/contracts?productType=usdt-futures",
#     "kucoin": "https://api-futures.kucoin.com/api/v1/contracts/active",
#     "phemex": "https://api.phemex.com/public/products",
# }


# @dataclass(frozen=True)
# class SpecialAssetsSnapshot:
#     updated_at_ms: int
#     metals: frozenset[str]
#     stocks: frozenset[str]
#     exchange_bases: Dict[str, frozenset[str]]
#     pair_categories: Dict[str, Dict[str, frozenset[str]]]

#     def classify_base(self, base: str, *, pair_key: Optional[str] = None) -> str:
#         b = normalize_base(base)
#         if not b:
#             return ASSET_KIND_OTHER
#         # Global metals/stocks always win (prevents stale cache from misclassifying defaults)
#         if b in self.metals:
#             return ASSET_KIND_METAL
#         if b in self.stocks:
#             return ASSET_KIND_ACTION
#         if pair_key:
#             cats = self.pair_categories.get(str(pair_key).lower()) or {}
#             if b in cats.get(ASSET_KIND_METAL, frozenset()):
#                 return ASSET_KIND_METAL
#             if b in cats.get(ASSET_KIND_ACTION, frozenset()):
#                 return ASSET_KIND_ACTION
#         return ASSET_KIND_OTHER


# def normalize_base(symbol: str) -> str:
#     if not symbol:
#         return ""
#     s = str(symbol).upper().replace("_", "").replace("-", "").strip()
#     for suf in ("USDTM", "USDCM", "USDT", "PERP", "USD"):
#         if s.endswith(suf):
#             base = s[:-len(suf)]
#             return "BTC" if base == "XBT" else base
#     return "BTC" if s == "XBT" else s


# def clean_coingecko_symbol(symbol: str) -> list[str]:
#     if not symbol:
#         return []
#     norm = normalize_base(symbol)
#     if not norm:
#         return []
#     results = {norm}
    
#     s = str(symbol).upper().strip()
#     clean = s.replace("_", "").replace("-", "").replace(".", "")
    
#     # 1. Strip known suffixes: .D, ON, X, BDR, M
#     for suf in ("ON", "X", "BDR", "D", "M"):
#         if clean.endswith(suf) and len(clean) > len(suf) + 1:
#             root = clean[:-len(suf)]
#             results.add(root)
#             if root.startswith("B") or root.startswith("M"):
#                 results.add(root[1:])

#     # 2. Strip known prefixes: B (Backed), M (Mirror)
#     if clean.startswith("B") or clean.startswith("M"):
#         if len(clean) > 2:
#             results.add(clean[1:])
            
#     return [r for r in results if r]

# def _default_metals() -> Set[str]:
#     return {normalize_base(x) for x in SPECIAL_ASSETS_FALLBACK_METAL_BASES if normalize_base(x)}


# def _default_stocks() -> Set[str]:
#     return {normalize_base(x) for x in SPECIAL_ASSETS_FALLBACK_ACTION_BASES if normalize_base(x)}


# def _force_usual_set() -> Set[str]:
#     """Return the set of bases that must always be classified as other_assets (usual crypto)."""
#     return {normalize_base(x) for x in SPECIAL_ASSETS_FORCE_USUAL_BASES if normalize_base(x)}


# class SpecialAssetsRegistry:
#     def __init__(
#         self,
#         *,
#         cache_path: str | Path | None = None,
#         refresh_every_sec: int = SPECIAL_ASSETS_REFRESH_EVERY_SEC,
#         timeout_sec: float = SPECIAL_ASSETS_TIMEOUT_SEC,
#         enabled_exchanges: Optional[Iterable[str]] = None,
#         logger=None,
#     ):
#         self.logger = logger
#         self.cache_path = Path(cache_path or (ROOT_DIR / SPECIAL_ASSETS_CACHE_FILE))
#         if not self.cache_path.is_absolute():
#             self.cache_path = ROOT_DIR / self.cache_path
#         self.refresh_every_sec = max(300, int(refresh_every_sec or SPECIAL_ASSETS_REFRESH_EVERY_SEC))
#         self.timeout = aiohttp.ClientTimeout(total=float(timeout_sec or SPECIAL_ASSETS_TIMEOUT_SEC))
#         self.enabled_exchanges = [str(x).strip().lower() for x in (enabled_exchanges or ENABLED_EXCHANGES or []) if str(x).strip()]
#         self._lock = asyncio.Lock()
#         self._session: aiohttp.ClientSession | None = None
#         self._refresh_task: asyncio.Task | None = None
#         self._snapshot: Optional[SpecialAssetsSnapshot] = None

#     async def _get_session(self) -> aiohttp.ClientSession:
#         if self._session is not None and not self._session.closed:
#             return self._session
#         connector = aiohttp.TCPConnector(limit=20, ttl_dns_cache=300, enable_cleanup_closed=True)
#         self._session = aiohttp.ClientSession(timeout=self.timeout, connector=connector)
#         return self._session

#     async def aclose(self) -> None:
#         if self._refresh_task is not None:
#             self._refresh_task.cancel()
#             with contextlib.suppress(Exception):
#                 await self._refresh_task
#             self._refresh_task = None
#         if self._session is not None:
#             with contextlib.suppress(Exception):
#                 await self._session.close()
#             self._session = None

#     def _build_coingecko_request_meta(self) -> Optional[tuple[str, Dict[str, str]]]:
#         api_key = os.getenv("COINGECKO_API_KEY", "").strip()
#         if not api_key:
#             self._log("info", "[SPECIAL_ASSETS] CoinGecko API key missing; using defaults/cache only")
#             return None

#         base_url = str(SPECIAL_ASSETS_BASE_URL).rstrip("/")
#         headers: Dict[str, str] = {"accept": "application/json"}

#         if "pro-api" in base_url.lower():
#             headers["x-cg-pro-api-key"] = api_key
#             plan_name = "pro"
#         else:
#             headers["x-cg-demo-api-key"] = api_key
#             plan_name = "demo"

#         self._log("info", f"[SPECIAL_ASSETS] CoinGecko enabled plan={plan_name} base_url={base_url}")
#         return base_url, headers

#     def start_background_refresh(self) -> None:
#         if self._refresh_task is None or self._refresh_task.done():
#             self._refresh_task = asyncio.create_task(self._refresh_loop(), name="special-assets-refresh")

#     async def _refresh_loop(self) -> None:
#         while True:
#             try:
#                 await self.ensure_fresh(force=False)
#             except asyncio.CancelledError:
#                 raise
#             except Exception as e:
#                 self._log("warning", f"[SPECIAL_ASSETS] background refresh failed: {e}")
#             await asyncio.sleep(max(300, int(self.refresh_every_sec)))

#     def _log(self, level: str, msg: str) -> None:
#         if not self.logger:
#             return
#         fn = getattr(self.logger, level, None)
#         if callable(fn):
#             try:
#                 fn(msg)
#             except Exception:
#                 pass

#     def snapshot(self) -> Optional[SpecialAssetsSnapshot]:
#         return self._snapshot

#     async def ensure_fresh(self, *, force: bool = False) -> SpecialAssetsSnapshot:
#         async with self._lock:
#             cached = self._snapshot or self._load_cache()
#             now_ms = int(time.time() * 1000)
#             if cached and not force:
#                 age_sec = max(0.0, (now_ms - cached.updated_at_ms) / 1000.0)
#                 if age_sec < float(self.refresh_every_sec):
#                     self._snapshot = cached
#                     return cached

#             try:
#                 fresh = await self._fetch_and_build_snapshot()
#                 if fresh and (fresh.metals or fresh.stocks or fresh.exchange_bases):
#                     self._snapshot = fresh
#                     self._save_cache(fresh)
#                     self._log("info", f"[SPECIAL_ASSETS] cache refreshed metals={len(fresh.metals)} stocks={len(fresh.stocks)}")
#                     return fresh
#                 if cached:
#                     self._log("warning", "[SPECIAL_ASSETS] refresh returned empty data, keeping previous cache")
#                     self._snapshot = cached
#                     return cached
#                 raise RuntimeError("special assets refresh returned empty data and no cache exists")
#             except Exception as e:
#                 if cached:
#                     self._log("warning", f"[SPECIAL_ASSETS] refresh failed, keeping previous cache: {e}")
#                     self._snapshot = cached
#                     return cached
#                 raise

#     def classify_base(self, base: str, *, ex1: Optional[str] = None, ex2: Optional[str] = None) -> str:
#         snap = self._snapshot or self._load_cache()
#         if not snap:
#             return ASSET_KIND_OTHER
#         pair_key = canonical_pair_rule_key(ex1 or "", ex2 or "") if ex1 and ex2 else None
#         return snap.classify_base(base, pair_key=pair_key)

#     def get_pair_categories(self, ex1: str, ex2: str) -> Dict[str, Set[str]]:
#         snap = self._snapshot or self._load_cache()
#         if not snap:
#             return {ASSET_KIND_METAL: set(), ASSET_KIND_ACTION: set(), ASSET_KIND_OTHER: set()}
#         pair_key = canonical_pair_rule_key(ex1, ex2)
#         src = snap.pair_categories.get(pair_key) or {}
#         return {
#             ASSET_KIND_METAL: set(src.get(ASSET_KIND_METAL, frozenset())),
#             ASSET_KIND_ACTION: set(src.get(ASSET_KIND_ACTION, frozenset())),
#             ASSET_KIND_OTHER: set(src.get(ASSET_KIND_OTHER, frozenset())),
#         }

#     def _load_cache(self) -> Optional[SpecialAssetsSnapshot]:
#         if not self.cache_path.exists():
#             self._log("warning", f"[SPECIAL_ASSETS] Dynamic cache file not found at {self.cache_path}. Falling back to hardcoded defaults.")
#             return None
#         try:
#             data = json.loads(self.cache_path.read_text(encoding="utf-8"))
#             snap = self._snapshot_from_json(data)
#             self._snapshot = snap
#             self._log("info", f"[SPECIAL_ASSETS] Successfully loaded dynamic cache from {self.cache_path}. Metals: {len(snap.metals)}, Stocks: {len(snap.stocks)}")
#             return snap
#         except Exception as e:
#             self._log("warning", f"[SPECIAL_ASSETS] Failed to read dynamic cache at {self.cache_path}: {e}. Falling back to hardcoded defaults.")
#             return None

#     def _save_cache(self, snap: SpecialAssetsSnapshot) -> None:
#         payload = {
#             "updated_at_ms": int(snap.updated_at_ms),
#             "metals": sorted(snap.metals),
#             "stocks": sorted(snap.stocks),
#             "exchange_bases": {k: sorted(v) for k, v in sorted(snap.exchange_bases.items())},
#             "pair_categories": {
#                 k: {kk: sorted(vv) for kk, vv in sorted(v.items())}
#                 for k, v in sorted(snap.pair_categories.items())
#             },
#         }
#         self.cache_path.parent.mkdir(parents=True, exist_ok=True)
#         self.cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

#     def _snapshot_from_json(self, data: Dict[str, Any]) -> SpecialAssetsSnapshot:
#         exchange_bases = {
#             str(k).lower(): frozenset(normalize_base(x) for x in (v or []) if normalize_base(x))
#             for k, v in (data.get("exchange_bases") or {}).items()
#         }

#         pair_categories: Dict[str, Dict[str, frozenset[str]]] = {}
#         for pair_key, mapping in (data.get("pair_categories") or {}).items():
#             if not isinstance(mapping, dict):
#                 continue
#             pair_categories[str(pair_key).lower()] = {
#                 ASSET_KIND_METAL: frozenset(normalize_base(x) for x in (mapping.get(ASSET_KIND_METAL) or []) if normalize_base(x)),
#                 ASSET_KIND_ACTION: frozenset(normalize_base(x) for x in (mapping.get(ASSET_KIND_ACTION) or []) if normalize_base(x)),
#                 ASSET_KIND_OTHER: frozenset(normalize_base(x) for x in (mapping.get(ASSET_KIND_OTHER) or []) if normalize_base(x)),
#             }

#         metals = {normalize_base(x) for x in (data.get("metals") or []) if normalize_base(x)}
#         stocks = {normalize_base(x) for x in (data.get("stocks") or []) if normalize_base(x)}

#         default_m = _default_metals()
#         default_s = _default_stocks()

#         if not metals:
#             self._log("warning", "[SPECIAL_ASSETS] Dynamic cache has 0 metals. Pulling from hardcoded default metal list.")
#         if not stocks:
#             self._log("warning", "[SPECIAL_ASSETS] Dynamic cache has 0 stocks. Pulling from hardcoded default stock list.")

#         metals |= default_m
#         stocks |= default_s

#         # Force-usual override: remove tickers that are actually crypto, not stocks/metals
#         force_usual = _force_usual_set()
#         if force_usual:
#             metals -= force_usual
#             stocks -= force_usual

#         return SpecialAssetsSnapshot(
#             updated_at_ms=int(data.get("updated_at_ms") or 0),
#             metals=frozenset(sorted(metals)),
#             stocks=frozenset(sorted(stocks)),
#             exchange_bases=exchange_bases,
#             pair_categories=pair_categories,
#         )

#     async def _request_json(self, url: str, *, params: Optional[dict] = None, headers: Optional[dict] = None) -> Any:
#         last_err = None
#         for attempt in range(1, 6):
#             try:
#                 session = await self._get_session()
#                 async with session.get(url, params=params, headers=headers) as resp:
#                     if resp.status == 429:
#                         retry_after = resp.headers.get("Retry-After")
#                         wait_sec = float(retry_after) if retry_after and retry_after.isdigit() else min(5.0 * attempt, 30.0)
#                         await asyncio.sleep(wait_sec)
#                         continue
#                     txt = await resp.text()
#                     if resp.status >= 400:
#                         raise RuntimeError(f"HTTP {resp.status}: {txt[:300]}")
#                     try:
#                         return json.loads(txt)
#                     except Exception:
#                         return await resp.json(content_type=None)
#             except Exception as e:
#                 last_err = e
#                 await asyncio.sleep(min(1.5 * attempt, 5.0))
#         raise RuntimeError(f"request failed: {url} params={params} err={last_err}")

#     async def _fetch_and_build_snapshot(self) -> SpecialAssetsSnapshot:
#         default_metals = _default_metals()
#         default_stocks = _default_stocks()

#         exchanges_task = asyncio.create_task(self._fetch_exchange_bases())

#         cg_meta = self._build_coingecko_request_meta()
#         if cg_meta:
#             metals_task = asyncio.create_task(
#                 self._fetch_category_symbols(
#                     set(SPECIAL_ASSETS_METAL_CATEGORIES),
#                     cg_meta=cg_meta,
#                 )
#             )
#             stocks_task = asyncio.create_task(
#                 self._fetch_category_symbols(
#                     set(SPECIAL_ASSETS_STOCK_CATEGORIES),
#                     cg_meta=cg_meta,
#                 )
#             )
#             metals_extra, stocks_extra, exchange_bases = await asyncio.gather(
#                 metals_task,
#                 stocks_task,
#                 exchanges_task,
#             )
#         else:
#             metals_extra = set()
#             stocks_extra = set()
#             exchange_bases = await exchanges_task

#         metals = set(default_metals) | set(metals_extra)
#         stocks = set(default_stocks) | set(stocks_extra)

#         # Force-usual override: remove tickers that are actually crypto, not stocks/metals
#         force_usual = _force_usual_set()
#         if force_usual:
#             metals -= force_usual
#             stocks -= force_usual

#         pair_categories = self._build_pair_categories(exchange_bases, metals, stocks)

#         all_bases: Set[str] = set()
#         for bases in exchange_bases.values():
#             all_bases |= bases
        
#         det_m = len([x for x in all_bases if x in metals])
#         det_s = len([x for x in all_bases if x in stocks])
#         det_u = len(all_bases) - det_m - det_s
#         self._log("info", f"[SPECIAL_ASSETS] Determination summary across {len(exchange_bases)} exchanges ({len(all_bases)} unique bases): usual={det_u}, action={det_s}, metall={det_m}")

#         return SpecialAssetsSnapshot(
#             updated_at_ms=int(time.time() * 1000),
#             metals=frozenset(sorted(metals)),
#             stocks=frozenset(sorted(stocks)),
#             exchange_bases={k: frozenset(sorted(v)) for k, v in sorted(exchange_bases.items())},
#             pair_categories={
#                 k: {
#                     ASSET_KIND_METAL: frozenset(sorted(v.get(ASSET_KIND_METAL, set()))),
#                     ASSET_KIND_ACTION: frozenset(sorted(v.get(ASSET_KIND_ACTION, set()))),
#                     ASSET_KIND_OTHER: frozenset(sorted(v.get(ASSET_KIND_OTHER, set()))),
#                 }
#                 for k, v in sorted(pair_categories.items())
#             },
#         )
    
#     async def _fetch_category_symbols(
#         self,
#         category_ids: Set[str],
#         *,
#         cg_meta: tuple[str, Dict[str, str]],
#     ) -> Set[str]:
#         out: Set[str] = set()
#         if not category_ids:
#             return out

#         base_url, headers = cg_meta

#         # Demo-план ~30 req/min, поэтому не дергаем слишком быстро.
#         sleep_between_pages_sec = 1.25

#         for category_id in sorted(category_ids):
#             page = 1
#             while True:
#                 try:
#                     data = await self._request_json(
#                         f"{base_url}/coins/markets",
#                         params={
#                             "vs_currency": "usd",
#                             "category": category_id,
#                             "order": "market_cap_desc",
#                             "per_page": 250,
#                             "page": page,
#                             "sparkline": "false",
#                         },
#                         headers=headers,
#                     )
#                 except Exception as e:
#                     self._log(
#                         "warning",
#                         f"[SPECIAL_ASSETS] category fetch failed category={category_id} page={page}: {e}"
#                     )
#                     break

#                 if not isinstance(data, list) or not data:
#                     self._log(
#                         "info",
#                         f"[SPECIAL_ASSETS] category={category_id} completed at page={page} collected={len(out)}"
#                     )
#                     break

#                 batch: Set[str] = set()
#                 for item in data:
#                     if isinstance(item, dict) and item.get("symbol"):
#                         for c in clean_coingecko_symbol(item.get("symbol")):
#                             if c:
#                                 batch.add(c)
#                 out |= batch

#                 if len(data) < 250:
#                     break

#                 page += 1
#                 await asyncio.sleep(sleep_between_pages_sec)

#         return out

#     async def _fetch_exchange_bases(self) -> Dict[str, Set[str]]:
#         tasks = [asyncio.create_task(self._fetch_one_exchange_bases(ex)) for ex in sorted(set(self.enabled_exchanges)) if ex in EXCHANGE_ENDPOINTS]
#         rows = await asyncio.gather(*tasks, return_exceptions=True)
#         out: Dict[str, Set[str]] = {}
#         for row in rows:
#             if isinstance(row, Exception):
#                 continue
#             ex_name, bases = row
#             out[str(ex_name).lower()] = set(bases)
#         return out

#     async def _fetch_one_exchange_bases(self, ex_name: str):
#         url = EXCHANGE_ENDPOINTS[ex_name]
#         data = await self._request_json(url)
#         bases: Set[str] = set()
#         if ex_name == "binance":
#             for s in (data.get("symbols") or []):
#                 if isinstance(s, dict) and s.get("contractType") == "PERPETUAL" and s.get("status") == "TRADING":
#                     b = normalize_base(s.get("baseAsset", ""))
#                     if b:
#                         bases.add(b)
#         elif ex_name == "bitget":
#             for s in (data.get("data") or []):
#                 if isinstance(s, dict) and str(s.get("symbolType") or "").lower() == "perpetual":
#                     b = normalize_base(s.get("baseCoin") or s.get("symbol") or "")
#                     if b:
#                         bases.add(b)
#         elif ex_name == "kucoin":
#             for s in (data.get("data") or []):
#                 if isinstance(s, dict):
#                     b = normalize_base(s.get("symbol") or "")
#                     if b:
#                         bases.add(b)
#         elif ex_name == "phemex":
#             d = data.get("data", {})
#             perp_list = None
#             if isinstance(d, dict):
#                 for key in ("perpProductsV2", "perpProducts", "products"):
#                     if isinstance(d.get(key), list):
#                         perp_list = d.get(key)
#                         break
#             elif isinstance(d, list):
#                 perp_list = d
#             for s in (perp_list or []):
#                 if isinstance(s, dict):
#                     b = normalize_base(s.get("symbol") or "")
#                     if b:
#                         bases.add(b)
#         return ex_name, bases

#     @staticmethod
#     def _build_pair_categories(exchange_bases: Dict[str, Set[str]], metals: Set[str], stocks: Set[str]) -> Dict[str, Dict[str, Set[str]]]:
#         pairs: Dict[str, Dict[str, Set[str]]] = {}
#         names = sorted(exchange_bases.keys())
#         for i, a in enumerate(names):
#             for b in names[i + 1:]:
#                 common = set(exchange_bases.get(a, set())) & set(exchange_bases.get(b, set()))
#                 pair_key = canonical_pair_rule_key(a, b)
#                 pair_metals = {x for x in common if x in metals}
#                 pair_stocks = {x for x in common if x in stocks}
#                 pair_other = common - pair_metals - pair_stocks
#                 pairs[pair_key] = {
#                     ASSET_KIND_METAL: pair_metals,
#                     ASSET_KIND_ACTION: pair_stocks,
#                     ASSET_KIND_OTHER: pair_other,
#                 }
#         return pairs


# ============================================================
# File: API/special_assets.py
# Role: Кэширование и получение списков специальных активов (без CoinGecko)
# ============================================================

from __future__ import annotations

import asyncio
import json
import time
import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Set

import aiohttp

from consts import (
    ASSET_KIND_ACTION,
    ASSET_KIND_METAL,
    ASSET_KIND_OTHER,
    ENABLED_EXCHANGES,
    ROOT_DIR,
    SPECIAL_ASSETS_CACHE_FILE,
    SPECIAL_ASSETS_FALLBACK_ACTION_BASES,
    SPECIAL_ASSETS_FALLBACK_METAL_BASES,
    SPECIAL_ASSETS_FORCE_USUAL_BASES,
    SPECIAL_ASSETS_REFRESH_EVERY_SEC,
    SPECIAL_ASSETS_TIMEOUT_SEC,
    canonical_pair_rule_key,
)

EXCHANGE_ENDPOINTS = {
    "binance": "https://fapi.binance.com/fapi/v1/exchangeInfo",
    "bitget": "https://api.bitget.com/api/v2/mix/market/contracts?productType=usdt-futures",
    "kucoin": "https://api-futures.kucoin.com/api/v1/contracts/active",
    "phemex": "https://api.phemex.com/public/products",
}

@dataclass(frozen=True)
class SpecialAssetsSnapshot:
    updated_at_ms: int
    metals: frozenset[str]
    stocks: frozenset[str]
    exchange_bases: Dict[str, frozenset[str]]
    pair_categories: Dict[str, Dict[str, frozenset[str]]]

    def classify_base(self, base: str, *, pair_key: Optional[str] = None) -> str:
        b = normalize_base(base)
        if not b:
            return ASSET_KIND_OTHER
        if b in self.metals:
            return ASSET_KIND_METAL
        if b in self.stocks:
            return ASSET_KIND_ACTION
        if pair_key:
            cats = self.pair_categories.get(str(pair_key).lower()) or {}
            if b in cats.get(ASSET_KIND_METAL, frozenset()):
                return ASSET_KIND_METAL
            if b in cats.get(ASSET_KIND_ACTION, frozenset()):
                return ASSET_KIND_ACTION
        return ASSET_KIND_OTHER

def normalize_base(symbol: str) -> str:
    if not symbol:
        return ""
    s = str(symbol).upper().replace("_", "").replace("-", "").strip()
    for suf in ("USDTM", "USDCM", "USDT", "PERP", "USD"):
        if s.endswith(suf):
            base = s[:-len(suf)]
            return "BTC" if base == "XBT" else base
    return "BTC" if s == "XBT" else s

def _default_metals() -> Set[str]:
    return {normalize_base(x) for x in SPECIAL_ASSETS_FALLBACK_METAL_BASES if normalize_base(x)}

def _default_stocks() -> Set[str]:
    return {normalize_base(x) for x in SPECIAL_ASSETS_FALLBACK_ACTION_BASES if normalize_base(x)}

def _force_usual_set() -> Set[str]:
    return {normalize_base(x) for x in SPECIAL_ASSETS_FORCE_USUAL_BASES if normalize_base(x)}

class SpecialAssetsRegistry:
    def __init__(
        self,
        *,
        cache_path: str | Path | None = None,
        refresh_every_sec: int = SPECIAL_ASSETS_REFRESH_EVERY_SEC,
        timeout_sec: float = SPECIAL_ASSETS_TIMEOUT_SEC,
        enabled_exchanges: Optional[Iterable[str]] = None,
        logger=None,
    ):
        self.logger = logger
        self.cache_path = Path(cache_path or (ROOT_DIR / SPECIAL_ASSETS_CACHE_FILE))
        if not self.cache_path.is_absolute():
            self.cache_path = ROOT_DIR / self.cache_path
        self.refresh_every_sec = max(300, int(refresh_every_sec or SPECIAL_ASSETS_REFRESH_EVERY_SEC))
        self.timeout = aiohttp.ClientTimeout(total=float(timeout_sec or SPECIAL_ASSETS_TIMEOUT_SEC))
        self.enabled_exchanges = [str(x).strip().lower() for x in (enabled_exchanges or ENABLED_EXCHANGES or []) if str(x).strip()]
        self._lock = asyncio.Lock()
        self._session: aiohttp.ClientSession | None = None
        self._refresh_task: asyncio.Task | None = None
        self._snapshot: Optional[SpecialAssetsSnapshot] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is not None and not self._session.closed:
            return self._session
        connector = aiohttp.TCPConnector(limit=20, ttl_dns_cache=300, enable_cleanup_closed=True)
        self._session = aiohttp.ClientSession(timeout=self.timeout, connector=connector)
        return self._session

    async def aclose(self) -> None:
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            with contextlib.suppress(Exception):
                await self._refresh_task
            self._refresh_task = None
        if self._session is not None:
            with contextlib.suppress(Exception):
                await self._session.close()
            self._session = None

    def start_background_refresh(self) -> None:
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.create_task(self._refresh_loop(), name="special-assets-refresh")

    async def _refresh_loop(self) -> None:
        while True:
            try:
                await self.ensure_fresh(force=False)
            except Exception as e:
                self._log("warning", f"[SPECIAL_ASSETS] background refresh failed: {e}")
            await asyncio.sleep(max(300, int(self.refresh_every_sec)))

    def _log(self, level: str, msg: str) -> None:
        if not self.logger:
            return
        fn = getattr(self.logger, level, None)
        if callable(fn):
            try:
                fn(msg)
            except Exception:
                pass

    async def ensure_fresh(self, *, force: bool = False) -> SpecialAssetsSnapshot:
        async with self._lock:
            cached = self._snapshot or self._load_cache()
            now_ms = int(time.time() * 1000)
            if cached and not force:
                age_sec = max(0.0, (now_ms - cached.updated_at_ms) / 1000.0)
                if age_sec < float(self.refresh_every_sec):
                    self._snapshot = cached
                    return cached
            try:
                fresh = await self._fetch_and_build_snapshot()
                self._snapshot = fresh
                self._save_cache(fresh)
                return fresh
            except Exception as e:
                if cached:
                    return cached
                raise e

    def classify_base(self, base: str, *, ex1: Optional[str] = None, ex2: Optional[str] = None) -> str:
        snap = self._snapshot or self._load_cache()
        if not snap:
            return ASSET_KIND_OTHER
        pair_key = canonical_pair_rule_key(ex1 or "", ex2 or "") if ex1 and ex2 else None
        return snap.classify_base(base, pair_key=pair_key)

    def _load_cache(self) -> Optional[SpecialAssetsSnapshot]:
        if not self.cache_path.exists():
            return None
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return self._snapshot_from_json(data)
        except Exception:
            return None

    def _save_cache(self, snap: SpecialAssetsSnapshot) -> None:
        payload = {
            "updated_at_ms": int(snap.updated_at_ms),
            "metals": sorted(snap.metals),
            "stocks": sorted(snap.stocks),
            "exchange_bases": {k: sorted(v) for k, v in sorted(snap.exchange_bases.items())},
            "pair_categories": {k: {kk: sorted(vv) for kk, vv in v.items()} for k, v in snap.pair_categories.items()},
        }
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _snapshot_from_json(self, data: Dict[str, Any]) -> SpecialAssetsSnapshot:
        exchange_bases = {k: frozenset(v) for k, v in data.get("exchange_bases", {}).items()}
        pair_categories = {k: {kk: frozenset(vv) for kk, vv in v.items()} for k, v in data.get("pair_categories", {}).items()}
        metals = frozenset(data.get("metals", []))
        stocks = frozenset(data.get("stocks", []))
        return SpecialAssetsSnapshot(int(data.get("updated_at_ms", 0)), metals, stocks, exchange_bases, pair_categories)

    async def _request_json(self, url: str) -> Any:
        for attempt in range(1, 4):
            try:
                session = await self._get_session()
                async with session.get(url) as resp:
                    if resp.status == 200:
                        return await resp.json(content_type=None)
            except Exception:
                await asyncio.sleep(min(1.5 * attempt, 5.0))
        return {}

    async def _fetch_and_build_snapshot(self) -> SpecialAssetsSnapshot:
        exchange_bases = await self._fetch_exchange_bases()
        metals = _default_metals()
        stocks = _default_stocks()

        force_usual = _force_usual_set()
        if force_usual:
            metals -= force_usual
            stocks -= force_usual

        pair_categories = self._build_pair_categories(exchange_bases, metals, stocks)

        return SpecialAssetsSnapshot(
            updated_at_ms=int(time.time() * 1000),
            metals=frozenset(sorted(metals)),
            stocks=frozenset(sorted(stocks)),
            exchange_bases={k: frozenset(sorted(v)) for k, v in exchange_bases.items()},
            pair_categories={k: {kk: frozenset(vv) for kk, vv in v.items()} for k, v in pair_categories.items()},
        )

    async def _fetch_exchange_bases(self) -> Dict[str, Set[str]]:
        tasks = [asyncio.create_task(self._fetch_one_exchange_bases(ex)) for ex in set(self.enabled_exchanges) if ex in EXCHANGE_ENDPOINTS]
        rows = await asyncio.gather(*tasks, return_exceptions=True)
        out = {}
        for row in rows:
            if not isinstance(row, Exception):
                out[row[0]] = row[1]
        return out

    async def _fetch_one_exchange_bases(self, ex_name: str):
        url = EXCHANGE_ENDPOINTS[ex_name]
        data = await self._request_json(url)
        bases = set()
        if ex_name == "binance":
            for s in (data.get("symbols") or []):
                if isinstance(s, dict) and s.get("contractType") == "PERPETUAL":
                    if b := normalize_base(s.get("baseAsset", "")): bases.add(b)
        elif ex_name == "bitget":
            for s in (data.get("data") or []):
                if isinstance(s, dict) and str(s.get("symbolType") or "").lower() == "perpetual":
                    if b := normalize_base(s.get("baseCoin") or s.get("symbol") or ""): bases.add(b)
        elif ex_name == "kucoin":
            for s in (data.get("data") or []):
                if isinstance(s, dict):
                    if b := normalize_base(s.get("symbol") or ""): bases.add(b)
        elif ex_name == "phemex":
            d = data.get("data", {})
            perp_list = d.get("perpProductsV2") or d.get("perpProducts") or d.get("products") or (d if isinstance(d, list) else [])
            for s in perp_list:
                if isinstance(s, dict):
                    if b := normalize_base(s.get("symbol") or ""): bases.add(b)
        return ex_name, bases

    @staticmethod
    def _build_pair_categories(exchange_bases: Dict[str, Set[str]], metals: Set[str], stocks: Set[str]) -> Dict[str, Dict[str, Set[str]]]:
        pairs = {}
        names = sorted(exchange_bases.keys())
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                common = set(exchange_bases.get(a, set())) & set(exchange_bases.get(b, set()))
                pairs[canonical_pair_rule_key(a, b)] = {
                    ASSET_KIND_METAL: {x for x in common if x in metals},
                    ASSET_KIND_ACTION: {x for x in common if x in stocks},
                    ASSET_KIND_OTHER: common - metals - stocks,
                }
        return pairs