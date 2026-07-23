"""Base agent with a simulated LLM 'think' step and plain A2A messaging.

Real deployments would call an LLM in ``think``. We simulate it: latency +
a MODEL event so the dashboard's LLM CALLS counter and event stream light
up, then a deterministic templated 'reasoning' string.

VANILLA build — no mesh identity, no DID, no trust handshake.
--------------------------------------------------------------
Agent-to-agent communication here is just ``send()``: an A2A event on the bus
plus a direct in-process method call (or an HTTP call in distributed mode).
There is NO Ed25519 identity and NO challenge/response handshake before
delegation. Those are re-introduced in docs/AGT_MIGRATION_GUIDE.md, step 4,
by adding a ``mesh.py`` module and a ``handshake()`` method that gates
delegation on a verified peer.
"""
from __future__ import annotations

import asyncio
import random

from ..events import BUS, Event, EventType


class Agent:
    node_id: str = "agent"
    label: str = "Agent"

    def __init__(self, session: str) -> None:
        self.session = session

    def _status(self, status: str) -> None:
        BUS.publish(Event(type=EventType.STATUS, source=self.node_id,
                          session=self.session, message=status,
                          data={"status": status}))

    async def think(self, prompt: str, tag: str = "reason") -> str:
        """Simulate a single LLM call."""
        self._status("RUNNING")
        BUS.publish(Event(
            type=EventType.MODEL, source=self.node_id, session=self.session,
            message=f"llm[{tag}]: {prompt[:70]}{'...' if len(prompt) > 70 else ''}",
            data={"tag": tag, "prompt": prompt},
        ))
        await asyncio.sleep(random.uniform(0.5, 1.3))
        return f"[{self.label} reasoning::{tag}] processed: {prompt[:60]}"

    def send(self, target: str, message: str) -> None:
        """Emit an agent-to-agent (A2A) message edge on the event bus."""
        BUS.publish(Event(
            type=EventType.A2A, source=self.node_id, target=target,
            session=self.session, message=message,
            data={},
        ))

    def flow(self, message: str) -> None:
        BUS.publish(Event(type=EventType.FLOW, source=self.node_id,
                          session=self.session, message=message))
