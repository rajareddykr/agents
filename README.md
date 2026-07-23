# Agentic AI & MCP — Agent Ops (Vanilla)

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
├── smoke_test.py              # autonomous, stdlib-only self-test
├── tests/
│   └── test_pipeline.py       # pytest suite (15 cases)
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

## Verify it (autonomous self-test)

No server needed — runs the whole pipeline in-process on mock data:

```bash
python smoke_test.py        # prints PASS/FAIL per check, exit 0 on success
pytest -q                   # 15 granular cases
```

Both assert the full multi-agent flow: parallel delegation, two-way A2A
messaging, both MCP servers called, simulated LLM calls, final fusion, plus the
guardrail blocking a non-compliant mission.

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
