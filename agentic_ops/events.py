"""Structured event stream + async pub/sub bus.

Every meaningful thing that happens in the system (a flow step, an LLM call,
an MCP request/response, an agent-to-agent message, a guardrail block) is
emitted as an Event. The dashboard subscribes to the bus over a WebSocket and
renders the live event stream + topology.

This module is pure standard library — it has no dependency on any governance
or mesh layer, so it is identical in the vanilla and the governed builds.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, AsyncIterator, Optional


class EventType(str, Enum):
    FLOW = "flow"            # high-level pipeline milestones
    MODEL = "model"          # (simulated) LLM calls
    MCP_IN = "mcp_in"        # request sent into an MCP server
    MCP_OUT = "mcp_out"      # response returned from an MCP server
    A2A = "a2a"              # agent-to-agent message
    GUARDRAIL = "guardrail"  # a guardrail check
    BLOCKED = "blocked"      # a guardrail blocked something
    STATUS = "status"        # node status change (IDLE/RUNNING/DONE)


@dataclass
class Event:
    type: EventType
    source: str                       # node id: coordinator / fin_agent / ...
    message: str
    session: str
    ts: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    target: Optional[str] = None      # for A2A / MCP edges
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Event":
        """Rebuild an Event from a to_dict() payload (for cross-process replay)."""
        return cls(
            type=EventType(d["type"]),
            source=d.get("source", ""),
            message=d.get("message", ""),
            session=d.get("session", ""),
            ts=d.get("ts", time.time()),
            id=d.get("id", uuid.uuid4().hex[:8]),
            target=d.get("target"),
            data=d.get("data") or {},
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        d["clock"] = time.strftime("%H:%M:%S", time.localtime(self.ts))
        return d


class EventBus:
    """A simple async fan-out bus. Keeps a bounded history for late joiners."""

    def __init__(self, history_size: int = 500) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._history: list[Event] = []
        self._history_size = history_size

    def publish(self, event: Event) -> None:
        self._history.append(event)
        if len(self._history) > self._history_size:
            self._history.pop(0)
        for q in list(self._subscribers):
            q.put_nowait(event)

    def history(self) -> list[Event]:
        return list(self._history)

    async def subscribe(self) -> AsyncIterator[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers.add(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._subscribers.discard(q)


# Global bus shared by the running app.
BUS = EventBus()
