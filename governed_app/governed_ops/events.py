"""Minimal in-memory event bus. Used only for the /events endpoint."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EventType(str, Enum):
    FLOW = "flow"
    A2A = "a2a"
    MCP_IN = "mcp_in"
    MCP_OUT = "mcp_out"
    BLOCKED = "blocked"


@dataclass
class Event:
    type: EventType
    source: str
    session: str
    message: str = ""
    target: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=lambda: time.time())

    def to_dict(self) -> dict:
        return {
            "type": self.type.value, "source": self.source, "target": self.target,
            "session": self.session, "message": self.message,
            "data": self.data, "ts": self.ts,
        }


class Bus:
    def __init__(self, cap: int = 500) -> None:
        self._history: list[Event] = []
        self._cap = cap

    def publish(self, ev: Event) -> None:
        self._history.append(ev)
        if len(self._history) > self._cap:
            self._history = self._history[-self._cap :]

    def history(self, since: float = 0.0) -> list[Event]:
        return [e for e in self._history if e.ts >= since]


BUS = Bus()
