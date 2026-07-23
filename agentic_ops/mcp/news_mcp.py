"""NEWS MCP — news + sentiment. Uses Finnhub when configured, else mock."""
from __future__ import annotations

import hashlib
import random
from datetime import datetime, timezone
from typing import Any

from .base import MCPServer
from .. import config
from ..providers import finnhub

_HEADLINE_TEMPLATES = [
    "{e} posts quarterly results ahead of estimates",
    "Analysts upgrade {e} on margin expansion",
    "{e} faces regulatory scrutiny over lending practices",
    "{e} announces expansion into new markets",
    "Sector rotation pressures {e} shares",
    "{e} leadership reshuffle sparks investor debate",
]
_POS = ("beat", "surge", "upgrade", "record", "growth", "profit", "gain", "ahead", "strong", "expansion")
_NEG = ("miss", "fall", "downgrade", "loss", "probe", "scrutiny", "cut", "weak", "decline", "lawsuit")


def _seed(text: str) -> random.Random:
    h = int(hashlib.sha256(text.encode()).hexdigest(), 16)
    return random.Random(h)


def _naive_sentiment(headlines: list[str]) -> float:
    if not headlines:
        return 0.0
    score = 0
    for h in headlines:
        low = h.lower()
        score += sum(w in low for w in _POS)
        score -= sum(w in low for w in _NEG)
    # squash into -1..1
    return round(max(-1.0, min(1.0, score / (len(headlines) * 2))), 2)


class NewsMCP(MCPServer):
    node_id = "news_mcp"
    label = "NEWS MCP"

    def register_tools(self) -> None:
        @self.tool("search_news", "Recent headlines for an entity")
        async def search_news(entity: str, limit: int = 4) -> dict[str, Any]:
            if config.data_mode() == "live":
                try:
                    sym = await finnhub.resolve_symbol(entity)
                    raw = await finnhub.company_news(sym, days=30)
                    now = datetime.now(timezone.utc).timestamp()
                    headlines = []
                    for a in raw[:limit]:
                        hrs = int(max(0, (now - a.get("datetime", now)) / 3600))
                        headlines.append({"title": a.get("headline", ""),
                                          "source": a.get("source", ""),
                                          "hours_ago": hrs, "url": a.get("url", "")})
                    return {"entity": entity, "symbol": sym,
                            "count": len(headlines), "headlines": headlines,
                            "source": "finnhub"}
                except Exception as e:  # noqa: BLE001
                    return {**_mock_news(entity, limit), "source": f"mock (finnhub error: {e})"}
            return {**_mock_news(entity, limit), "source": "mock"}

        @self.tool("get_sentiment", "Aggregate news sentiment score (-1..1)")
        async def get_sentiment(entity: str) -> dict[str, Any]:
            if config.data_mode() == "live":
                try:
                    sym = await finnhub.resolve_symbol(entity)
                    raw = await finnhub.company_news(sym, days=30)
                    titles = [a.get("headline", "") for a in raw]
                    score = _naive_sentiment(titles)
                    label = "bullish" if score > 0.2 else "bearish" if score < -0.2 else "neutral"
                    return {"entity": entity, "symbol": sym,
                            "sentiment_score": score, "label": label,
                            "articles_analyzed": len(titles),
                            "method": "headline-keyword", "source": "finnhub"}
                except Exception as e:  # noqa: BLE001
                    return {**_mock_sentiment(entity), "source": f"mock (finnhub error: {e})"}
            return {**_mock_sentiment(entity), "source": "mock"}


def _mock_news(entity: str, limit: int) -> dict[str, Any]:
    r = _seed("news:" + entity)
    picks = r.sample(_HEADLINE_TEMPLATES, min(limit, len(_HEADLINE_TEMPLATES)))
    headlines = [{"title": t.format(e=entity),
                  "source": r.choice(["Reuters", "Bloomberg", "ET", "Mint"]),
                  "hours_ago": r.randint(1, 48)} for t in picks]
    return {"entity": entity, "count": len(headlines), "headlines": headlines}


def _mock_sentiment(entity: str) -> dict[str, Any]:
    r = _seed("sent:" + entity)
    score = round(r.uniform(-1, 1), 2)
    label = "bullish" if score > 0.2 else "bearish" if score < -0.2 else "neutral"
    return {"entity": entity, "sentiment_score": score, "label": label,
            "articles_analyzed": r.randint(12, 140)}
