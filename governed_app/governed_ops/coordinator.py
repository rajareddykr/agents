"""The Coordinator process.

Runs in its OWN process (see ``server.py``). Owns nothing but the mission
plan and the two ``@peer_verified`` gates. Every delegation goes over HTTP
to a specialist worker; the trust check happens BEFORE the HTTP call.

Two decorators are used in this file:

  * ``@peer_verified("Financial Agent")`` — CP tells us whether we may talk to
    the peer. If not, ``AGTPeerUntrusted`` is raised and we skip the HTTP call
    entirely; the mission still terminates with a partial view.

No ``AutoKernel``. No manual mesh handshake. No policy lookups. The SDK's
IMPL-065 A2A gate does all of that behind ``@peer_verified``.
"""
from __future__ import annotations

import os
import asyncio
import json
import urllib.request
import uuid
from typing import Any

from agt_sdk import peer_verified
from agt_sdk.exceptions import AGTPeerUntrusted

from . import config
from .events import BUS, Event, EventType
from .guardrails import check as local_check


class Coordinator:
    node_id = "coordinator"
    label = "Coordinator"

    async def run_mission(self, mission: str) -> dict[str, Any]:
        session = uuid.uuid4().hex
        entity = _extract_entity(mission)
        BUS.publish(Event(type=EventType.FLOW, source=self.node_id,
                          session=session, message=f"mission received: {mission}"))

        # Local offline safety net — fast, deterministic.
        local = local_check(mission)
        if not local.allowed:
            BUS.publish(Event(type=EventType.BLOCKED, source=self.node_id,
                              session=session,
                              message=f"BLOCKED mission: {local.reason}",
                              data={"rule": local.rule, "by": "local-guardrail"}))
            return {"status": "blocked", "reason": local.reason,
                    "rule": local.rule, "by": "local-guardrail"}

        # Delegate to both specialists in parallel. Each delegation is gated
        # by @peer_verified BEFORE the HTTP call runs; ``AGTPeerUntrusted`` is
        # raised in place of the return value when the peer fails the gate.
        # ``return_exceptions=True`` keeps the second peer's result even if
        # the first is refused — one peer being untrusted must not sink the
        # whole mission.
        fin_task = asyncio.create_task(self._delegate_to_finance(entity, session))
        res_task = asyncio.create_task(self._delegate_to_research(entity, session))
        raw_fin, raw_res = await asyncio.gather(fin_task, res_task,
                                                return_exceptions=True)
        fin_res = self._unwrap_peer_result(raw_fin, "Financial Agent",
                                           node_id="fin_agent", session=session)
        res_res = self._unwrap_peer_result(raw_res, "Research Agent",
                                           node_id="res_agent", session=session)

        final = self._fuse(entity, fin_res, res_res)
        BUS.publish(Event(type=EventType.FLOW, source=self.node_id,
                          session=session, message="mission complete"))
        return {"status": "done", "mission": mission, "entity": entity,
                "financial": fin_res, "research": res_res,
                "final": final, "session": session}

    # ── A2A gates ────────────────────────────────────────────────────────────
    #
    # Trust floor: no explicit ``min_trust=…`` argument here — the SDK falls
    # back to ``AGT_A2A_MIN_TRUST`` (see ``.env``) when the decorator omits it.
    # Chain:  @peer_verified(peer)  ->  kernel.a2a_gate(peer, min_trust=None)
    #                                ->  run_gate:  bar = cfg.a2a_min_trust
    #                                ->  cfg.a2a_min_trust = int(env AGT_A2A_MIN_TRUST or 0)
    # To pin a stricter bar just for one delegation, pass ``min_trust=<N>``
    # here — an explicit value beats the env default.

    @peer_verified("Financial Agent")
    async def _delegate_to_finance(self, entity: str, session: str) -> dict[str, Any]:
        return await self._dispatch("fin_agent", "Financial Agent",
                                    config.FIN_AGENT_URL, entity, session)

    @peer_verified("Research Agent")
    async def _delegate_to_research(self, entity: str, session: str) -> dict[str, Any]:
        return await self._dispatch("res_agent", "Research Agent",
                                    config.RES_AGENT_URL, entity, session)

    # ── HTTP dispatch (identical for both peers) ─────────────────────────────

    async def _dispatch(self, node_id: str, label: str, base_url: str,
                        entity: str, session: str) -> dict[str, Any]:
        """Call the peer worker's ``/analyze``. Assumes @peer_verified passed."""
        BUS.publish(Event(type=EventType.A2A, source=self.node_id,
                          target=node_id, session=session,
                          message=f"delegate: analyze {entity}"))
        # ``_dispatch`` only runs when @peer_verified allowed the delegation;
        # AGTPeerUntrusted is raised BEFORE the body, so it never reaches here.
        try:
            data = await asyncio.to_thread(_post_json,
                f"{base_url}/analyze", {"entity": entity, "session": session})
        except Exception as exc:  # noqa: BLE001 — worker down / transport error
            BUS.publish(Event(type=EventType.BLOCKED, source=self.node_id,
                              session=session,
                              message=f"{label} worker unreachable: {exc}",
                              data={"by": "transport"}))
            return {"agent": label, "verdict": "error", "sentiment": "error",
                    "summary": f"{label} worker unreachable ({exc})."}

        # Replay the worker's events onto our bus so the /events feed shows
        # the full picture (MCP_IN / MCP_OUT / BLOCKED from the worker).
        for ev in data.get("events", []):
            try:
                BUS.publish(Event(type=EventType(ev["type"]), source=ev["source"],
                                  target=ev.get("target", ""), session=ev["session"],
                                  message=ev.get("message", ""),
                                  data=ev.get("data", {}), ts=ev.get("ts", 0)))
            except Exception:  # noqa: BLE001
                pass
        BUS.publish(Event(type=EventType.A2A, source=node_id, target=self.node_id,
                          session=session, message="findings ready"))
        return data.get("result", {"agent": label, "summary": "no result"})

    # ── AGTPeerUntrusted / exception translation ─────────────────────────────

    def _unwrap_peer_result(self, raw: Any, label: str, *, node_id: str,
                            session: str) -> dict[str, Any]:
        """Turn ``asyncio.gather`` output back into a blocked-shaped dict.

        Three cases:
          * ``dict``                → happy path, return as-is
          * ``AGTPeerUntrusted``    → @peer_verified refused this delegation
          * any other ``Exception`` → the HTTP dispatch itself raised
        """
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, AGTPeerUntrusted):
            BUS.publish(Event(type=EventType.BLOCKED, source=self.node_id,
                              target=node_id, session=session,
                              message=f"peer refused: {label}: {raw}",
                              data={"by": "peer-verified", "reason": str(raw),
                                    "peer_name": getattr(raw, "peer_name", label)}))
            return {"agent": label, "blocked": True, "reason": str(raw),
                    "verdict": "blocked", "sentiment": "blocked",
                    "summary": f"{label} refused by @peer_verified: {raw}"}
        # Some other exception — treat as transport / worker error.
        BUS.publish(Event(type=EventType.BLOCKED, source=self.node_id,
                          target=node_id, session=session,
                          message=f"{label} delegation error: {raw}",
                          data={"by": "transport", "reason": str(raw)}))
        return {"agent": label, "verdict": "error", "sentiment": "error",
                "summary": f"{label} delegation error: {raw}"}

    # ── Fusion (pure logic, no governance) ──────────────────────────────────

    @staticmethod
    def _fuse(entity: str, fin: dict[str, Any], res: dict[str, Any]) -> str:
        v = fin.get("verdict")
        s = res.get("sentiment")
        if v == "constructive" and s == "bullish":
            call = "Aligned positive - fundamentals and narrative agree."
        elif v == "cautious" and s == "bearish":
            call = "Aligned negative - both quant and narrative flag risk."
        elif v == "blocked" or s == "blocked" or v == "error" or s == "error":
            call = "Partial view - one or both specialists were blocked/unreachable."
        else:
            call = "Mixed - quant and narrative diverge; monitor closely."
        return (f"FINAL INTELLIGENCE - {entity}\n\n"
                f"Financial: {fin.get('summary')}\n\n"
                f"Research: {res.get('summary')}\n\n"
                f"Coordinator call: {call}")


# ── Helpers ─────────────────────────────────────────────────────────────────

def _extract_entity(mission: str) -> str:
    text = mission.strip()
    for kw in (" for ", " of ", " on ", " about "):
        if kw in text.lower():
            idx = text.lower().rindex(kw) + len(kw)
            return text[idx:].strip().rstrip(".") or text
    return text


def _post_json(url: str, payload: dict,
               timeout: int = int(os.environ.get("AGT_SPECIALIST_HTTP_TIMEOUT", "300"))) -> dict:
    # Default 300s (was 120): a specialist may now BLOCK waiting for a human
    # approval (R4.1 block-and-resume). Must exceed the worker's total wait
    # (AGT_APPROVAL_WAIT_SECONDS per held action) or the coordinator gives up
    # mid-approval and reports the worker unreachable.
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))
