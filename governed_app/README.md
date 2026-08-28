# governed_app — reference multi-agent app on the AGT platform

A clean, minimal rewrite of the vanilla agentic-mcp-ops app that uses the
AGT SDK through **only two decorators** — nothing else.

```python
from agt_sdk import governed, peer_verified
```

No `AutoKernel`, no `governance.authorize()`, no direct policy calls anywhere
in the agent code. This is what a real integration looks like once you strip
out the diagnostics and the plumbing.

Pair with **[../docs/AGT_INTEGRATION_PLAYBOOK.md](../docs/AGT_INTEGRATION_PLAYBOOK.md)**
— that document explains the *why*; this app is the *how*.

---

## What the app does

Same shape as the vanilla build: a **Coordinator** delegates a mission in
parallel to a **Financial Agent** and a **Research Agent**, each of which
calls a couple of MCP-style tools (FIN MCP + NEWS MCP) and returns findings
that the Coordinator fuses into a final answer.

The only thing that changed is where governance lives:

| Concern | Vanilla | governed_app |
|---|---|---|
| Tool-call authorization | manual `governance.authorize(...)` in every MCP handler | `@governed(action="fin_mcp.get_quote")` on the tool function |
| A2A trust check | manual mesh handshake code + `attest_handshake()` | `@peer_verified("Financial Agent")` on the delegating method |
| Identity attach | manual `AutoKernel.instance()` warm-up in FastAPI startup | happens automatically the first time a decorated function is defined |
| Policy fetch | manual `_policy_provider` wiring | ditto — the decorator triggers the singleton |

The vanilla `agentic_ops/` package remains alongside so you can diff the two.

---

## Layout — three processes, one per role

```
governed_app/
├── README.md                     ← you are here
├── .env.example                  ← copy to .env, paste registered credentials
├── requirements.txt              ← fastapi + uvicorn + agt-sdk[mesh,bootstrap]
├── run.py                        ← launcher: spawns 3 uvicorn processes, tees their output
├── logs/                         ← runtime logs (gitignored, see "Logs" below)
└── governed_ops/
    ├── __init__.py               ← runs logging_setup THEN config (before any SDK import)
    ├── logging_setup.py          ← the only module that configures `logging`
    ├── config.py                 ← dotenv + AGENT_ROLE → per-role credential + port
    ├── events.py                 ← tiny in-memory bus (per process)
    ├── guardrails.py             ← local regex safety net (offline)
    ├── mcp_servers.py            ← tools, each with @governed(action="…")
    ├── specialist.py             ← FinancialAgent + ResearchAgent (@governed on analyze)
    ├── coordinator.py            ← Coordinator with @peer_verified per delegation
    ├── worker_app.py             ← FastAPI for fin_agent / res_agent processes
    └── server.py                 ← FastAPI for the coordinator process
```

Three processes at runtime:

```
POST /mission ──▶ coordinator (8100)                             ┐
                     │  @peer_verified("Financial Agent")        │
                     ├─▶ HTTP POST /analyze ──▶ fin_agent (8101) │
                     │                                           │ replays
                     │  @peer_verified("Research Agent")         │ workers'
                     └─▶ HTTP POST /analyze ──▶ res_agent (8102) │ events
                                                                 ┘
```

Each process has its own DID recovered from CP escrow. Each specialist's
tool calls are gated by `@governed`; each delegation is gated by
`@peer_verified` inside the coordinator.

Every module docstring says whether it touches the SDK. Short version: only
`agents.py` and `mcp_servers.py` import from `agt_sdk`, and both import only
`governed`, `peer_verified`, and the two exception classes.

---

## Setup

### 1. Register the three agents in the CP UI (once)

Log into the AGT dashboard for org `demodevelop` (or your org):

1. Governance → Agents → **Register Agent**
2. Register three agents with these EXACT names — they are what
   `@peer_verified(...)` looks up:
   - `Coordinator Agent`
   - `Financial Agent`
   - `Research Agent`
3. Copy each shown `agt_...` credential — the UI shows it once.

### 2. Fill in `.env`

```bash
cd governed_app
cp .env.example .env
```

Paste the three credentials and set the org passphrase (≥32 chars, host-local,
never transmitted):

