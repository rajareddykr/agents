# AGT Integration Playbook

A single-file, do-this-in-order guide for putting any agent app under the AGT
platform. Assumes you have a control plane (CP) running and can log into its
admin UI. No prior AGT knowledge required.

---

## 1. The five-minute mental model

**What AGT gives you:** a stable cryptographic identity per agent, a control
plane that publishes policies, and enforcement hooks in your code.

**What you have to give AGT:** three env vars per agent, one register-once
click in the UI, and two decorators sprinkled in your code.

**Three secrets, three jobs:**

| Secret | Env var | Job | Where it lives |
|---|---|---|---|
| Credential | `AGT_AGENT_TOKEN` | Proves "I am this registered agent" to the CP | Your `.env`, per agent |
| Passphrase | `AGT_AGENT_PASSPHRASE` | Encrypts the private key at rest, decrypts it at restart | Your `.env`, org-wide, ≥32 chars, **never sent to CP** |
| Private key | *(no env var)* | Signs handshakes, defines the DID | Agent process memory only |

**One equation:** `DID = "did:mesh:" + sha256(public_key)[:24]`.
Same key → same DID. Recovering the key on restart is what makes the identity
stable — nothing else.

---

## 2. The two decorators (the whole user-facing SDK surface)

```python
from agt_sdk import governed, peer_verified
```

| Decorator | Answers | Raises | Use on |
|---|---|---|---|
| `@governed(action="...")` | "Should this action be allowed?" | `AGTBlocked` | Tool calls, MCP handlers, sensitive functions |
| `@peer_verified("peer-name", min_trust=600)` | "Should I trust this peer to delegate to?" | `AGTPeerUntrusted` | Agent-to-agent calls |

Stack them when both matter:

```python
@peer_verified("Finance-Agent", min_trust=600)   # first: trust
@governed(action="request_invoice")              # then:  policy
async def request_invoice(invoice_id: str): ...
```

Everything else (`AutoKernel`, `A2AVerdict`, framework wrappers like
`GovernedToolNode` / `GovernanceMiddleware` / `Governed` / `governed_agent` /
`governed_tool`) is machinery that ultimately calls the same two checks.

---

## 3. One-time setup per agent

### 3.1 In the CP UI

1. **Register Agent** → give it a display name (e.g. `Financial Agent`).
2. Copy the shown `agt_...` credential. **It is shown once. Never retrievable.**
3. If you need multiple agents, repeat. Names must be unique within the org.

### 3.2 On the host

Set these env vars (put them in `.env` for local dev):

```ini
AGT_CP_URL=http://<cp-host>:<port>
AGT_ORG_CODE=<your-org-slug>
AGT_AGENT_PASSPHRASE=<≥32-char org-wide secret>   # NEVER sent to CP
AGT_AGENT_TOKEN=agt_...                            # per-agent, from step 3.1
AGT_AGENT_NAME=Financial Agent                     # MUST match registered name
```

The passphrase is one value per organisation, shared across all agents on the
host. Losing it means every agent on that host mints a new DID on next restart.

### 3.3 First start, then every restart

You do nothing. The SDK handles both:

```
first start    → generate keypair → attach public half → escrow private half
every restart  → fetch ciphertext → decrypt → same DID, no new epoch
```

Verify with `GET /whoami` on the agent (if exposed) or `AutoKernel.instance().mesh_engines.identity.did` in code.

---

## 4. Wiring an existing agent app

Six edits, in this order:

### 4.1 Add a `governance.py` bridge module

```python
# your_app/governance.py
import asyncio
import concurrent.futures
import logging
import os

log = logging.getLogger("your_app.governance")

_ENABLED = os.environ.get("AGT_GOVERNANCE_ENABLED", "true").lower() not in ("0", "false", "no")
FAIL_OPEN = os.environ.get("AGT_FAIL_OPEN", "true").lower() not in ("0", "false", "no")

try:
    if not _ENABLED:
        raise ImportError("governance disabled")
    from agt_sdk import AutoKernel
    from agt_sdk.exceptions import AGTBlocked
    AVAILABLE = True
except Exception as e:
    AutoKernel = None
    AVAILABLE = False
    class AGTBlocked(Exception): ...
    log.warning("agt_sdk unavailable (%s) — running ungoverned", e)


def _run_check(action: str, tool_args: dict):
    """Call kernel.check from either sync or in-loop sync context."""
    kernel = AutoKernel.instance()
    coro = lambda: kernel.check(action, tool_args)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro())
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro()).result()


def authorize(action: str, node_id: str | None = None, **context) -> None:
    """Raises AGTBlocked on deny. Fails open on transport errors."""
    if not AVAILABLE:
        return
    try:
        result = _run_check(action, dict(context))
    except AGTBlocked:
        raise
    except Exception as e:
        if FAIL_OPEN:
            log.warning("govern check errored for %s (%s) — failing open", action, e)
            return
        raise
    if not result.allowed:
        raise AGTBlocked(
            reason=result.reason or f"Blocked by AGT policy: {action}",
            action=action,
            matched_rule=result.matched_rule,
            policy_name=result.policy_name,
        )
```

