# Architecture — Vanilla build

This document explains how the pieces fit together and, crucially, **where the
seams are** so you know exactly what changes when you add governance/mesh.

## 1. The event backbone (`events.py`)

Everything is observable because every action publishes a structured `Event` to
a global async `EventBus`. The dashboard subscribes over a WebSocket; the
distributed workers capture events per-session and hand them back to the
Coordinator to replay.

Event types: `FLOW`, `MODEL` (simulated LLM), `MCP_IN`, `MCP_OUT`, `A2A`,
`GUARDRAIL`, `BLOCKED`, `STATUS`. This module is pure stdlib and is **identical**
in the vanilla and governed builds — you never touch it during migration.

## 2. Agents (`agents/`)

- **`base.Agent`** — shared behaviour: `think()` (simulated LLM call + `MODEL`
  event), `send()` (emits an `A2A` event), `flow()`, `_status()`.
  Vanilla has **no** `self.identity` / `self.did` and **no** `handshake()`.
- **`Coordinator`** — guardrails the mission, plans, dispatches to both
  specialists **in parallel** (`asyncio.gather`), guardrails their findings,
  and fuses a final answer.
- **`FinancialAgent` / `ResearchAgent`** — each calls its MCP server's tools and
  returns a structured finding. Plain `async def analyze()` — **no decorator**.

### Agent-to-agent communication

This is the "multi-agent communication" the project is about, and it is fully
present in vanilla:

1. Coordinator → specialists: an `A2A` event (`send`) **plus** the actual call
   (`fin_agent.analyze()` in-process, or `POST /analyze` in distributed mode).
2. Specialists → Coordinator: `A2A` events ("financial findings ready", …).

What vanilla does **not** do: verify a peer's cryptographic identity before
delegating. In the governed build a `handshake()` gates step 1 on a
challenge/signed-response against the peer's `did:mesh`.

## 3. MCP servers (`mcp/`)

`MCPServer.call()` is the tool-execution path. For every call it:

1. emits `MCP_IN`,
2. runs a **pre-check** (`_precheck` → local guardrail),
3. on allow: emits `STATUS=RUNNING`, runs the tool handler, emits `MCP_OUT`,
4. on block: emits `BLOCKED` and returns `{"blocked": true, ...}`.

`_precheck` is **the governance seam** (see migration guide, step 3). Tools
themselves (`fin_mcp`, `news_mcp`) branch on `config.data_mode()` to return
live Finnhub data or deterministic mock data.

## 4. Orchestration & transport (`orchestrator.py`)

`Orchestrator.run_mission()` builds the agents (in-process objects, or
`RemoteAgent` HTTP proxies when `config.DISTRIBUTED`), constructs a
`Coordinator`, and runs it. In distributed mode each worker (`agent_service.py`)
hosts one agent, runs the analysis under its own process, and returns the events
it emitted so the Coordinator can replay them to the dashboard.

Vanilla `RemoteAgent` is a **plain HTTP proxy** — no `/whoami` DID discovery, no
handshake. The governed build re-adds both.

## 5. Web layer (`server.py`, `dashboard.html`)

FastAPI serves the single-file dashboard, exposes `GET /api/topology`,
`POST /api/mission`, and the `/ws` WebSocket. The dashboard renders the topology,
live event stream, counters, and the fused result. The header shows a
`BUILD · VANILLA` badge and the data mode.

## Data flow of one mission

```
POST /api/mission
   └─ Orchestrator.run_mission(mission)
        ├─ extract_entity(mission)
        ├─ Coordinator.run(mission, entity)
        │    ├─ guardrail(mission)                        → GUARDRAIL / maybe BLOCKED
        │    ├─ think("plan")                             → MODEL
        │    ├─ send(fin_agent) / send(res_agent)         → A2A  (parallel)
        │    ├─ gather(fin.analyze(), res.analyze())
        │    │     └─ each: think → MCP_IN → guardrail → MCP_OUT → finding
        │    ├─ specialists send("coordinator", …)        → A2A
        │    ├─ guardrail(findings)                       → GUARDRAIL
        │    ├─ think("fuse")                             → MODEL
        │    └─ FLOW "Final intelligence ready" (+final)
        └─ result streamed as events over /ws
```

## Where governance/mesh will attach (summary)

| Concern | Vanilla today | Attaches at |
|---|---|---|
| Per-action authorization | local regex guardrail | `mcp/base.py::_precheck`, agent `analyze()` decorators |
| Agent identity | none | new `agents/mesh.py`; `agents/base.Agent.__init__` |
| Peer trust before A2A | none | new `Agent.handshake()`; `Coordinator.run` |
| Distributed identity | plain HTTP | `agent_service.py::whoami`, `RemoteAgent.__init__` |
| Config | Finnhub + ports | `config.py` (AGT_CP_URL, org, tokens) |

Full instructions in **AGT_MIGRATION_GUIDE.md**.
