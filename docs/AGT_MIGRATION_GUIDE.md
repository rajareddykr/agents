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

You need a running AGT control plane and the SDK wheels.

1. **Control plane up** (local, e.g. `http://localhost:20355`, org `demodevelop`).
2. **Install the SDK** into your venv. The mesh features live behind extras:

   ```bash
   # from your local SDK checkout / wheelhouse
   pip install -e path/to/agent-os-sdk[mesh,bootstrap]
   # (avoid [all] — it pulls framework adapters that pin Python <3.14)
   ```

3. **Add AGT settings to `.env`:**

   ```
   AGT_CP_URL=http://localhost:20355
   AGT_ORG_CODE=demodevelop
   AGT_BOOTSTRAP_TOKEN=agt_boot_...        # a valid token from your CP
   AGT_GOVERNANCE_ENABLED=true
   AGT_FAIL_OPEN=true                      # keep true during migration
   AGT_HEARTBEAT_SECONDS=10
   ```

4. **Extend `config.py`** to surface these (optional but tidy) — or just read
   them from `os.environ` inside the governance module in Step 1.

**Checkpoint:** `python smoke_test.py` still passes (you haven't wired anything
yet).

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

Add a one-time `agent_online` authorize in each process's FastAPI `startup`
(`server.py` and `agent_service.py`), so the process registers in the mesh.

### 3c. `.env` — one token per agent

```
AGT_COORDINATOR_TOKEN=agt_boot_...
AGT_FIN_AGENT_TOKEN=agt_boot_...
AGT_RES_AGENT_TOKEN=agt_boot_...
```

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
| 0 | SDK + `.env` | `.env`, (`config.py`) | smoke still PASS |
| 1 | tool authorization | `governance.py` (new), `mcp/base.py` | smoke PASS; decisions in CP |
| 2 | method governance | `governance.py`, `financial_agent.py`, `research_agent.py` | smoke PASS; deny → blocked finding |
| 3 | per-agent identities | `governance.py`, `server.py`, `agent_service.py`, `.env` | 3 agents in registry |
| 4 | mesh id + handshake | `agents/mesh.py` (new), `agents/base.py`, `agents/coordinator.py` | handshake events; update smoke A2A assert |
| 5 | CP publishing | `agents/mesh.py`, `agent_service.py`, `orchestrator.py` | trust-graph edges |

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
