"""Research Agent — uses NEWS MCP tools to build a qualitative view.

VANILLA build: no ``@governed`` decorator (see financial_agent.py note).
"""
from __future__ import annotations

from typing import Any

from .base import Agent
from ..mcp.news_mcp import NewsMCP
from .. import governance

class ResearchAgent(Agent):
    node_id = "res_agent"
    label = "Research Agent"

    def __init__(self, session: str, news_mcp: NewsMCP) -> None:
        super().__init__(session)
        self.news_mcp = news_mcp

    @governance.governed(action="res_agent.analyze")
    async def analyze(self, entity: str) -> dict[str, Any]:
        self.flow(f"Research scan of {entity} started")
        await self.think(f"Plan research scan for {entity}", tag="plan")

        news = await self.news_mcp.call("search_news", self.session,
                                        self.node_id, entity=entity, limit=4)
        sentiment = await self.news_mcp.call("get_sentiment", self.session,
                                             self.node_id, entity=entity)

        await self.think("Assess narrative & sentiment", tag="synthesize")

        # A guardrailed tool may have been blocked -> result carries no data.
        if news.get("blocked") or sentiment.get("blocked"):
            reason = news.get("reason") or sentiment.get("reason") or "policy"
            summary = f"{entity}: research blocked by guardrail ({reason})."
            self._status("IDLE")
            return {"agent": self.label, "sentiment": "unknown",
                    "summary": summary, "news": news, "sentiment_detail": sentiment}

        headlines = news.get("headlines") or []
        top = headlines[0]["title"] if headlines else "n/a"
        label = sentiment.get("label", "unknown")
        score = sentiment.get("sentiment_score", 0.0)
        n = sentiment.get("articles_analyzed", 0)
        summary = (
            f"{entity}: sentiment {label} ({score:+}) across {n} articles. "
            f"Lead story: \"{top}\". [{news.get('source', 'mock')}]"
        )
        self._status("IDLE")
        return {"agent": self.label, "sentiment": label,
                "summary": summary, "news": news, "sentiment_detail": sentiment}
