# Agentic AI & MCP — Agent Ops

A **plain, un-governed multi-agent orchestration engine** with a live web
dashboard. This is the clean baseline of the `agentic-mcp-ops` project with
**agt-sdk, mesh identity, and the A2A trust handshake removed** — so you can add
them back yourself, step by step, for hands-on learning.

A **Coordinator** agent plans a mission and delegates in parallel to two
specialists — a **Financial Agent** and a **Research Agent** — each of which
calls tools exposed by **MCP servers** (FIN MCP, NEWS MCP). Every step (flow
milestones, simulated LLM calls, MCP request/response, agent-to-agent messages,
guardrail checks/blocks) is emitted as a structured event and streamed to the
dashboard over a WebSocket in real time.

> Runs **fully offline** — LLM reasoning is simulated and MCP tools return mock
> data. No API keys, no control plane, no crypto. Nothing to install beyond
> FastAPI + Uvicorn.

## What this build is (and is NOT)

| Included (vanilla) | Removed (add back via the migration guide) |
|---|---|
| Coordinator + 2 specialist agents | `agt-sdk` governance (`@governed`, control-plane checks) |
| Agent-to-agent (A2A) messaging | Mesh identity (`did:mesh`, Ed25519 keys) |
| MCP tool servers (FIN, NEWS) | A2A trust **handshake** (challenge/signed-response) |
| Event bus + live dashboard | CP publishing (trust graph, decisions, handshakes) |
| Local regex **guardrails** | Per-agent bootstrap tokens |
| Finnhub live/mock data | — |

The **local guardrail** is the only pre-execution check in this build. It is
the natural seam where AGT governance plugs in later.

## Architecture

```
                    ┌───────────────┐
   mission  ───────▶│  Coordinator  │  plan · guardrail · fuse
                    └──────┬────┬───┘
             A2A dispatch  │    │  A2A dispatch   (no handshake in vanilla)
                ┌──────────┘    └──────────┐
        ┌───────▼────────┐         ┌───────▼────────┐
        │ Financial Agent│         │ Research Agent │
        └───────┬────────┘         └───────┬────────┘
          MCP calls│                 MCP calls│
        ┌──────────▼──────┐        ┌──────────▼─────┐
        │     FIN MCP     │        │    NEWS MCP    │
        │ get_quote       │        │ search_news    │
        │ get_fundamentals│        │ get_sentiment  │
        │ get_price_trend │        └────────────────┘
        └─────────────────┘
```

## Project layout

```
agentic-mcp-ops-vanilla/
├── run.py                     # launcher (single or distributed)
├── requirements.txt           # fastapi + uvicorn only
├── .env.example               # all optional; app runs offline by default
├── smoke_test.py              # offline pipeline self-test (stdlib only)
├── verify_did_stability.py    # end-to-end: same DID across restart (needs CP)
├── tests/
│   ├── test_pipeline.py       # 15 pipeline cases (offline)
│   └── test_registration.py   # 9 IMPL-067 register-once + passphrase cases
├── docs/
│   ├── ARCHITECTURE.md        # how the pieces fit together
│   └── AGT_MIGRATION_GUIDE.md # add agt-sdk / mesh / handshake, step by step
└── agentic_ops/
    ├── events.py              # structured events + async pub/sub bus  (pure)
    ├── guardrails.py          # local regex guardrails                (pure)
    ├── config.py              # .env + mock/live + run-mode
    ├── orchestrator.py        # wires MCP + agents, runs a mission
    ├── server.py              # FastAPI app + WebSocket + REST
    ├── agent_service.py       # per-agent worker process (distributed)
    ├── dashboard.html         # single-file live dashboard UI
    ├── mcp/
    │   ├── base.py            # MCP server abstraction + guardrail seam
    │   ├── fin_mcp.py         # financial tools (mock/finnhub)
    │   └── news_mcp.py        # news/sentiment tools (mock/finnhub)
    ├── agents/
    │   ├── base.py            # base agent (simulated LLM 'think', A2A send)
    │   ├── coordinator.py
    │   ├── financial_agent.py
    │   └── research_agent.py
    └── providers/
        └── finnhub.py         # thin stdlib Finnhub client
```

## Run it

