"""Wires MCP servers + agents together and runs a mission end-to-end.

Two modes:
  * in-process  — agents run as objects in this process (simple/dev).
  * distributed — each agent runs in its OWN process. The Coordinator
    dispatches to those worker processes over HTTP via ``RemoteAgent``, then
    replays the workers' events onto the local event bus so the dashboard
    shows the full run. Selected by config.DISTRIBUTED (both FIN_AGENT_URL and
    RES_AGENT_URL set).

VANILLA build: ``RemoteAgent`` is a plain HTTP proxy. It does NOT fetch a mesh
DID from the worker and there is no trust handshake — the Coordinator simply
POSTs the task and replays the returned events. (In the governed build the
proxy also fetches ``/whoami`` for the peer's real ``did:mesh`` and the
Coordinator handshakes against it — see the migration guide.)
"""
from __future__ import annotations

import asyncio
import json
import urllib.request
import uuid
from typing import Any

from . import config
from .agents.coordinator import Coordinator
from .agents.financial_agent import FinancialAgent
from .agents.research_agent import ResearchAgent
from .events import BUS, Event, EventType
from .mcp.fin_mcp import FinMCP
from .mcp.news_mcp import NewsMCP


NODES = [
    {"id": "coordinator", "label": "Coordinator", "kind": "agent"},
    {"id": "fin_agent", "label": "Financial Agent", "kind": "agent"},
    {"id": "res_agent", "label": "Research Agent", "kind": "agent"},
    {"id": "fin_mcp", "label": "FIN MCP", "kind": "mcp"},
    {"id": "news_mcp", "label": "NEWS MCP", "kind": "mcp"},
]


def extract_entity(mission: str) -> str:
    """Very small heuristic to pull the subject out of a mission string."""
    text = mission.strip()
    for kw in (" for ", " of ", " on ", " about "):
        if kw in text.lower():
            idx = text.lower().rindex(kw) + len(kw)
            return text[idx:].strip().rstrip(".") or text
    return text


def _post_json(url: str, payload: dict, timeout: int = 120) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class RemoteAgent:
    """Proxy for an agent running in a separate process (HTTP dispatch).

    Plain vanilla: just the worker URL. No DID discovery, no capabilities
    handshake. ``analyze()`` POSTs the task and replays the worker's events
    onto the local bus so the dashboard shows the full run.
    """

    def __init__(self, node_id: str, label: str, url: str, session: str) -> None:
        self.node_id = node_id
        self.label = label
        self.url = url
        self.session = session

    def send(self, target: str, message: str) -> None:
        BUS.publish(Event(type=EventType.A2A, source=self.node_id,
                          target=target, session=self.session, message=message))

    async def analyze(self, entity: str) -> dict[str, Any]:
        try:
            data = await asyncio.to_thread(
                _post_json, f"{self.url}/analyze",
                {"entity": entity, "session": self.session})
        except Exception as e:  # noqa: BLE001 - worker down / transport error
            BUS.publish(Event(type=EventType.BLOCKED, source=self.node_id,
                              session=self.session,
                              message=f"{self.label} worker unreachable: {e}",
                              data={"by": "transport"}))
            return {"agent": self.label, "verdict": "error", "sentiment": "error",
                    "summary": f"{self.label} worker unreachable ({e})."}
        # Replay the worker's events onto our bus for the live dashboard.
        for ev in data.get("events", []):
            try:
                BUS.publish(Event.from_dict(ev))
            except Exception:  # noqa: BLE001
                pass
        return data.get("result", {})


class Orchestrator:
    def __init__(self) -> None:
        # Kept for topology/tool listing even in distributed mode.
        self.fin_mcp = FinMCP()
        self.news_mcp = NewsMCP()

    def _build_agents(self, session: str):
        if config.DISTRIBUTED:
            fin = RemoteAgent("fin_agent", "Financial Agent",
                              config.FIN_AGENT_URL, session)
            res = RemoteAgent("res_agent", "Research Agent",
                              config.RES_AGENT_URL, session)
        else:
            fin = FinancialAgent(session, self.fin_mcp)
            res = ResearchAgent(session, self.news_mcp)
        return fin, res

    async def run_mission(self, mission: str) -> dict[str, Any]:
        session = uuid.uuid4().hex
        entity = extract_entity(mission)
        fin_agent, res_agent = self._build_agents(session)
        coordinator = Coordinator(session, fin_agent, res_agent)
        result = await coordinator.run(mission, entity)
        result["session"] = session
        result["mode"] = config.run_mode()
        return result
