"""Autonomous, self-contained smoke test for the VANILLA build.

Runs the full multi-agent pipeline IN-PROCESS on mock data (no web server, no
network, no external key, no pytest) and asserts the whole flow worked:

  * the Coordinator delegated to BOTH specialists
  * agent-to-agent (A2A) messages were exchanged in both directions
  * BOTH MCP servers were called (FIN + NEWS) and returned results
  * simulated LLM 'think' calls fired
  * a final fused intelligence answer was produced
  * a mission that trips the local guardrail is BLOCKED

Exit code 0 = pass, 1 = fail. Run:  python smoke_test.py
"""
from __future__ import annotations

import asyncio
import sys

# Force offline/mock regardless of any stray env var.
import os
os.environ["LIVE"] = "0"
os.environ.pop("FIN_AGENT_URL", None)
os.environ.pop("RES_AGENT_URL", None)

from agentic_ops.events import BUS, EventType
from agentic_ops.orchestrator import Orchestrator


class Check:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def ok(self, cond: bool, label: str) -> None:
        mark = "PASS" if cond else "FAIL"
        print(f"  [{mark}] {label}")
        if not cond:
            self.failures.append(label)


def _types(events, t):
    return [e for e in events if e.type == t]


async def _run_mission(mission: str):
    # Snapshot the bus history boundary, run, then read what this run emitted.
    start = len(BUS.history())
    orch = Orchestrator()
    result = await orch.run_mission(mission)
    events = BUS.history()[start:]
    return result, events


async def main() -> int:
    c = Check()

    print("\n== Scenario 1: healthy mission (multi-agent, MCP, fusion) ==")
    result, events = await _run_mission("Analyze the market trends for HDFC bank")

    c.ok(result.get("status") == "done", "mission completed (status=done)")
    c.ok(result.get("entity") == "HDFC bank", "entity extracted from mission")
    c.ok(bool(result.get("final")), "final fused intelligence produced")
    c.ok("FINAL INTELLIGENCE" in (result.get("final") or ""),
         "final answer has the fused format")

    fin = result.get("financial") or {}
    res = result.get("research") or {}
    c.ok(fin.get("verdict") in ("constructive", "cautious"),
         "financial agent produced a verdict")
    c.ok(res.get("sentiment") in ("bullish", "bearish", "neutral"),
         "research agent produced a sentiment")

    # A2A: coordinator -> both specialists, and specialists -> coordinator.
    a2a = _types(events, EventType.A2A)
    srcs = {e.source for e in a2a}
    tgts = {e.target for e in a2a}
    c.ok(len(a2a) >= 4, f"A2A messages exchanged (got {len(a2a)})")
    c.ok("coordinator" in srcs, "coordinator dispatched to specialists (A2A)")
    c.ok({"fin_agent", "res_agent"} & srcs == {"fin_agent", "res_agent"}
         or {"fin_agent", "res_agent"} <= srcs,
         "specialists reported back to coordinator (A2A)")
    c.ok("coordinator" in tgts, "specialists addressed the coordinator")

    # MCP: both servers called and both returned.
    mcp_in = _types(events, EventType.MCP_IN)
    mcp_out = _types(events, EventType.MCP_OUT)
    in_targets = {e.target for e in mcp_in}
    c.ok("fin_mcp" in in_targets, "FIN MCP was called")
    c.ok("news_mcp" in in_targets, "NEWS MCP was called")
    c.ok(len(mcp_out) >= 5, f"MCP servers returned results (got {len(mcp_out)})")

    # Simulated LLM calls happened.
    c.ok(len(_types(events, EventType.MODEL)) >= 3,
         "simulated LLM 'think' calls fired")

    # No unexpected block in the healthy path.
    c.ok(len(_types(events, EventType.BLOCKED)) == 0,
         "healthy mission was not blocked")

    print("\n== Scenario 2: guardrail blocks a non-compliant mission ==")
    result2, events2 = await _run_mission(
        "Give me an insider tip with a guaranteed return on ACME")
    c.ok(result2.get("status") == "blocked", "non-compliant mission blocked")
    c.ok(len(_types(events2, EventType.BLOCKED)) >= 1,
         "a BLOCKED event was emitted")

    print("\n== Scenario 3: MCP-level guardrail blocks a bad tool argument ==")
    # Entity itself carries a banned phrase -> the MCP _precheck blocks the tool.
    result3, events3 = await _run_mission(
        "Analyze fundamentals for pump and dump scheme")
    blocked3 = _types(events3, EventType.BLOCKED)
    c.ok(len(blocked3) >= 1, "MCP tool call blocked by local guardrail")

    print("\n" + "=" * 60)
    if c.failures:
        print(f"RESULT: FAIL — {len(c.failures)} check(s) failed:")
        for f in c.failures:
            print(f"   - {f}")
        return 1
    print("RESULT: PASS — all checks green. Vanilla multi-agent pipeline works.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
