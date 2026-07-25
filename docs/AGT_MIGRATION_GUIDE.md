# AGT Migration Guide — add governance, mesh & handshake, step by step

This guide takes the **vanilla** build and re-introduces the three things it
deliberately left out, one testable step at a time:

1. **Governance** — every action authorized by the AGT control plane (`agt-sdk`)
2. **Mesh identity** — each agent gets a real `did:mesh` + Ed25519 keypair
3. **A2A handshake** — a challenge/signed-response before agents delegate

Each step is self-contained: do it, run the self-test, see it still work, then
move on. Nothing here is speculative — it mirrors the governed reference build
(`agentic-mcp-ops`), so you can copy the shapes directly.

> **Golden rule:** keep it **fail-open** during migration. If the SDK is missing
> or the control plane is unreachable, the app should keep running ungoverned.
> That way each step is safe and you never end up with a dead demo.

---

## Step 0 — Prerequisites

You need a running AGT control plane (with its database + schema), a seeded org
and operator, and the SDK installed.

### 0a. Database + schema (`agents_gov`)

The control plane **never runs `CREATE SCHEMA` itself** — it pins the connection's
`search_path` to the configured schema and *verifies* it exists on startup. If
the schema is missing you get, during migrations:

```
asyncpg.exceptions.InvalidSchemaNameError: no schema has been selected to create in
[SQL: CREATE TABLE alembic_version ...]
```

So after (re)creating the database, create the schema **on the same Postgres the
CP connects to** — connect as a superuser or the `agt` role to
`agt_control_plane` and run:

```sql
CREATE SCHEMA IF NOT EXISTS agents_gov AUTHORIZATION agt;
GRANT ALL ON SCHEMA agents_gov TO agt;
ALTER ROLE agt IN DATABASE agt_control_plane SET search_path = agents_gov, public;
```

> **Port gotcha:** the CP connects via `DATABASE_URL=...@host.docker.internal:5432`
> but the compose file may also expose `PG_PORT=5433`. Create the schema on the
> instance the CP actually reaches (from the host that's `localhost:5432`, **not**
> `5433`). Confirm with `SELECT current_database(), inet_server_port();`.

### 0b. Seed the org + operator (`demodevelop` / `demoadmin`)

The CP seeds a default org and a `superadmin` user on startup from env vars. Set
these so you get exactly org `demodevelop` and user `demoadmin` / `changeme`:

```dotenv
# CP .env (infra/.env)
DEFAULT_ORG_SLUG=demodevelop
DEFAULT_ORG_NAME=Demo Develop
SEED_ADMIN_USERNAME=demoadmin
SEED_ADMIN_PASSWORD=changeme
SEED_ADMIN_EMAIL=demoadmin@agt.local
```

Restart the CP (fresh DB → migrates, then seeds). Verify:

```sql
SELECT slug FROM agents_gov.organisations WHERE slug='demodevelop';
SELECT username, role FROM agents_gov.users WHERE username='demoadmin';
```