```bash
python -m venv .venv && .venv\Scripts\activate      # Windows
# source .venv/bin/activate                          # macOS/Linux
pip install -r requirements.txt

# Simplest: everything in one process (best for reading the code)
set MODE=single && python run.py                     # Windows
# MODE=single python run.py                           # macOS/Linux

# open http://127.0.0.1:8000
```

Type a mission (e.g. *"Analyze the market trends for HDFC bank"*) and press
**Run Mission**. Watch the topology light up and the event stream fill in real
time; the fused result appears under **Final Intelligence**.

Distributed mode (default) runs each agent in its own process:

```bash
python run.py     # workers on :8071/:8072, dashboard on :8000
```

## Governed identity (IMPL-067 register-once + key escrow)

Each of the three roles registers **once** in the CP UI (Register Agent) and
receives a durable ``agt_...`` credential. Combined with a ≥32-char org-wide
``AGT_AGENT_PASSPHRASE``, every restart recovers the private key from escrow
and the agent comes back with the **same** ``did:mesh:...`` DID — trust score,
drift history and policy bindings all carry over.

Set up `.env`:

```ini
AGT_CP_URL=http://localhost:20355
AGT_ORG_CODE=demodevelop
AGT_AGENT_PASSPHRASE=<≥32 char org-wide secret, host-local, never transmitted>
AGT_COORDINATOR_TOKEN=agt_...   # from Register Agent in the CP UI
AGT_FIN_AGENT_TOKEN=agt_...
AGT_RES_AGENT_TOKEN=agt_...
AGT_AUTO_MINT=0                 # keep durable creds; do not overwrite on launch
```

The three registered display names must match the ones in
``agentic_ops/governance.py``:
``Coordinator Agent`` · ``Financial Agent`` · ``Research Agent``.

## Verify it (self-tests)

Three tiers, each smaller-scope than the next:

```bash
python smoke_test.py                # 1) offline pipeline — no server, no CP
pytest -q                           # 2) 24 unit + integration cases
python verify_did_stability.py      # 3) end-to-end: same DID across restart
```

1. **`smoke_test.py`** runs the whole multi-agent pipeline in-process on mock
   data: parallel delegation, two-way A2A messaging, both MCP servers called,
   simulated LLM calls, final fusion, plus the guardrail blocking a
   non-compliant mission.
2. **`pytest -q`** — 15 pipeline cases + 9 registration/passphrase cases
   (`tests/test_registration.py`): durable-token env mapping, stale-bootstrap
   clearing, legacy-token fallback, missing/short passphrase, per-role name
   binding, `SdkConfig.uses_agent_credential`, mesh identity fallback and
   SDK-identity preference.
3. **`verify_did_stability.py`** starts the three workers in fresh OS
   processes, captures each ``/whoami`` DID, stops everything, restarts, and
   asserts the DIDs are byte-identical. Requires a running CP with the three
   agents already registered — otherwise it prints ``SKIP`` and exits 0.

Example passing run of test 3:

```
=== ROUND 1 (first boot: generate keypair, attach, escrow) ===
  [R1] fin_agent    -> did:mesh:410de4157617bd16f732086b
  [R1] res_agent    -> did:mesh:b788509bc326d1e3134fc314
  [R1] coordinator  -> did:mesh:55e24ebed0ba0e456d615660
=== ROUND 2 (restart: recover from escrow — SAME DID expected) ===
  [R2] fin_agent    -> did:mesh:410de4157617bd16f732086b
  [R2] res_agent    -> did:mesh:b788509bc326d1e3134fc314
  [R2] coordinator  -> did:mesh:55e24ebed0ba0e456d615660
RESULT: PASS — DIDs stable across restart
```

## Live data (Finnhub, optional)

The FIN/NEWS MCP tools use **real Finnhub data** when `FINNHUB_API_KEY` is set,
and fall back to deterministic **mock** data otherwise (any failed live call
also degrades to mock). Copy `.env.example` to `.env` and paste your key. The
dashboard header shows a **DATA · LIVE** / **DATA · MOCK** badge.

## Next: add governance & mesh

When you're ready, follow **[docs/AGT_MIGRATION_GUIDE.md](docs/AGT_MIGRATION_GUIDE.md)**
to re-introduce agt-sdk, per-agent mesh identities, and the A2A handshake — one
small, testable step at a time, each mapped to the exact file and line you touch.
```
