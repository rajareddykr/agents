"""Financial Agent — uses FIN MCP tools to build a quantitative view.

VANILLA build: no ``@governed`` decorator. ``analyze()`` runs directly. In the
governed build (migration guide, step 3) this method is wrapped with
``@governance.governed(action="fin_agent.analyze")`` so the control plane
authorizes the whole analysis under this agent's own mesh identity.
"""
from __future__ import annotations

from typing import Any

from .base import Agent
from ..mcp.fin_mcp import FinMCP
from .. import governance

class FinancialAgent(Agent):
    node_id = "fin_agent"
    label = "Financial Agent"

    def __init__(self, session: str, fin_mcp: FinMCP) -> None:
        super().__init__(session)
        self.fin_mcp = fin_mcp

    @governance.governed(action="fin_agent.analyze")
    async def analyze(self, entity: str) -> dict[str, Any]:
        self.flow(f"Financial analysis of {entity} started")
        await self.think(f"Plan financial workup for {entity}", tag="plan")

        quote = await self.fin_mcp.call("get_quote", self.session,
                                        self.node_id, entity=entity)
        fundamentals = await self.fin_mcp.call("get_fundamentals", self.session,
                                               self.node_id, entity=entity)
        trend = await self.fin_mcp.call("get_price_trend", self.session,
                                        self.node_id, entity=entity, days=30)

        await self.think("Synthesize quantitative signals", tag="synthesize")

        # A guardrailed tool may have been blocked -> result carries no data.
        if any(r.get("blocked") for r in (quote, fundamentals, trend)):
            blk = next(r for r in (quote, fundamentals, trend) if r.get("blocked"))
            summary = (f"{entity}: financial analysis blocked by guardrail "
                       f"({blk.get('reason', 'policy')}).")
            self._status("IDLE")
            return {"agent": self.label, "verdict": "blocked", "summary": summary,
                    "quote": quote, "fundamentals": fundamentals, "trend": trend}

        roe = fundamentals.get("roe_pct")
        verdict = ("constructive"
                   if trend.get("trend") == "up" and (roe or 0) > 12
                   else "cautious")

        def _n(v, suffix="", pct=False):
            if v is None:
                return "n/a"
            if pct:
                return f"{v:+}{suffix}" if isinstance(v, (int, float)) else f"{v}{suffix}"
            return f"{v}{suffix}"

        summary = (
            f"{entity}: {_n(quote.get('price'))} {quote.get('currency', '')} "
            f"({_n(quote.get('day_change_pct'), '%', pct=True)}), "
            f"P/E {_n(fundamentals.get('pe_ratio'))}, "
            f"ROE {_n(roe, '%')}, 30d trend {trend.get('trend', 'n/a')}. "
            f"Stance: {verdict}. [{quote.get('source', 'mock')}]"
        )
        self._status("IDLE")
        return {"agent": self.label, "verdict": verdict, "summary": summary,
                "quote": quote, "fundamentals": fundamentals, "trend": trend}
