"""Thin async Finnhub client using only the stdlib (urllib in a thread pool).

Docs: https://finnhub.io/docs/api
Free tier is US-market focused; some endpoints (e.g. candles, news-sentiment)
may require a paid plan and will raise/return empty — callers should degrade
gracefully.
"""
from __future__ import annotations

import asyncio
import json
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import FINNHUB_API_KEY

_BASE = "https://finnhub.io/api/v1"


class FinnhubError(RuntimeError):
    pass


def _get_sync(path: str, params: dict[str, Any]) -> Any:
    params = {**params, "token": FINNHUB_API_KEY}
    url = f"{_BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "agentic-mcp-ops-vanilla/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise FinnhubError(f"HTTP {e.code} for {path}: {e.reason}") from e
    except Exception as e:  # noqa: BLE001
        raise FinnhubError(f"request failed for {path}: {e}") from e


async def _get(path: str, params: dict[str, Any]) -> Any:
    return await asyncio.to_thread(_get_sync, path, params)


# --- symbol resolution (name -> ticker), cached ---------------------------
_symbol_cache: dict[str, str] = {}


async def resolve_symbol(entity: str) -> str:
    entity = entity.strip()
    key = entity.lower()
    if key in _symbol_cache:
        return _symbol_cache[key]
    # If it already looks like a ticker, use as-is.
    if entity.isupper() and 1 <= len(entity) <= 6:
        _symbol_cache[key] = entity
        return entity
    data = await _get("/search", {"q": entity})
    results = (data or {}).get("result", [])
    symbol = results[0]["symbol"] if results else entity.upper()
    _symbol_cache[key] = symbol
    return symbol


# --- market data ----------------------------------------------------------
async def quote(symbol: str) -> dict[str, Any]:
    return await _get("/quote", {"symbol": symbol})


async def metrics(symbol: str) -> dict[str, Any]:
    return await _get("/stock/metric", {"symbol": symbol, "metric": "all"})


async def profile(symbol: str) -> dict[str, Any]:
    return await _get("/stock/profile2", {"symbol": symbol})


async def company_news(symbol: str, days: int = 30) -> list[dict[str, Any]]:
    to = datetime.now(timezone.utc).date()
    frm = to - timedelta(days=days)
    data = await _get("/company-news",
                      {"symbol": symbol, "from": frm.isoformat(),
                       "to": to.isoformat()})
    return data or []


async def news_sentiment(symbol: str) -> dict[str, Any]:
    """May require a paid plan; callers should catch FinnhubError."""
    return await _get("/news-sentiment", {"symbol": symbol})
