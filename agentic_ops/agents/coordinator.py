"""Coordinator — plans the mission, delegates to specialists, fuses results.

VANILLA build — plain delegation, no mesh handshake, no control-plane check.
--------------------------------------------------------------------------
The flow is:
  1. local guardrail on the mission text
  2. simulated planning ("think")
  3. A2A dispatch to both specialists IN PARALLEL (send + analyze)
  4. specialists report back (A2A)
  5. local guardrail on each finding
  6. fuse into a final answer

What was removed vs the governed build:
  * ``governance.authorize("coordinator.run_mission", ...)`` before step 2
  * a mesh ``handshake()`` with each peer between step 2 and step 3 that
    gated delegation on a verified Ed25519 identity
Both come back in docs/AGT_MIGRATION_GUIDE.md (steps 3 and 4).
"""
from __future__ import annotations

import asyncio
from typing import Any

from .base import Agent
from .financial_agent import FinancialAgent
from .research_agent import ResearchAgent
from ..guardrails import check
from ..events import BUS, Event, EventType


class Coordinator(Agent):
    node_id = "coordinator"
    label = "Coordinator"

    def __init__(self, session: str, fin_agent: FinancialAgent,
                 res_agent: ResearchAgent) -> None:
        super().__init__(session)
        self.fin_agent = fin_agent
        self.res_agent = res_agent

    def _guard(self, text: str, source: str) -> bool:
        result = check(text)
        BUS.publish(Event(type=EventType.GUARDRAIL, source=self.node_id,
                          session=self.session,
                          message=f"guardrail check on {source}"))
        if not result.allowed:
            BUS.publish(Event(type=EventType.BLOCKED, source=self.node_id,
                              session=self.session,
                              message=f"BLOCKED {source}: {result.reason}",
                              data={"rule": result.rule}))
            return False
        return True

    async def run(self, mission: str, entity: str) -> dict[str, Any]:
        self.flow("Analysis started")
        self._status("RUNNING")

        # Local regex guardrail (fast, offline) — the only pre-check in vanilla.
        if not self._guard(mission, "mission"):
            self._status("IDLE")
            return {"status": "blocked", "reason": "mission failed guardrail"}

        await self.think(f"Decompose mission: {mission}", tag="plan")

        fin_ok, res_ok = await asyncio.gather(
            self.handshake(self.fin_agent),
            self.handshake(self.res_agent),
        )
        if not (fin_ok and res_ok):
            self._status("IDLE")
            return {"status": "blocked", "reason": "mesh handshake failed"}

        # A2A dispatch to specialists in parallel (handshake verified above).
        self.send("fin_agent", f"analyze financials for {entity}")
        self.send("res_agent", f"research news & sentiment for {entity}")

        fin_res, res_res = await asyncio.gather(
            self.fin_agent.analyze(entity),
            self.res_agent.analyze(entity),
        )


        # Specialists report back.
        self.fin_agent.send("coordinator", "financial findings ready")
        self.res_agent.send("coordinator", "research findings ready")

        # Guardrail the incoming findings before fusing.
        for res, name in ((fin_res, "financial"), (res_res, "research")):
            self._guard(res["summary"], name)

        await self.think("Fuse specialist findings into final intelligence",
                         tag="fuse")

        final = self._fuse(entity, fin_res, res_res)
        BUS.publish(Event(type=EventType.FLOW, source=self.node_id,
                          session=self.session,
                          message="Final intelligence ready",
                          data={"final": final}))
        self._status("DONE")
        return {
            "status": "done",
            "mission": mission,
            "entity": entity,
            "final": final,
            "financial": fin_res,
            "research": res_res,
        }

    @staticmethod
    def _fuse(entity: str, fin: dict[str, Any], res: dict[str, Any]) -> str:
        stance = fin.get("verdict")
        senti = res.get("sentiment")
        if stance == "constructive" and senti == "bullish":
            call = "Aligned positive — fundamentals and narrative agree."
        elif stance == "cautious" and senti == "bearish":
            call = "Aligned negative — both quant and narrative flag risk."
        else:
            call = "Mixed — quant and narrative diverge; monitor closely."
        return (
            f"FINAL INTELLIGENCE — {entity}\n\n"
            f"Financial: {fin['summary']}\n\n"
            f"Research: {res['summary']}\n\n"
            f"Coordinator call: {call}"
        )