> The seed is idempotent and only fires when the org/user are **absent** — so do
> it on a fresh DB. Don't use `python -m app.cli.tenant onboard` for this: its
> `local` auth-mode auto-generates a one-time password (you can't pin `changeme`)
> and uses the admin email as the username.

### 0c. Install the SDK

```bash
# from your local SDK checkout / wheelhouse
pip install -e path/to/agent-os-sdk[mesh,bootstrap]
# (avoid [all] — it pulls framework adapters that pin Python <3.14)
```

### 0d. Add AGT settings to the vanilla app's `.env`

```dotenv
AGT_CP_URL=http://localhost:20355
AGT_ORG_CODE=demodevelop
AGT_GOVERNANCE_ENABLED=true
AGT_FAIL_OPEN=true                      # keep true during migration
AGT_HEARTBEAT_SECONDS=10
# AGT_BOOTSTRAP_TOKEN / AGT_CP_TOKEN / AGT_AGENT_ID are set in Step 0.5
```

**Checkpoint:** `python smoke_test.py` still passes (you haven't wired anything
yet).

---

## Step 0.5 — Register the agent (one-time) ⚠ read before Step 1

Bootstrap tokens are **single-use**. Consuming one registers the agent and mints
a **long-lived `api_token`**; after that you drop the bootstrap token and run on
the long-lived credential. Leaving `AGT_BOOTSTRAP_TOKEN` set on every run is the
#1 cause of the `401 Unauthorized` on `/policies/bundle/resolve` — the second run
tries to re-consume a spent token.

### 0.5a. Mint a fresh bootstrap token (as `demoadmin`)

```powershell
$cp = "http://localhost:20355"
$login = Invoke-RestMethod "$cp/api/v1/auth/login" -Method Post -ContentType application/json `
  -Body '{"username":"demoadmin","password":"changeme"}'
$tok = $login.data.access_token; if (-not $tok) { $tok = $login.access_token }

$boot = Invoke-RestMethod "$cp/api/v1/orgs/demodevelop/agents/bootstrap-tokens" -Method Post `
  -Headers @{ Authorization = "Bearer $tok" } -ContentType application/json `
  -Body '{"agent_type":"langgraph-agent","ttl_hours":720,"note":"vanilla migration"}'
$boot.data.token          # -> agt_boot_...  (shown once)
```

Put it in `.env`: `AGT_BOOTSTRAP_TOKEN=agt_boot_...`

### 0.5b. Consume it once with `register_once.py`

The project ships `register_once.py` (calls `agt_sdk._bootstrap.exchange(...)`).
Run it once:

```bash
python register_once.py
# => REGISTERED ✓  DID: did:mesh:...   api_token: agt_...
```

### 0.5c. Switch to the long-lived credential

Paste the printed values into `.env` and **remove the (now-spent) bootstrap
token**:

```dotenv
AGT_AGENT_ID=did:mesh:...       # printed DID
AGT_CP_TOKEN=agt_...            # printed api_token
# AGT_BOOTSTRAP_TOKEN=          <- delete / comment out
```

Now every run authenticates with `AGT_CP_TOKEN` (no re-consume, no 401), and the
DID shows in the dashboard's **Agent Registry**. This is choice 2 in the SDK's
own restart contract.

> If `register_once.py` reports `api_token: (none)`, your CP predates IMPL-024.5 —
> mint an agent API token via the operator API and use that as `AGT_CP_TOKEN`.
> `AGT_CP_TOKEN` covers everything Steps 1–3 need (policy resolve, decision ingest,
> heartbeat — all header auth). The Ed25519 signing key only matters for the Step 4
> handshake; at that point re-run `register_once.py` with a fresh token so the
> process holds the private key.

---

## Step 1 — A governance module + the MCP seam

**Goal:** authorize each MCP tool call against the control plane. On deny, the
tool is blocked exactly like a guardrail block (the event shape already exists).

### 1a. Create `agentic_ops/governance.py`

A thin, graceful wrapper around `agt_sdk`. If the SDK import fails or is
disabled, everything becomes a no-op (fail-open).

```python
import asyncio, functools, inspect, logging, os
log = logging.getLogger("agentic_ops.governance")

_ENABLED = os.environ.get("AGT_GOVERNANCE_ENABLED", "true").lower() not in ("0","false","no")
FAIL_OPEN = os.environ.get("AGT_FAIL_OPEN", "true").lower() not in ("0","false","no")

try:
    if not _ENABLED:
        raise ImportError("governance disabled")
    from agt_sdk import governed as _governed
    from agt_sdk.exceptions import AGTBlocked
    AVAILABLE = True
except Exception as e:
    _governed = None
    AVAILABLE = False
    class AGTBlocked(Exception): ...
    log.warning("agt_sdk unavailable (%s) — running ungoverned", e)

def enabled() -> bool:
    return AVAILABLE

_authorizers = {}
def _authorizer_for(action: str):
    if action not in _authorizers:
        if AVAILABLE:
            @_governed(action=action)
            def _a(**kw): return kw
            _authorizers[action] = _a
        else:
            _authorizers[action] = lambda **kw: kw
    return _authorizers[action]

def authorize(action: str, node_id: str | None = None, **context) -> None:
    """Raises AGTBlocked on deny. Fails open on transport errors."""
    fn = _authorizer_for(action)
    try:
        fn(**context)
    except AGTBlocked:
        raise
    except Exception as e:
        if FAIL_OPEN:
            log.warning("govern check errored for %s (%s) — failing open", action, e)
        else:
            raise
```

### 1b. Wire the MCP seam — `agentic_ops/mcp/base.py`

The vanilla code already isolates the pre-check in `MCPServer._precheck`. Add the
governance call **alongside** the local guardrail (keep both — defence in depth):

```python
from .. import governance   # new import

def _precheck(self, action, caller, kwargs):
    # existing local guardrail first
    payload = " ".join(str(v) for v in kwargs.values())
    local = guardrails.check(payload)
    if not local.allowed:
        return local
    # NEW: control-plane authorization (raises AGTBlocked on deny)
    governance.authorize(action, caller, **{k: v for k, v in kwargs.items()
                                            if isinstance(v, (str, int, float, bool))})
    return local
```

Then wrap the `_precheck` call site in `call()` to convert `AGTBlocked` into the
existing BLOCKED event/return (you already emit that on `verdict.allowed = False`;
just catch `governance.AGTBlocked` around `_precheck` and reuse that branch).

**Test:**
```bash
python smoke_test.py         # still PASS (fail-open if CP down)
python run.py                # run a mission; check the AGT Decision Explorer
```
**Expected:** with the CP up and a deny rule for e.g. `fin_mcp.get_quote`, that
tool call turns into a BLOCKED event and the decision shows in the dashboard.

---

## Step 2 — Govern the agent methods

**Goal:** authorize the whole `analyze()` under the agent, not just each tool.

### 2a. Add a `@governed` decorator to `governance.py`

```python
def governed(action: str):
    def deco(fn):
        is_async = inspect.iscoroutinefunction(fn)
        @functools.wraps(fn)
        async def awrapper(self, *a, **k):
            node = getattr(self, "node_id", None)
            try:
                await asyncio.to_thread(authorize, action, node)
            except AGTBlocked as e:
                # graceful degraded result (mirror your blocked-result shape)
                return {"agent": getattr(self, "label", "Agent"),
                        "verdict": "blocked", "sentiment": "blocked",
                        "blocked": True, "reason": str(e),
                        "summary": f"blocked by governance ({e})"}
            return await fn(self, *a, **k)
        return awrapper if is_async else fn
    return deco
```

### 2b. Decorate the specialists

```python
# financial_agent.py
from .. import governance

@governance.governed(action="fin_agent.analyze")
async def analyze(self, entity): ...

# research_agent.py
@governance.governed(action="res_agent.analyze")
async def analyze(self, entity): ...
```

**Test:** `python smoke_test.py` (still PASS). With a deny rule on
`fin_agent.analyze`, the financial finding returns `verdict="blocked"` and the
Coordinator still fuses gracefully.

---

## Step 3 — Per-agent identities (process-per-agent)

**Goal:** the Coordinator, Financial, and Research agents each appear as a
**distinct** agent in the AGT registry, with decisions attributed correctly.

`agt_sdk` binds **one identity per process** (from the token + `AGT_AGENT_*` env
read at import time). So distinct identities require **distinct processes** —
which the distributed launcher already gives you.

### 3a. Seed identity from `AGENT_ROLE` **before** importing `agt_sdk`

In `governance.py`, at module top (before the SDK import), map roles to tokens:

```python
_ROLE = (os.environ.get("AGENT_ROLE") or "coordinator").strip()
_SPECS = {
  "coordinator": ("AGT_COORDINATOR_TOKEN", "Coordinator Agent", "agentic-coordinator"),
  "fin_agent":   ("AGT_FIN_AGENT_TOKEN",   "Financial Agent",   "agentic-fin-agent"),
  "res_agent":   ("AGT_RES_AGENT_TOKEN",   "Research Agent",    "agentic-res-agent"),
}
_tok_env, _name, _id = _SPECS.get(_ROLE, _SPECS["coordinator"])
if os.environ.get(_tok_env):
    os.environ["AGT_BOOTSTRAP_TOKEN"] = os.environ[_tok_env]
os.environ["AGT_AGENT_NAME"] = _name
os.environ["AGT_AGENT_ID"]   = _id
# ... only now: from agt_sdk import governed ...
```

### 3b. Register on startup

Add a one-time `agent_online` authorize in each process's FastAPI `startup` so
the process announces itself to the control plane at boot (forces the SDK to warm
up under *this* process's identity, and writes a liveness decision so the agent
appears in the Registry before any mission runs).

`server.py` (Coordinator process):

```python
import asyncio
from . import governance

@app.on_event("startup")
async def _register_on_startup():
    # coordinator process announces itself; fail-open, non-fatal
    await asyncio.to_thread(governance.authorize, "agent_online", "coordinator")
```

`agent_service.py` (worker process — `ROLE` is `fin_agent` / `res_agent`):

```python
import asyncio
from . import governance

@app.on_event("startup")
async def _register_on_startup():
    await asyncio.to_thread(governance.authorize, "agent_online", ROLE)
```

Wrap it in `asyncio.to_thread` (the `authorize` call is sync + does network) and
rely on fail-open so an unreachable CP never blocks startup.

### 3c. `.env` — one token per agent

Each agent process needs its **own** identity. Two ways:

* **Per-agent bootstrap tokens** (mint three via Step 0.5a, one per role):

  ```dotenv
  AGT_COORDINATOR_TOKEN=agt_boot_...
  AGT_FIN_AGENT_TOKEN=agt_boot_...
  AGT_RES_AGENT_TOKEN=agt_boot_...
  ```

* **Or** register each role once with `register_once.py` (set `AGENT_ROLE` first)
  and store a per-role `AGT_CP_TOKEN` + `AGT_AGENT_ID`. Same single-use rule as
  Step 0.5 — one consume per role, then run on the long-lived token.

**Test:** `python run.py` (distributed). **Expected:** three agents in the AGT
Agent Registry with distinct DIDs; each tool decision attributed to the right one.

> Pitfall: register **only this process's own role**. Registering other roles
> from one process attributes them all to that process's single kernel identity
> (the classic "everything shows up as Coordinator" bug).

---

## Step 4 — Mesh identity + A2A handshake

**Goal:** before the Coordinator delegates, run a real challenge/signed-response
handshake with each peer, so the AGT trust graph shows genuine A2A edges.

### 4a. Create `agentic_ops/agents/mesh.py`

Wrap `agentmesh` identities + `TrustHandshake`:

```python
from agentmesh.identity.agent_id import AgentIdentity, IdentityRegistry
from agentmesh.trust.handshake import TrustHandshake, HandshakeResult

REGISTRY = IdentityRegistry()
_IDENTITIES = {}
_MIN_TRUST = 500

def ensure_identity(node_id, capabilities=None):
    if node_id in _IDENTITIES: return _IDENTITIES[node_id]
    idn = AgentIdentity.create(name=node_id, sponsor="agentic-mcp-ops@demo.local",
                               capabilities=capabilities or [f"{node_id}.analyze"])
    REGISTRY.register(idn); _IDENTITIES[node_id] = idn
    return idn

async def handshake(initiator_did, peer_did):
    ts = TrustHandshake(agent_did=initiator_did,
                        identity=REGISTRY.get(initiator_did), registry=REGISTRY)
    return await ts.initiate(peer_did=peer_did,
                             required_trust_score=_MIN_TRUST, use_cache=False)
```

### 4b. Give agents an identity — `agents/base.py`

```python
from . import mesh

def __init__(self, session):
    self.session = session
    self.identity = mesh.ensure_identity(self.node_id)   # NEW
    self.did = str(self.identity.did)                    # NEW
```

### 4c. Add `handshake()` to the base agent

Emit three `A2A` events (challenge → signed response → verified/failed) and
return `True` on success. (Copy the reference `Agent.handshake()` shape.)

### 4d. Gate delegation — `agents/coordinator.py`

```python
fin_ok, res_ok = await asyncio.gather(
    self.handshake(self.fin_agent),
    self.handshake(self.res_agent),
)
if not (fin_ok and res_ok):
    self._status("IDLE")
    return {"status": "blocked", "reason": "mesh handshake failed"}
```

**Test:** `python smoke_test.py` — **update it first**: vanilla asserts exactly
4 A2A events; with the handshake you now expect **more** (3 per peer). Loosen
that assertion to `>= 4` (already the case) or add explicit handshake-phase
checks. **Expected:** dashboard shows the handshake sequence; AGT trust graph
gets edges.

---

## Step 5 — Publish handshakes to the control plane

**Goal:** the AGT trust graph shows real DIDs on both sides.

In `mesh.py`, after a handshake, fetch the SDK's mesh publisher and post:

```python
def _sdk_publisher():
    try:
        from agt_sdk import AutoKernel
        k = AutoKernel.instance()
        return getattr(k, "cp_handshake_publisher", None)
    except Exception:
        return None

# after initiate():
pub = _sdk_publisher()
if pub is not None:
    try:
        await pub.publish_handshake(initiator_did=..., transport="a2a", result=result)
    except Exception as e:
        log.debug("CP publish failed (fail-open): %s", e)
```

For **distributed** peers (a `RemoteAgent`), you can't run the crypto locally
(you don't hold the peer's private key). Instead: have the worker expose
`GET /whoami` returning its real `did:mesh`, have `RemoteAgent.__init__` fetch
it, and record an **attestation** (`publish_handshake` with the peer's real DID)
rather than a full challenge/response. This is the `attest` vs `iatp` split in
the reference `base.Agent.handshake()`.

**Test:** run a distributed mission; **Expected:** trust-graph edges between the
three real agent DIDs.

---

## Migration checklist

| Step | Adds | Files touched | Self-test after |
|---|---|---|---|
| 0 | DB/schema, org+user seed, SDK, `.env` | Postgres, CP `.env`, app `.env` | CP starts; smoke still PASS |
| 0.5 | one-time agent registration | `register_once.py`, `.env` | `REGISTERED ✓`; DID in Agent Registry |
| 1 | tool authorization | `governance.py` (new), `mcp/base.py` | smoke PASS; decisions in CP |
| 2 | method governance | `governance.py`, `financial_agent.py`, `research_agent.py` | smoke PASS; deny → blocked finding |
| 3 | per-agent identities + startup register | `governance.py`, `server.py`, `agent_service.py`, `.env` | 3 agents in registry |
| 4 | mesh id + handshake | `agents/mesh.py` (new), `agents/base.py`, `agents/coordinator.py` | handshake events; update smoke A2A assert |
| 5 | CP publishing | `agents/mesh.py`, `agent_service.py`, `orchestrator.py` | trust-graph edges |

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `401 Unauthorized` on `/policies/bundle/resolve` at startup | No / stale / already-consumed credential; SDK fails open and continues | Do Step 0.5: register once, then run on `AGT_CP_TOKEN` + `AGT_AGENT_ID` (remove `AGT_BOOTSTRAP_TOKEN`) |
| `BootstrapAlreadyConsumed` | Re-consuming a single-use token (bootstrap token left set across runs) | Mint a fresh token (0.5a) **or** switch to the long-lived `AGT_CP_TOKEN` (0.5c) |
| `no schema has been selected to create in` (CP won't start) | `agents_gov` schema missing on the DB the CP connects to | Step 0a — create the schema on the **right** instance/port |
| CP connects but schema still "missing" | Created the schema on `:5433` but CP uses `:5432` (or vice-versa) | Verify with `SELECT inet_server_port()`; create on the CP's instance |
| Login `401`/`404` when minting the token | Wrong route for your build | Open `http://localhost:20355/docs` and confirm the exact paths |
| All agents show as "Coordinator" in the Registry | One process registered multiple roles | Register **only** the process's own role (Step 3 pitfall) |
| smoke test's "exactly 4 A2A" check fails after Step 4 | Handshake now emits extra A2A events | Expected — loosen the assertion to `>= 4` |

## Tips

- **Do steps in order.** Each is independently runnable and reversible.
- **Keep `AGT_FAIL_OPEN=true`** until you're done, then flip to `false` to make
  the demo strict.
- **Diff against the reference build** (`agentic-mcp-ops`) whenever a shape is
  unclear — the vanilla files were derived from it by *removing* exactly these
  hooks, so the reverse is a clean re-insertion.
- After Step 4, the vanilla `smoke_test.py`'s "exactly 4 A2A" style checks may
  need loosening — that's expected and is itself a good signal the handshake is
  now firing.