**Why not use `@governed` directly?** Because it binds args to the function
signature, and a `**kw`-catch-all target ends up putting your kwargs under a
`"kw"` key. Calling `kernel.check(action, flat_dict)` directly makes rules
like `field: entity, operator: contains` work as YAML reads.

### 4.2 Call it at your tool-call seam

Somewhere between "user asked the agent to do X" and "code actually does X":

```python
from your_app import governance
from your_app.governance import AGTBlocked

def _precheck(action: str, caller: str, kwargs: dict):
    try:
        governance.authorize(action, caller, **{
            k: v for k, v in kwargs.items()
            if isinstance(v, (str, int, float, bool))
        })
        return None
    except AGTBlocked as e:
        return {"blocked": True, "reason": str(e),
                "rule": e.matched_rule, "source": "cp-policy"}
```

**Rule of thumb:** filter to primitives. Nested objects don't help the policy
engine and just noise the audit logs.

### 4.3 Warm the SDK at process startup

Force the identity attach to run *before* the first request lands (otherwise
`/whoami` races and returns `did=None` for a few seconds):

```python
# FastAPI
@app.on_event("startup")
async def _warm():
    def _run():
        try:
            from agt_sdk import AutoKernel
            AutoKernel.instance()   # bootstrap → attach → mesh wiring
        except Exception:
            pass
        governance.authorize("agent_online", "coordinator")   # optional
    await asyncio.to_thread(_run)
```

### 4.4 Env var precedence — use `setdefault` when loading `.env`

Process env should win over `.env`. If your loader unconditionally overwrites,
your tests will silently ignore any env override:

```python
# WRONG — .env clobbers the process env
os.environ[key] = val

# RIGHT — process env wins
os.environ.setdefault(key, val)
```

### 4.5 (Optional) A2A gates for delegation

If your agents call each other:

```python
from agt_sdk import peer_verified

class Coordinator:
    @peer_verified("Financial-Agent", min_trust=600)
    async def _delegate_to_finance(self, request): ...
```

The peer's public key is looked up in the CP registry, its trust score is
compared to `min_trust`, and the call is allowed / refused before your body
runs. Raises `AGTPeerUntrusted` on refusal — catch it or let it propagate.

### 4.6 (Distributed only) Expose `/whoami`

If each agent runs in its own process and calls others over HTTP, publish the
agent's real DID so peers can attest:

```python
@app.get("/whoami")
def whoami():
    from agt_sdk import AutoKernel
    ident = AutoKernel.instance().mesh_engines.identity
    return {"did": str(ident.did), "name": ident.name,
            "capabilities": list(ident.capabilities or [])}
```

---

## 5. Writing a policy (YAML) that actually enforces

**Two enforcement paths run in parallel:** the modern rule engine
(`rules[]`) and the legacy list model (`defaults.blocked_*`). Use both.

### 5.1 Priority — DENY must beat ALLOW

The rule engine sorts by `priority` **DESCENDING**, and the **first match wins**.
So this fails silently:

```yaml
# WRONG — allow fires first, deny never runs
- {name: allow-reads,   condition: {field: action, operator: in, value: [read, list]}, action: allow, priority: 100}
- {name: block-secrets, condition: {field: params, operator: matches, value: "(?i)secret"}, action: deny, priority: 50}
```

Fix: bump denies to a higher priority.

```yaml
# RIGHT
- {name: block-secrets, ..., action: deny,  priority: 300}
- {name: allow-reads,   ..., action: allow, priority: 100}
```

### 5.2 `contains` vs `matches` — pick the right operator

The evaluator does `target in ctx_value`. The value at `field: params` is a
**dict** — so `"secret" in {"query": "the secret"}` is a *key* check and
returns `False`. Two ways to fix:

| Want | Do this |
|---|---|
| Substring search anywhere in the params dict | `operator: matches`, `value: "(?i)pattern"` — regex against `str(dict)` |
| Substring in one specific kwarg | `field: <kwarg_name>` (e.g. `entity`), `operator: contains` |
| Case-insensitive substring anywhere | `defaults.blocked_patterns: [pattern]` — legacy, always case-insensitive |

### 5.3 A minimal working template

```yaml
version: "1.0"
name: my-agent-policy
description: What this policy denies.

rules:
  # DENY rules — priority 300+
  - name: block-restricted-action
    action: deny
    priority: 300
    condition: {field: action, operator: eq, value: dangerous_tool.exec}
    message: "This action is disabled by policy."

  - name: block-sensitive-content
    action: deny
    priority: 300
    condition: {field: params, operator: matches, value: "(?i)ssn|social security|credit.?card"}
    message: "Sensitive content detected in parameters."

  # ALLOW rules — priority 100 (catch-all safe list)
  - name: allow-reads
    action: allow
    priority: 100
    condition: {field: action, operator: in, value: [read_file, list_files, search]}
    message: "Read-only operations are allowed."

defaults:
  action: allow
  blocked_actions:
    - dangerous_tool.exec
  blocked_patterns:
    - ssn
    - credit_card
  require_approval: []
```

