"""FIN MCP — financial data. Uses Finnhub when a key is configured, else mock."""
from __future__ import annotations

import hashlib
import random
from typing import Any

from .base import MCPServer
from .. import config
from ..providers import finnhub


def _seed(text: str) -> random.Random:
    h = int(hashlib.sha256(text.encode()).hexdigest(), 16)
    return random.Random(h)


class FinMCP(MCPServer):
    node_id = "fin_mcp"
    label = "FIN MCP"

    def register_tools(self) -> None:
        @self.tool("get_quote", "Latest price + day change for a ticker/entity")
        async def get_quote(entity: str) -> dict[str, Any]:
            if config.data_mode() == "live":
                try:
                    sym = await finnhub.resolve_symbol(entity)
                    q = await finnhub.quote(sym)
                    prof = await finnhub.profile(sym)
                    return {
                        "entity": entity, "symbol": sym,
                        "price": q.get("c"),
                        "day_change_pct": q.get("dp"),
                        "currency": prof.get("currency", "USD"),
                        "volume": None, "source": "finnhub",
                    }
                except Exception as e:  # noqa: BLE001
                    return {**_mock_quote(entity), "source": f"mock (finnhub error: {e})"}
            return {**_mock_quote(entity), "source": "mock"}

        @self.tool("get_fundamentals", "Key fundamental ratios for an entity")
        async def get_fundamentals(entity: str) -> dict[str, Any]:
            if config.data_mode() == "live":
                try:
                    sym = await finnhub.resolve_symbol(entity)
                    m = (await finnhub.metrics(sym)).get("metric", {})
                    prof = await finnhub.profile(sym)
                    return {
                        "entity": entity, "symbol": sym,
                        "pe_ratio": m.get("peTTM") or m.get("peBasicExclExtraTTM"),
                        "market_cap_bn": (prof.get("marketCapitalization") or 0) / 1000
                            if prof.get("marketCapitalization") else None,
                        "roe_pct": m.get("roeTTM"),
                        "debt_to_equity": m.get("totalDebt/totalEquityAnnual")
                            or m.get("longTermDebt/equityAnnual"),
                        "dividend_yield_pct": m.get("dividendYieldIndicatedAnnual"),
                        "source": "finnhub",
                    }
                except Exception as e:  # noqa: BLE001
                    return {**_mock_fundamentals(entity), "source": f"mock (finnhub error: {e})"}
            return {**_mock_fundamentals(entity), "source": "mock"}

        @self.tool("get_price_trend", "Trend + volatility signal")
        async def get_price_trend(entity: str, days: int = 30) -> dict[str, Any]:
            if config.data_mode() == "live":
                try:
                    sym = await finnhub.resolve_symbol(entity)
                    q = await finnhub.quote(sym)
                    m = (await finnhub.metrics(sym)).get("metric", {})
                    hi, lo = m.get("52WeekHigh"), m.get("52WeekLow")
                    price = q.get("c")
                    pos = None
                    if hi and lo and price and hi != lo:
                        pos = round((price - lo) / (hi - lo), 2)
                    direction = "up" if (q.get("dp") or 0) >= 0 else "down"
                    return {
                        "entity": entity, "symbol": sym, "days": days,
                        "trend": direction,
                        "range_position": pos,  # 0=52w low, 1=52w high
                        "week52_high": hi, "week52_low": lo,
                        "volatility": m.get("beta"),
                        "source": "finnhub",
                    }
                except Exception as e:  # noqa: BLE001
                    return {**_mock_trend(entity, days), "source": f"mock (finnhub error: {e})"}
            return {**_mock_trend(entity, days), "source": "mock"}


# --- deterministic mock generators ---------------------------------------
def _mock_quote(entity: str) -> dict[str, Any]:
    r = _seed("quote:" + entity)
    return {"entity": entity, "price": round(r.uniform(80, 2400), 2),
            "day_change_pct": round(r.uniform(-4.5, 4.5), 2),
            "currency": "INR" if r.random() > 0.5 else "USD",
            "volume": r.randint(1_00_000, 90_00_000)}


def _mock_fundamentals(entity: str) -> dict[str, Any]:
    r = _seed("fund:" + entity)
    return {"entity": entity, "pe_ratio": round(r.uniform(8, 45), 1),
            "market_cap_bn": round(r.uniform(5, 900), 1),
            "roe_pct": round(r.uniform(6, 28), 1),
            "debt_to_equity": round(r.uniform(0.1, 2.4), 2),
            "dividend_yield_pct": round(r.uniform(0, 4.5), 2)}


def _mock_trend(entity: str, days: int) -> dict[str, Any]:
    r = _seed(f"trend:{entity}:{days}")
    series = [round(r.uniform(90, 110), 2) for _ in range(min(days, 30))]
    return {"entity": entity, "days": days,
            "trend": "up" if series[-1] >= series[0] else "down",
            "volatility": round(r.uniform(0.1, 0.6), 2),
            "sample_series": series}
