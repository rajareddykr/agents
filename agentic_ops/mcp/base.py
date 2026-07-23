"""Minimal MCP-style server abstraction.

A real MCP server exposes a set of *tools* an agent can call. Here we model the
same shape (name, description, callable) but back it with deterministic mock
data so the whole system runs offline. Each tool call emits MCP_IN / MCP_OUT
events so the dashboard can draw the request/response edges.

VANILLA build: before a tool runs, we apply the LOCAL regex guardrail
(agentic_ops.guardrails) to the call arguments. That is the only pre-execution
check. This method is the exact seam where an AGT governance check is inserted
in the governed build (see docs/AGT_MIGRATION_GUIDE.md, step 3): the
``_precheck`` call below is replaced/augmented with ``governance.authorize(...)``.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from ..events import BUS, Event, EventType
from .. import guardrails


@dataclass
class Tool:
    name: str
    description: str
    handler: Callable[..., Awaitable[dict[str, Any]]]


class MCPServer:
    node_id: str = "mcp"
    label: str = "MCP"

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self.register_tools()

    # Subclasses register their tools here.
    def register_tools(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def tool(self, name: str, description: str):
        def deco(fn: Callable[..., Awaitable[dict[str, Any]]]):
            self._tools[name] = Tool(name, description, fn)
            return fn
        return deco

    def list_tools(self) -> list[dict[str, str]]:
        return [{"name": t.name, "description": t.description}
                for t in self._tools.values()]

    # --- pre-execution check (the AGT seam) ------------------------------
    def _precheck(self, action: str, caller: str, kwargs: dict[str, Any]):
        """Local guardrail check on the tool-call arguments.

        Returns a GuardrailResult. In the governed build, an AGT control-plane
        authorize() call is added right here (raising AGTBlocked on deny).
        """
        payload = " ".join(str(v) for v in kwargs.values())
        return guardrails.check(payload)

    async def call(self, tool_name: str, session: str, caller: str,
                   **kwargs: Any) -> dict[str, Any]:
        if tool_name not in self._tools:
            raise KeyError(f"{self.label} has no tool '{tool_name}'")

        BUS.publish(Event(
            type=EventType.MCP_IN, source=caller, target=self.node_id,
            session=session,
            message=f"call {self.label}.{tool_name}({_fmt(kwargs)})",
            data={"tool": tool_name, "args": kwargs},
        ))

        # --- LOCAL guardrail check (before the action runs) --------------
        action = f"{self.node_id}.{tool_name}"
        BUS.publish(Event(
            type=EventType.GUARDRAIL, source=self.node_id, session=session,
            message=f"guardrail check: {action}",
            data={"action": action},
        ))
        verdict = self._precheck(action, caller, kwargs)
        if not verdict.allowed:
            BUS.publish(Event(
                type=EventType.BLOCKED, source=self.node_id, target=caller,
                session=session,
                message=f"BLOCKED {action}: {verdict.reason}",
                data={"action": action, "reason": verdict.reason,
                      "rule": verdict.rule, "by": "local-guardrail"},
            ))
            BUS.publish(Event(type=EventType.STATUS, source=self.node_id,
                              session=session, message="IDLE",
                              data={"status": "IDLE"}))
            return {"blocked": True, "action": action, "reason": verdict.reason,
                    "rule": verdict.rule, "source": "local-guardrail"}

        BUS.publish(Event(type=EventType.STATUS, source=self.node_id,
                          session=session, message="RUNNING",
                          data={"status": "RUNNING"}))

        # Simulate network / compute latency.
        await asyncio.sleep(random.uniform(0.4, 1.1))
        result = await self._tools[tool_name].handler(**kwargs)

        BUS.publish(Event(
            type=EventType.MCP_OUT, source=self.node_id, target=caller,
            session=session,
            message=f"{self.label}.{tool_name} -> {_summary(result)}",
            data={"tool": tool_name, "result": result},
        ))
        BUS.publish(Event(type=EventType.STATUS, source=self.node_id,
                          session=session, message="IDLE",
                          data={"status": "IDLE"}))
        return result


def _fmt(kwargs: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v!r}" for k, v in kwargs.items())


def _summary(result: dict[str, Any]) -> str:
    keys = ", ".join(result.keys())
    return f"{{{keys}}}"