### 5.4 Upload and publish

Dashboard → Governance → Policies → **New Policy** → paste → **Save** →
**Publish** → set `status: active` → bind to your agent or org.

Verify via `GET /api/v1/orgs/{org}/policies/bundle` — the SDK fetches from
that endpoint on the `AGT_POLICY_REFRESH_SECONDS` interval (default 60s).

---

## 6. Common failure modes and their fixes

| Symptom | Root cause | Fix |
|---|---|---|
| Agent mints a new DID every restart | Missing `AGT_AGENT_PASSPHRASE`, or the `[mesh]` extra is not installed, or a stale `AGT_BOOTSTRAP_TOKEN` is winning | Set the passphrase, `pip install 'agt-sdk[mesh]'`, remove bootstrap token from env |
| `KeyRecoveryFailed` at startup | Wrong passphrase or `AGT_AGENT_NAME` changed | Restore the exact passphrase and registered name. The SDK is fail-*closed* here on purpose — running as a different agent is worse than not running |
| `403 URL org code does not match the authenticated tenant` | Operator's tenant ≠ URL org | Log in as a user in the target org, or use platform-admin routes to create one |
| Policy uploaded but nothing blocks | Deny priority ≤ allow priority (first-match wins), or `field: params, operator: contains` matched keys not values | Bump denies to priority 300+; switch to `operator: matches` with `(?i)regex` |
| `AGTBlocked` propagates and crashes the request | Your `_precheck` doesn't catch it | Wrap the `authorize()` call in try/except and turn `AGTBlocked` into a graceful "blocked" response |
| Tests set env vars but they don't take effect | Your `.env` loader overwrites process env | Change loader to `os.environ.setdefault(k, v)` |

---

## 7. Self-test the integration

Three checks, cheapest first:

### 7.1 Governance is wired

```python
from your_app import governance
from your_app.governance import AGTBlocked

# Should succeed
governance.authorize("read_file", entity="OK")

# Should raise if you have a deny rule matching "secret"
try:
    governance.authorize("read_file", entity="secret data")
    print("FAIL — governance not enforcing")
except AGTBlocked as e:
    print("OK — blocked:", e)
```

### 7.2 DID is stable across restart

Start the agent, capture the DID, stop, restart, capture again.

```bash
# terminal 1
python -m my_app.worker
curl -s localhost:8080/whoami | jq -r .did
# → did:mesh:410de4157617bd16f732086b

# ctrl-c, then rerun
python -m my_app.worker
curl -s localhost:8080/whoami | jq -r .did
# → did:mesh:410de4157617bd16f732086b   ← must match
```

### 7.3 Policy propagation

Change a policy in the CP → wait one refresh interval → the new rule fires
without restarting the agent.

---

## 8. Migration checklist

Print this. Cross off as you go.

- [ ] CP reachable at `AGT_CP_URL`
- [ ] Org exists; you have a user in it (not the platform-superadmin's default org)
- [ ] Each agent registered in the UI; `agt_...` credential captured
- [ ] `AGT_AGENT_PASSPHRASE` set on the host (≥32 chars)
- [ ] `AGT_AGENT_TOKEN` / `AGT_AGENT_NAME` set per agent, names match registrations
- [ ] `governance.py` bridge module dropped into your app
- [ ] `_precheck` (or equivalent seam) calls `governance.authorize(...)` before each tool call
- [ ] `AutoKernel.instance()` warmed at process startup
- [ ] `.env` loader uses `setdefault` (process env wins)
- [ ] Policy YAML uploaded, denies at priority 300+, allows at 100
- [ ] `contains` replaced with `matches (?i)…` for params content rules
- [ ] `/whoami` exposed (if distributed)
- [ ] Restart the process, confirm the DID is identical to the previous run
- [ ] Trigger a rule-matching request, confirm a BLOCKED event fires with `by: cp-policy`

When every box is ticked, the agent is governed.

---

## 9. Where to go next

- **Trust graph & handshake internals** — how `@peer_verified` looks up peers
  and how the CP scores them.
- **Framework adapters** — `agt_sdk.langgraph` / `.langchain` / `.crewai` /
  `.autogen` / `.openai_agents` if you're not building raw Python.
- **Credential rotation** — `POST /orgs/{org}/agents/{id}/credential/rotate`
  issues a new credential while keeping the DID stable (escrow is keyed on
  `(org, name)`, not the credential).
- **Kill switch** — `POST /identity/{id}/revoke` immediately locks the agent
  out at its next CP call. Different from credential revoke: it also revokes
  the DID so peers refuse it.
