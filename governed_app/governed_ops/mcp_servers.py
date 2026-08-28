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
import os
import random
import time
from typing import Any

from agt_sdk import governed
from agt_sdk.exceptions import AGTBlocked

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


async def _await_grant(action: str, params: dict, *, timeout_s: float | None = None) -> bool:
    """Block until a human grants the parked approval for ``action``, else False.

    R4.1 block-and-resume. Only waits for a ``require_approval`` HOLD — a hard
    ``deny`` returns False at once, so a mission never hangs on something no
    human will approve. Needs ``AGT_HITL_APPROVALS=true`` (that is what gives
    the kernel its ``_approvals`` client). The SDK already parked the request
    when it raised AGTBlocked; here we just wait for the grant.

    Bounded by ``AGT_APPROVAL_WAIT_SECONDS`` (default 90) so the wait fits
    inside the coordinator's 120s HTTP timeout to this worker — approve within
    the window and the single run resumes; miss it and the run returns blocked.
    """
    if timeout_s is None:
        timeout_s = float(os.environ.get("AGT_APPROVAL_WAIT_SECONDS", "90"))
    try:
        from agt_sdk import AutoKernel
        from agt_sdk._approvals import detect_require_approval
    except Exception:
        return False
    kernel = AutoKernel.instance()
    approvals = getattr(kernel, "_approvals", None)
    if approvals is None:
        return False  # HITL off → cannot wait
    try:
        if not detect_require_approval(kernel._kernel, action, params):
            return False  # a hard deny, not an approval hold
    except Exception:
        return False
    try:
        await approvals.park(action, reason=f"{action}: awaiting human approval")
    except Exception:
        pass
    interval = float(os.environ.get("AGT_APPROVAL_POLL_SECONDS", "10"))
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        await approvals.poll_once()
        if approvals.grant_active(action):
            return True
        await asyncio.sleep(interval)
    return False


async def call_tool(server: str, tool: str, session: str, caller: str,
                    **kwargs) -> dict[str, Any]:
    """Invoke ``server.tool(**kwargs)`` and emit MCP_IN / MCP_OUT events.

    R4.1 block-and-resume: if the call is held for approval, wait for a human
    grant and resume automatically, so a single mission run completes instead
    of returning blocked. A hard deny still raises AGTBlocked to the caller.
    """
    spec = MCP_TOOLS[server]
    fn = spec["tools"][tool]
    action = f"{server}.{tool}"

    BUS.publish(Event(type=EventType.MCP_IN, source=caller, target=server,
                      session=session, message=f"call {action}({kwargs})",
                      data={"tool": tool, "args": kwargs}))
    try:
        result = await fn(**kwargs)   # ← @governed runs here; raises AGTBlocked on deny
    except AGTBlocked:
        # Held for approval? Wait for the grant, then retry — the SDK injects
        # the grant on the next call so the require_approval rule now permits.
        BUS.publish(Event(type=EventType.MCP_IN, source=caller, target=server,
                          session=session, message=f"{action} awaiting approval …",
                          data={"tool": tool, "waiting_for_approval": True}))
        if not await _await_grant(action, dict(kwargs)):
            raise
        result = await fn(**kwargs)
    BUS.publish(Event(type=EventType.MCP_OUT, source=server, target=caller,
                      session=session, message=f"{action} -> ok",
                      data={"tool": tool, "result": result}))
    return result
