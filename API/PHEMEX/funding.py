
# ============================================================
# FILE: API/PHEMEX/funding.py
# ROLE: Phemex USDT-M Perpetual funding via REST (public)
# ENDPOINT: GET https://api.phemex.com/contract-biz/public/real-funding-rates
# NOTES:
#   Phemex paginates this endpoint. To fetch ALL symbols you must iterate pages.
# NOTE: Single responsibility: ONLY funding.
# ============================================================

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import time

import aiohttp


@dataclass(frozen=True)
class FundingInfo:
    symbol: str
    funding_rate: float
    next_funding_time_ms: int


class PhemexFunding:
    """Public funding client for Phemex contracts.

    Uses a shared aiohttp.ClientSession.
    """

    BASE_URL = "https://api.phemex.com"

    def __init__(self, timeout_sec: float = 20.0, retries: int = 3):
        self._timeout = aiohttp.ClientTimeout(total=float(timeout_sec))
        self._retries = int(retries)
        self._session: aiohttp.ClientSession | None = None
        self._session_lock = asyncio.Lock()
        
        # Кэш для реальных интервалов фандинга
        self._intervals_cache: Dict[str, int] = {}
        self._intervals_cache_ts: float = 0.0

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is not None and not self._session.closed:
            return self._session
        async with self._session_lock:
            if self._session is not None and not self._session.closed:
                return self._session
            connector = aiohttp.TCPConnector(limit=50, ttl_dns_cache=300, enable_cleanup_closed=True)
            self._session = aiohttp.ClientSession(timeout=self._timeout, connector=connector)
            return self._session

    async def aclose(self) -> None:
        if self._session is not None:
            try:
                await self._session.close()
            except Exception:
                pass
        self._session = None

    async def _get_json(self, path: str, params: Optional[dict] = None) -> Any:
        url = f"{self.BASE_URL}{path}"
        last_err: Optional[Exception] = None

        for attempt in range(1, self._retries + 1):
            try:
                session = await self._get_session()
                async with session.get(url, params=params) as resp:
                    text = await resp.text()
                    if resp.status != 200:
                        raise RuntimeError(f"HTTP {resp.status}: {text}")
                    return await resp.json()
            except Exception as e:
                last_err = e
                s = (str(e) or "").lower()
                if "session is closed" in s or "connector is closed" in s or "clientconnectorerror" in s:
                    self._session = None
                if attempt < self._retries:
                    await asyncio.sleep(0.4 * attempt)
                else:
                    break

        raise RuntimeError(f"Phemex funding failed: {path} params={params} err={last_err}")

    @staticmethod
    def _to_float(v: Any, default: float = 0.0) -> float:
        try:
            return float(v)
        except Exception:
            return default

    @staticmethod
    def _to_int(v: Any, default: int = 0) -> int:
        try:
            return int(v)
        except Exception:
            return default

    def _parse_one(self, obj: Dict[str, Any], intervals: Dict[str, int], now_ms: int) -> Optional[FundingInfo]:
        sym = obj.get("symbol")
        if not sym:
            return None
        sym_str = str(sym)

        # Берем то, что отдала биржа (часто врет про 8ч)
        nxt_ms = self._to_int(obj.get("nextFundingTime") or obj.get("nextfundingTime"), 0)
        
        # Исправляем время на основе реального fundingInterval
        interval_sec = intervals.get(sym_str)
        if interval_sec:
            interval_ms = interval_sec * 1000
            # Математически находим следующий кратный интервал от 00:00 UTC
            nxt_ms = ((now_ms // interval_ms) + 1) * interval_ms

        return FundingInfo(
            symbol=sym_str,
            funding_rate=self._to_float(obj.get("fundingRate"), 0.0),
            next_funding_time_ms=nxt_ms,
        )

    @staticmethod
    def _extract_rows(payload: Any) -> List[Dict[str, Any]]:
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]

        if not isinstance(payload, dict):
            return []

        data = payload.get("data")
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            for k in ("rows", "result", "list"):
                v = data.get(k)
                if isinstance(v, list):
                    return [x for x in v if isinstance(x, dict)]

        for k in ("rows", "result", "list"):
            v = payload.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]

        return []

    async def get_all(self) -> List[FundingInfo]:
        out: List[FundingInfo] = []
        page_num = 1
        page_size = 200

        # Получаем реальные интервалы и фиксируем текущее время
        intervals = await self._get_funding_intervals()
        now_ms = int(time.time() * 1000)

        while True:
            payload = await self._get_json(
                "/contract-biz/public/real-funding-rates",
                params={"symbol": "ALL", "pageNum": page_num, "pageSize": page_size},
            )
            rows = self._extract_rows(payload)
            if not rows:
                break

            for obj in rows:
                fi = self._parse_one(obj, intervals, now_ms)
                if fi:
                    out.append(fi)

            if len(rows) < page_size:
                break
            page_num += 1

        return out
    
    async def _get_funding_intervals(self) -> Dict[str, int]:
        now = time.time()
        if now - self._intervals_cache_ts < 3600 and self._intervals_cache:
            return self._intervals_cache

        try:
            data = await self._get_json("/public/products")
            root = data.get("data") if isinstance(data, dict) else None
            if root is None:
                return self._intervals_cache

            intervals = {}
            items_to_check = []
            
            # Обработка разных вариантов структуры Phemex API
            if isinstance(root, list):
                items_to_check = root
            elif isinstance(root, dict):
                arr = root.get("perpProductsV2") or root.get("perpProducts") or root.get("products") or []
                items_to_check = list(arr) if isinstance(arr, list) else []
                for _, v in root.items():
                    if isinstance(v, list):
                        items_to_check.extend(v)

            for it in items_to_check:
                if isinstance(it, dict):
                    sym = it.get("symbol")
                    fi = it.get("fundingInterval")
                    if sym and fi:
                        try:
                            intervals[str(sym)] = int(fi)
                        except (ValueError, TypeError):
                            pass
            
            if intervals:
                self._intervals_cache = intervals
                self._intervals_cache_ts = now
        except Exception:
            pass 

        return self._intervals_cache
    
    # async def _get_funding_intervals(self) -> Dict[str, int]:
    #     now = time.time()
    #     # Кэшируем данные на 1 час
    #     if now - self._intervals_cache_ts < 3600 and self._intervals_cache:
    #         return self._intervals_cache

    #     try:
    #         data = await self._get_json("/public/products")
    #         root = data.get("data") if isinstance(data, dict) else None
    #         if not isinstance(root, dict):
    #             return self._intervals_cache

    #         intervals = {}
    #         # Собираем массивы контрактов (фоллбэк как в symbol.py)
    #         arr = root.get("perpProductsV2") or root.get("perpProducts") or []
    #         items_to_check = list(arr) if isinstance(arr, list) else []
    #         for _, v in root.items():
    #             if isinstance(v, list):
    #                 items_to_check.extend(v)

    #         for it in items_to_check:
    #             if isinstance(it, dict):
    #                 sym = it.get("symbol")
    #                 fi = it.get("fundingInterval")
    #                 if sym and fi:
    #                     try:
    #                         intervals[str(sym)] = int(fi)
    #                     except (ValueError, TypeError):
    #                         pass
            
    #         if intervals:
    #             self._intervals_cache = intervals
    #             self._intervals_cache_ts = now
    #     except Exception:
    #         pass  # При сетевой ошибке просто возвращаем старый кэш или пустой словарь

    #     return self._intervals_cache

# ----------------------------
# SELF TEST
# ----------------------------
async def _main():
    api = PhemexFunding()
    rows = await api.get_all()
    print(f"Funding rows: {len(rows)}")
    for r in rows[:15]:
        print(r)
    await api.aclose()


if __name__ == "__main__":
    asyncio.run(_main())
