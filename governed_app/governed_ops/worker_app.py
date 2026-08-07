"""FastAPI worker — one instance per specialist role.

Selected by ``AGENT_ROLE`` (``fin_agent`` or ``res_agent``). Exposes:

    POST /analyze   → run this role's specialist against ``{entity, session}``
    GET  /whoami    → this process's registered DID + name
    GET  /health    → liveness

The specialist function is `@governed` — the CP policy decides whether each
of its tool calls is allowed. This worker file is thin because the
governance is already on the specialist.
"""
from __future__ import annotations

import logging
import time

import asyncio

from fastapi import FastAPI
from pydantic import BaseModel

from agt_sdk.exceptions import AGTBlocked

from . import config
from .events import BUS, Event, EventType
from .specialist import FinancialAgent, ResearchAgent

log = logging.getLogger(f"governed_ops.worker.{config.ROLE}")

_ROLE_SPECIALIST = {
    "fin_agent": FinancialAgent,
    "res_agent": ResearchAgent,
}
if config.ROLE not in _ROLE_SPECIALIST:
    raise RuntimeError(
        f"worker_app was started with AGENT_ROLE={config.ROLE!r} — expected "
        f"one of {list(_ROLE_SPECIALIST)}."
    )
_SPECIALIST = _ROLE_SPECIALIST[config.ROLE]

app = FastAPI(title=f"worker/{config.ROLE}")


@app.on_event("startup")
async def _start_reward_pump() -> None:
    """Kick the SDK's reward engine into publishing scores to the CP.

    The SDK's reward engine only recalculates (and therefore only fires its
    ``on_score_change`` callbacks — the ones that push scores to the CP) in
    two cases: on a "bad" signal (deny), or via ``start_background_updates``.
    ``_lifecycle._maybe_wire_mesh`` schedules the drift publisher's sampler
    loop but NOT the reward engine's, so allowed-only workloads never publish
    a score higher than the initial 0. Starting the loop here fixes that.

    This is infrastructure, not agent business logic — same category as the
    ``/whoami`` peek at ``AutoKernel``.
    """
    try:
        from agt_sdk import AutoKernel  # noqa: PLC0415 — startup-only
        engines = AutoKernel.instance().mesh_engines
        eng = getattr(engines, "reward_engine", None) if engines else None
        if eng is None:
            return
        asyncio.create_task(eng.start_background_updates())
        log.info("worker: reward-engine background updates started")
    except Exception as exc:  # noqa: BLE001 — fail-open; governance still works
        log.debug("worker: reward-engine pump not started (%s)", exc)


class AnalyzeReq(BaseModel):
    entity: str
    session: str


@app.get("/health")
def health() -> dict:
    return {"ok": True, "role": config.ROLE}


@app.get("/debug/reward")
def debug_reward() -> dict:
    """Diagnostics: current reward-engine state as this process sees it.

    Non-production endpoint. Kept for the "why is my trust score still 0"
    diagnosis. Safe to delete once the flow is understood.
    """
    try:
        from agt_sdk import AutoKernel  # noqa: PLC0415
        engines = AutoKernel.instance().mesh_engines
        if engines is None:
            return {"mesh_wired": False}
        eng = engines.reward_engine
        did = str(engines.identity.did) if engines.identity else None
        if eng is None:
            return {"mesh_wired": True, "reward_engine": None, "did": did}
        state = eng._agents.get(did)
        signals_by_dim: dict = {}
        current_score = None
        if state is not None:
            for s in state.recent_signals:
                signals_by_dim[s.dimension.value] = signals_by_dim.get(s.dimension.value, 0) + 1
            current_score = getattr(state.trust_score, "total_score", None)
        return {"mesh_wired": True, "did": did,
                "known_agents": list(eng._agents.keys()),
                "signals_by_dimension": signals_by_dim,
                "current_local_score": current_score,
                "running_loop": bool(getattr(eng, "_running", False))}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


@app.get("/whoami")
def whoami() -> dict:
    """This worker's live identity — read straight from the SDK singleton.

    Reading ``AutoKernel.instance().mesh_engines.identity`` is the ONE place
    we peek at the SDK — a diagnostics endpoint, not agent business logic.
    """
    did = None
    name = config.AGENT_NAME
    try:
        from agt_sdk import AutoKernel      # noqa: PLC0415 — diagnostics-only
        engines = AutoKernel.instance().mesh_engines
        ident = getattr(engines, "identity", None) if engines else None
        if ident is not None:
            did = str(getattr(ident, "did", "") or "") or None
            name = getattr(ident, "name", name) or name
    except Exception as exc:  # noqa: BLE001 — governance may be off
        log.debug("whoami: SDK not wired (%s)", exc)
    return {"role": config.ROLE, "did": did, "name": name}


@app.post("/analyze")
async def analyze(req: AnalyzeReq) -> dict:
    """Run this worker's specialist and return result + events.

    The specialist's ``analyze`` is itself ``@governed``, so a rule that
    matches the ``{node_id}.analyze`` action (or its params) fires here
    BEFORE any tool call runs. We catch ``AGTBlocked`` and translate it into
    a normal 200 response with a ``blocked: True`` result so the coordinator
    sees a graceful "this specialist was blocked" — not a transport error.
    """
    since = time.time()
    action = f"{_SPECIALIST.node_id}.analyze"
    try:
        result = await _SPECIALIST.analyze(req.entity, req.session)
    except AGTBlocked as e:
        BUS.publish(Event(type=EventType.BLOCKED, source=_SPECIALIST.node_id,
                          session=req.session,
                          message=f"BLOCKED {action}: {e}",
                          data={"action": action, "reason": str(e),
                                "by": "cp-policy"}))
        # Match the "blocked" shape the coordinator's fuse logic understands
        # (see coordinator._fuse). ``verdict``/``sentiment`` = "blocked" is the
        # signal for partial view.
        result = {"agent": _SPECIALIST.label, "blocked": True, "reason": str(e),
                  "verdict": "blocked", "sentiment": "blocked",
                  "summary": f"{_SPECIALIST.label} blocked by policy: {e}"}
    events = [e.to_dict() for e in BUS.history(since=since)
              if e.session == req.session]
    return {"result": result, "events": events}
