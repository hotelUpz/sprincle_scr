# ============================================================
# FILE: API/special_assets.py
# ROLE: Parser and registry for stocks and metals via Exchange API
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
    SPECIAL_ASSETS_FORCE_USUAL_BASES,
    SPECIAL_ASSETS_REFRESH_EVERY_SEC,
    SPECIAL_ASSETS_TIMEOUT_SEC,
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

    def classify_base(self, base: str) -> str:
        b = normalize_base(base)
        if not b:
            return ASSET_KIND_OTHER

        if b in self.metals:
            return ASSET_KIND_METAL
        if b in self.stocks:
            return ASSET_KIND_ACTION
            
        return ASSET_KIND_OTHER

def normalize_base(symbol: str) -> str:
    if not symbol:
        return ""
    s = str(symbol).upper().replace("_", "").replace("-", "").replace(".", "").strip()
    for suf in ("USDTM", "USDCM", "USDT", "PERP", "USD"):
        if s.endswith(suf):
            base = s[:-len(suf)]
            return "BTC" if base == "XBT" else base
    return "BTC" if s == "XBT" else s

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
            except asyncio.CancelledError:
                raise
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

    def snapshot(self) -> Optional[SpecialAssetsSnapshot]:
        return self._snapshot

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
                if fresh and (fresh.metals or fresh.stocks):
                    self._snapshot = fresh
                    self._save_cache(fresh)
                    self._log("info", f"[SPECIAL_ASSETS] cache refreshed RWA={len(fresh.metals) + len(fresh.stocks)}")
                    return fresh
                if cached:
                    self._log("warning", "[SPECIAL_ASSETS] refresh returned empty data, keeping previous cache")
                    self._snapshot = cached
                    return cached
                raise RuntimeError("special assets refresh returned empty data and no cache exists")
            except Exception as e:
                if cached:
                    self._log("warning", f"[SPECIAL_ASSETS] refresh failed, keeping previous cache: {e}")
                    self._snapshot = cached
                    return cached
                raise

    def classify_base(self, base: str, *, ex1: Optional[str] = None, ex2: Optional[str] = None) -> str:
        # Игнорируем ex1 и ex2, так как в новом v6 мы используем только глобальные RWA списки
        snap = self._snapshot or self._load_cache()
        if not snap:
            return ASSET_KIND_OTHER
        return snap.classify_base(base)

    def _load_cache(self) -> Optional[SpecialAssetsSnapshot]:
        if not self.cache_path.exists():
            return None
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            
            metals = {normalize_base(x) for x in (data.get("metals") or []) if normalize_base(x)}
            stocks = {normalize_base(x) for x in (data.get("stocks") or []) if normalize_base(x)}
            
            snap = SpecialAssetsSnapshot(
                updated_at_ms=int(data.get("updated_at_ms") or 0),
                metals=frozenset(sorted(metals)),
                stocks=frozenset(sorted(stocks)),
            )
            self._snapshot = snap
            self._log("info", f"[SPECIAL_ASSETS] Successfully loaded dynamic cache from {self.cache_path}. RWA: {len(snap.metals) + len(snap.stocks)}")
            return snap
        except Exception as e:
            self._log("warning", f"[SPECIAL_ASSETS] Failed to read dynamic cache at {self.cache_path}: {e}")
            return None

    def _save_cache(self, snap: SpecialAssetsSnapshot) -> None:
        payload = {
            "updated_at_ms": int(snap.updated_at_ms),
            "metals": sorted(snap.metals),
            "stocks": sorted(snap.stocks),
        }
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    async def _request_json(self, url: str, *, params: Optional[dict] = None, headers: Optional[dict] = None) -> Any:
        last_err = None
        for attempt in range(1, 4):
            try:
                session = await self._get_session()
                async with session.get(url, params=params, headers=headers) as resp:
                    if resp.status == 429:
                        retry_after = resp.headers.get("Retry-After")
                        wait_sec = float(retry_after) if retry_after and retry_after.isdigit() else min(5.0 * attempt, 30.0)
                        await asyncio.sleep(wait_sec)
                        continue
                    txt = await resp.text()
                    if resp.status >= 400:
                        raise RuntimeError(f"HTTP {resp.status}: {txt[:300]}")
                    try:
                        return json.loads(txt)
                    except Exception:
                        return await resp.json(content_type=None)
            except Exception as e:
                last_err = e
                await asyncio.sleep(min(1.5 * attempt, 5.0))
        raise RuntimeError(f"request failed: {url} params={params} err={last_err}")

    async def _fetch_and_build_snapshot(self) -> SpecialAssetsSnapshot:
        metals: Set[str] = set()
        stocks: Set[str] = set()
        
        # 1. Phemex
        try:
            d = await self._request_json(EXCHANGE_ENDPOINTS["phemex"])
            perp_list = d.get("data", {}).get("perpProductsV2", [])
            if not perp_list and isinstance(d.get("data"), list):
                perp_list = d.get("data")
            for p in perp_list:
                if isinstance(p, dict):
                    grp = p.get("perpProductSubGroup", "")
                    b = normalize_base(p.get("symbol", ""))
                    if b:
                        if grp == "Metals":
                            metals.add(b)
                        elif grp == "Stocks":
                            stocks.add(b)
        except Exception as e:
            self._log("warning", f"[SPECIAL_ASSETS] Phemex API fetch failed: {e}")

        # 2. Bitget
        try:
            d = await self._request_json(EXCHANGE_ENDPOINTS["bitget"])
            for p in d.get("data", []):
                if isinstance(p, dict) and p.get("isRwa") == "YES":
                    b = normalize_base(p.get("baseCoin") or p.get("symbol") or "")
                    if b:
                        stocks.add(b)
        except Exception as e:
            self._log("warning", f"[SPECIAL_ASSETS] Bitget API fetch failed: {e}")

        # 3. Kucoin
        try:
            d = await self._request_json(EXCHANGE_ENDPOINTS["kucoin"])
            for p in d.get("data", []):
                if isinstance(p, dict):
                    b = normalize_base(p.get("symbol", ""))
                    if b:
                        mt = p.get("marketType", "")
                        if mt in ("NASDAQ", "NYSE"):
                            stocks.add(b)
                        elif b.startswith("XAU") or b.startswith("XAG"):
                            metals.add(b)
        except Exception as e:
            self._log("warning", f"[SPECIAL_ASSETS] Kucoin API fetch failed: {e}")

        # 4. Binance
        try:
            d = await self._request_json(EXCHANGE_ENDPOINTS["binance"])
            for t in d.get("symbols", []):
                t_type = t.get("underlyingType")
                t_sub = str(t.get("underlyingSubType"))
                b = normalize_base(t.get("symbol", ""))
                if b:
                    if t_type == "COMMODITY":
                        metals.add(b)
                    elif t_type in ("EQUITY", "KR_EQUITY") or "RWA" in t_sub:
                        stocks.add(b)
        except Exception as e:
            self._log("warning", f"[SPECIAL_ASSETS] Binance API fetch failed: {e}")

        # Force-usual override: remove tickers that are actually crypto, not stocks/metals
        force_usual = _force_usual_set()
        if force_usual:
            metals -= force_usual
            stocks -= force_usual

        self._log("info", f"[SPECIAL_ASSETS] Fetched Native RWA: metals={len(metals)}, stocks={len(stocks)}")

        return SpecialAssetsSnapshot(
            updated_at_ms=int(time.time() * 1000),
            metals=frozenset(sorted(metals)),
            stocks=frozenset(sorted(stocks)),
        )