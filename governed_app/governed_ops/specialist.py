"""The two worker specialists — Financial and Research.

Each specialist runs in its OWN process (see ``worker_app.py``). Governance
lands only through ``@governed`` — the CP policy decides which of the
worker's tool calls are allowed.

No ``AutoKernel``, no manual ``authorize()``. Just the decorator.
"""
from __future__ import annotations

import random
from typing import Any

from agt_sdk import governed
from agt_sdk.exceptions import AGTBlocked

from .events import BUS, Event, EventType
from .mcp_servers import call_tool


class FinancialAgent:
    """Runs in the ``fin_agent`` process. Owns FIN MCP tool calls."""

    node_id = "fin_agent"
    label = "Financial Agent"

    @staticmethod
    @governed(action="fin_agent.analyze")
    async def analyze(entity: str, session: str) -> dict[str, Any]:
        results: dict[str, Any] = {"agent": FinancialAgent.label,
                                   "entity": entity, "checks": []}
        for tool in ("get_quote", "get_fundamentals", "get_price_trend"):
            try:
                r = await call_tool("fin_mcp", tool, session,
                                    caller=FinancialAgent.node_id, entity=entity)
                results["checks"].append(r)
            except AGTBlocked as e:
                _emit_blocked("fin_mcp", session, action=f"fin_mcp.{tool}", reason=str(e))
                results["checks"].append({"tool": tool, "blocked": True,
                                          "reason": str(e)})

        completed = sum("blocked" not in c for c in results["checks"])
        results["verdict"] = ("constructive" if completed and random.random() > 0.5
                              else ("cautious" if completed else "blocked"))
        results["summary"] = (f"Financial view on {entity}: verdict={results['verdict']}, "
                              f"{completed} of {len(results['checks'])} checks completed.")
        return results


class ResearchAgent:
    """Runs in the ``res_agent`` process. Owns NEWS MCP tool calls."""

    node_id = "res_agent"
    label = "Research Agent"

    @staticmethod
    @governed(action="res_agent.analyze")
    async def analyze(entity: str, session: str) -> dict[str, Any]:
        results: dict[str, Any] = {"agent": ResearchAgent.label,
                                   "entity": entity, "checks": []}
        for tool in ("search_news", "get_sentiment"):
            try:
                r = await call_tool("news_mcp", tool, session,
                                    caller=ResearchAgent.node_id, entity=entity)
                results["checks"].append(r)
            except AGTBlocked as e:
                _emit_blocked("news_mcp", session, action=f"news_mcp.{tool}", reason=str(e))
                results["checks"].append({"tool": tool, "blocked": True,
                                          "reason": str(e)})

        sentiments = [c.get("sentiment") for c in results["checks"] if "sentiment" in c]
        completed = sum("blocked" not in c for c in results["checks"])
        results["sentiment"] = sentiments[0] if sentiments else "blocked"
        results["summary"] = (f"Research view on {entity}: sentiment={results['sentiment']}, "
                              f"{completed} of {len(results['checks'])} checks completed.")
        return results


def _emit_blocked(source: str, session: str, *, action: str, reason: str) -> None:
    BUS.publish(Event(type=EventType.BLOCKED, source=source, session=session,
                      message=f"BLOCKED {action}: {reason}",
                      data={"action": action, "reason": reason, "by": "cp-policy"}))
