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
from . import mesh
from ..events import BUS, Event, EventType


class Agent:
    node_id: str = "agent"
    label: str = "Agent"

    def __init__(self, session: str) -> None:
        self.session = session
        self.identity = mesh.ensure_identity(self.node_id)  # NEW
        self.did = str(self.identity.did)  # NEW

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

    async def handshake(self, peer: "Agent") -> bool:
        """Run a mesh trust handshake with `peer` before delegating (Step 4c).

        Emits three A2A events (challenge -> signed response -> verified/failed)
        so the dashboard shows the sequence. Returns True on success. Uses only
        symbols already imported in this module (mesh, BUS, Event, EventType).
        """
        peer_id = peer.node_id
        # A peer with a .url is a RemoteAgent (runs in another process): we hold
        # no private key for it, so we ATTEST against its real /whoami DID rather
        # than run the crypto. In-process peers get the full challenge/response.
        is_remote = getattr(peer, "url", None) is not None
        peer_did = getattr(peer, "did", None) or str(mesh.ensure_identity(peer_id).did)

        # 1) challenge
        self.send(peer_id, f"handshake init: {self.node_id} -> {peer_id} (nonce challenge)")

        if is_remote:
            result = await mesh.attest_handshake(
                self.did, peer_did,
                peer_name=getattr(peer, "label", peer_id),
                capabilities=getattr(peer, "capabilities", None))
        else:
            result = await mesh.handshake(self.did, peer_did)

        # 2) signed response
        BUS.publish(Event(
            type=EventType.A2A, source=peer_id, target=self.node_id, session=self.session,
            message=f"handshake response: {peer_id} signed nonce (cap={result.capabilities})",
            data={"phase": "response", "peer_did": peer_did}))

        # 3) verified / failed
        if result.verified:
            BUS.publish(Event(
                type=EventType.A2A, source=self.node_id, target=peer_id, session=self.session,
                message=(f"handshake verified: {peer_id} trust={result.trust_score} "
                         f"level={result.trust_level}"),
                data={"phase": "verified", "trust_score": result.trust_score}))
            return True

        BUS.publish(Event(
            type=EventType.BLOCKED, source=self.node_id, target=peer_id, session=self.session,
            message=f"handshake FAILED with {peer_id}: {result.rejection_reason}",
            data={"phase": "failed", "by": "mesh-handshake"}))
        return False

    def flow(self, message: str) -> None:
        BUS.publish(Event(type=EventType.FLOW, source=self.node_id,
                          session=self.session, message=message))
