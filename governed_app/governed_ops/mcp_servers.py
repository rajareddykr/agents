"""MCP-style tool servers.

Each tool is a plain async function decorated with ``@governed(action="…")``.
That is the ENTIRE governance surface used here: no ``AutoKernel``, no manual
``authorize()``, no policy plumbing. The decorator raises ``AGTBlocked`` when
the CP-side rule engine denies; callers convert that into a graceful BLOCKED
event via ``try/except`` (see ``agents.py``).

Action name convention: ``<server>.<tool>``. It is what your policy YAML
addresses via ``field: action, operator: eq, value: fin_mcp.get_quote``.
"""
from __future__ import annotations

import asyncio
import random
from typing import Any

from agt_sdk import governed

from .events import BUS, Event, EventType


# ── FIN MCP tools ───────────────────────────────────────────────────────────

@governed(action="fin_mcp.get_quote")
async def fin_get_quote(entity: str) -> dict[str, Any]:
    """Return a mock price quote. ``entity`` is a stock ticker or company."""
    await asyncio.sleep(random.uniform(0.2, 0.5))
    return {"tool": "get_quote", "entity": entity,
            "price": round(100 + random.random() * 500, 2),
            "currency": "USD", "source": "mock"}


@governed(action="fin_mcp.get_fundamentals")
async def fin_get_fundamentals(entity: str) -> dict[str, Any]:
    await asyncio.sleep(random.uniform(0.3, 0.7))
    return {"tool": "get_fundamentals", "entity": entity,
            "pe": round(10 + random.random() * 30, 2),
            "revenue_growth_pct": round(random.random() * 20, 1),
            "source": "mock"}


@governed(action="fin_mcp.get_price_trend")
async def fin_get_price_trend(entity: str) -> dict[str, Any]:
    await asyncio.sleep(random.uniform(0.2, 0.5))
    trend = random.choice(["up", "down", "flat"])
    return {"tool": "get_price_trend", "entity": entity, "trend": trend,
            "change_pct": round((random.random() - 0.5) * 10, 2), "source": "mock"}


# ── NEWS MCP tools ──────────────────────────────────────────────────────────

@governed(action="news_mcp.search_news")
async def news_search(entity: str) -> dict[str, Any]:
    await asyncio.sleep(random.uniform(0.2, 0.5))
    return {"tool": "search_news", "entity": entity,
            "headlines": [f"{entity} beats quarterly estimates",
                          f"{entity} announces new partnership"],
            "source": "mock"}


@governed(action="news_mcp.get_sentiment")
async def news_sentiment(entity: str) -> dict[str, Any]:
    await asyncio.sleep(random.uniform(0.2, 0.5))
    return {"tool": "get_sentiment", "entity": entity,
            "sentiment": random.choice(["bullish", "bearish", "neutral"]),
            "score": round(random.random(), 2), "source": "mock"}


# ── Event-emitting wrapper ──────────────────────────────────────────────────
#
# Each tool call publishes MCP_IN and MCP_OUT events so a caller can watch the
# request/response edge. Wraps the raw decorated call — the decorator's block
# path raises AGTBlocked, which the caller (agents.py) catches.

MCP_TOOLS: dict[str, dict[str, Any]] = {
    "fin_mcp": {
        "label": "FIN MCP",
        "tools": {
            "get_quote":        fin_get_quote,
            "get_fundamentals": fin_get_fundamentals,
            "get_price_trend":  fin_get_price_trend,
        },
    },
    "news_mcp": {
        "label": "NEWS MCP",
        "tools": {
            "search_news":   news_search,
            "get_sentiment": news_sentiment,
        },
    },
}


async def call_tool(server: str, tool: str, session: str, caller: str,
                    **kwargs) -> dict[str, Any]:
    """Invoke ``server.tool(**kwargs)`` and emit MCP_IN / MCP_OUT events."""
    spec = MCP_TOOLS[server]
    fn = spec["tools"][tool]
    action = f"{server}.{tool}"

    BUS.publish(Event(type=EventType.MCP_IN, source=caller, target=server,
                      session=session, message=f"call {action}({kwargs})",
                      data={"tool": tool, "args": kwargs}))
    result = await fn(**kwargs)   # ← @governed runs here; raises AGTBlocked on deny
    BUS.publish(Event(type=EventType.MCP_OUT, source=server, target=caller,
                      session=session, message=f"{action} -> ok",
                      data={"tool": tool, "result": result}))
    return result