```ini
AGT_CP_URL=http://localhost:20355
AGT_ORG_CODE=demodevelop
AGT_AGENT_PASSPHRASE=<≥32 char org-wide secret>

AGT_COORDINATOR_TOKEN=agt_...
AGT_FIN_AGENT_TOKEN=agt_...
AGT_RES_AGENT_TOKEN=agt_...
```

### 3. Install and run

```bash
# from the repo root
python -m venv .venv && .venv\Scripts\activate       # Windows
# source .venv/bin/activate                            # macOS/Linux

pip install -r governed_app/requirements.txt
pip install --no-index --find-links agt-sdk/wheels 'agt-sdk[mesh,bootstrap]'

python governed_app/run.py
# → http://127.0.0.1:8100
```

---

## Trying it

```bash
# 1. Confirm each of the three processes has an escrow-recovered DID
curl -s http://127.0.0.1:8101/whoami   # fin_agent worker
curl -s http://127.0.0.1:8102/whoami   # res_agent worker
curl -s http://127.0.0.1:8100/whoami   # coordinator
# → three distinct did:mesh:… values, one per registered agent

# 2. Run a mission with an allowed entity
curl -s -X POST http://127.0.0.1:8100/mission \
     -H 'content-type: application/json' \
     -d '{"mission":"Analyze the market trends for HDFC bank"}'
# → { "status":"done", "final":"FINAL INTELLIGENCE - HDFC bank …", … }

# 3. Run a mission the policy denies (SBI is blocked by our test policy)
curl -s -X POST http://127.0.0.1:8100/mission \
     -H 'content-type: application/json' \
     -d '{"mission":"Analyze the market trends for SBI"}'
# → the workers' tool calls return {"blocked":true, "reason":"…"}
#   Coordinator returns a partial view; /events replays the worker BLOCKED events.

# 4. Stop all processes (Ctrl+C in the run.py terminal), restart, hit /whoami again
#    → same three DIDs  (recovered from CP escrow using AGT_AGENT_PASSPHRASE)
```

---

## Logs

Everything is written to `governed_app/logs/` (gitignored — the files carry
agent DIDs, policy decisions and request payloads). Override the location with
`AGT_LOG_DIR`; set the verbosity with `AGT_LOG_LEVEL` (already `DEBUG` in
`.env.example`).

| File | Contents |
|---|---|
| `coordinator.log`, `fin_agent.log`, `res_agent.log` | every `logging` record that process emits, at `AGT_LOG_LEVEL`, including uvicorn's |
| `governance.log` | governance-only slice — `agent_os.*`, `agt_sdk.*`, `governed_ops.*` — always at DEBUG regardless of `AGT_LOG_LEVEL` |
| `<role>.console.log` | raw stdout/stderr of that child, teed by `run.py` |
| `launcher.log` | `run.py`'s own lines, including `child exited (N)` |

**Start here when a call is blocked and you don't know which lane blocked it:**

```bash
# WHICH policies are bound to each process, and which lane enforces each
grep "governed_ops.policy" logs/governance.log

# Did any policy fail to parse, or arrive empty?
grep -E "IMPL-059|EMPTY policy bundle|did not parse" logs/governance.log

# The allow/deny trail
grep -iE "blocked|denied|decision posted" logs/governance.log
```

`governed_ops/policy_report.py` writes the first of those at startup and again
after **every** bundle refresh, so it stays true when you edit a policy in the
CP while the agents are running:

```
policy bundle (startup): 2 policy/policies — rule_engine=on evaluator=compiled
  policy 'agt-travel-planner-policy' enforced_by=rules[]+lists | rules=16 [...] | blocked_patterns=14
  policy 'agt-vanilla-test-policy'   enforced_by=rules[]       | rules=5  [...] | no defaults lists
```

`enforced_by` is the column that matters, because a policy can be delivered and
still enforce nothing:

| `enforced_by` | Meaning |
|---|---|
| `rules[]` | the rule engine evaluates it (deny short-circuits first) |
| `lists` | `defaults.blocked_actions` / `blocked_patterns` / `require_approval` |
| `rules[]+lists` | both, engine first — see [Policy authoring reminder](#policy-authoring-reminder) |
| `NOTHING` | **delivered but enforcing nothing** — either the document didn't parse, or the engine is off. A WARNING on the next line says which. |

The SDK itself only ever logs the bundle *count* (`fetched 2 policies`), and its
`diagnostics()["policies_loaded"]` lists the legacy-list lane only — so a
rules-only policy reads as absent there. That is why this is reported from the
app rather than taken from the SDK.

`governance.log` is deliberately DEBUG-only-always, because the one line that
tells you whether the `rules[]` lane is enforcing at all —
`IMPL-059: policy '…' did not parse as a rule document; list-model only` —
is otherwise unrecoverable after the fact. Without it, a deny that came from
`defaults.blocked_actions` is indistinguishable from one that came from a
`rules[]` deny, and they mean very different things (see
[Policy authoring reminder](#policy-authoring-reminder)).

Why this needed its own module: `AGT_LOG_LEVEL` is applied by the SDK to the
**`agt_sdk`** logger only, while the engine that decides allow/deny logs under
**`agent_os.*`**; uvicorn leaves the root logger bare, so those records used to
fall through to `logging.lastResort` (WARNING+, unformatted) or vanish
entirely. `governed_ops/logging_setup.py` documents the full failure mode.

---

## Where the SDK actually appears in the code

Two imports, two decorators, two exceptions. Grep it:

```bash
$ grep -R "agt_sdk" governed_app/governed_ops
mcp_servers.py:  from agt_sdk import governed
agents.py:       from agt_sdk import governed, peer_verified
agents.py:       from agt_sdk.exceptions import AGTBlocked, AGTPeerUntrusted
```

That is the complete integration surface. Everything else — bootstrap, key
escrow, mesh identity, policy bundle fetch, trust graph publisher — is
initialised automatically the first time a decorated function is applied,
because `@governed` / `@peer_verified` call `AutoKernel.instance()` inside
themselves.

## What each decorator is doing

```python
@governed(action="fin_mcp.get_quote")            #  ← policy gate on this action
async def fin_get_quote(entity: str) -> dict:    #  raises AGTBlocked on deny
    ...

@peer_verified("Financial Agent")                #  ← A2A trust gate on this peer
async def _delegate_to_finance(self, entity):    #  raises AGTPeerUntrusted on refuse
    return await FinancialAgent.analyze(entity)
```

`@governed` binds the function's arguments (via `inspect.signature`) into a
params dict, ships them to the CP-side rule engine, and raises `AGTBlocked`
if any rule matches with `action: deny`. The agent code catches and converts
to a "blocked" result — see `agents.py::FinancialAgent.analyze`.

`@peer_verified` runs the CP's a2a-gate: is the named peer registered, active,
and above the min-trust floor? If yes, the wrapped body runs; if no,
`AGTPeerUntrusted` is raised.

You do not have to `await AutoKernel.instance()`, register anything, or wire
a policy provider — the decorators do all of that.

---

## Policy authoring reminder

The policy that governs this app lives at
[`../agt_vanilla_test_policy.yaml`](../agt_vanilla_test_policy.yaml). Two
rules you'll see again and again:

1. **DENY priority > ALLOW priority.** First match wins after sorting DESC.
2. **`operator: contains` on `field: params` does not work.** `ctx["params"]`
   is a *dict*; `"sbi" in dict` checks keys. Use `operator: matches` with a
   regex like `(?i)sbi`, or address a specific kwarg with
   `field: entity, operator: contains`.

The playbook (`docs/AGT_INTEGRATION_PLAYBOOK.md` §5) has the full rundown.

---

## Comparing with the vanilla app

Same features, side-by-side files:

| Concern | vanilla `agentic_ops/` | governed_app `governed_ops/` |
|---|---|---|
| Governance bridge | `governance.py` (95 lines) | *(none — decorators directly)* |
| Mesh identity helper | `agents/mesh.py` (~120 lines) | *(none — SDK handles it)* |
| MCP tool call authz | `mcp/base.py::_precheck` (imperative `authorize()` + try/except) | `@governed` on the tool function |
| A2A trust handshake | `agents/base.py::handshake` + `attest_handshake` | `@peer_verified` on the delegator |
| SDK warm-up | manual `AutoKernel.instance()` in FastAPI startup | *(implicit — first decorator does it)* |
| Total non-SDK plumbing | ~350 lines | ~30 lines (`config.py` env mapping) |

If you're maintaining an existing app, the vanilla layout is a fine
migration path — you keep the shape and just replace mock stubs. If you're
starting fresh, use the shape of this app.
