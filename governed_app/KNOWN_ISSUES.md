# Known Issues

Real defects found while integrating this app against the AGT SDK
(`agt-sdk 0.1.1`) and control plane (staging-main, 2026-07-31 build). Each
entry names the exact file / line and the concrete effect on this app so you
don't waste time re-diagnosing.

---

## 1. `@peer_verified(min_trust=…)` always sees peer trust score = 0

**Symptom.** `AGT_A2A_MIN_TRUST=500` (or any positive value on the decorator)
refuses every delegation with `A2A refused for peer 'X': trust 0 < required N`
even after the peer has accumulated a real reputation on the CP (visible in
`GET /trust/leaderboard`, `GET /trust/matrix`, and `GET /agents/{did}` — all
show `score: 699` in our reproducer).

**Root cause — CP/SDK schema mismatch.** The CP's list endpoint
`GET /api/v1/orgs/{org}/agents?name=<peer>` returns the score as a **nested
object**:

```json
{
  "name": "Financial Agent",
  "trust": {
    "score": 699,
    "tier": "standard",
    "dimensions": { ... }
  },
  ...
}
```

`agt_sdk/_a2a.py::resolve_peer` (line 139 in v0.1.1) reads the OLD flat field:

```python
"trust_score": int(row.get("trust_score") or 0)
```

`row.get("trust_score")` is always `None` on the new CP shape, so the SDK
converts it to `0`. `run_gate` (line 205 of the same file) then compares
`0 >= bar` and refuses.

**Confirmation.** The very same row also carries a `trust` (dict) key with
`score: 699`. The single-record endpoint `GET /agents/{did}` returns the
identical shape. Every trust-facing endpoint on the CP agrees the score is
699; only the SDK's reader hasn't been updated.

**Files involved.**
- CP list response (nested `trust` object): see any `GET /agents?name=X`
- SDK reader (flat `trust_score` field): `.venv/Lib/site-packages/agt_sdk/_a2a.py:139`

**Workarounds ranked.**

1. **Set `AGT_A2A_MIN_TRUST=0`** (this repo's default). Documented as
   "attested mode: identity is still fail-closed, no trust bar applied." The
   decorators still refuse unknown / inactive / superseded peers. Reputation
   is not enforced but is still published and visible on the CP.
2. **Monkey-patch `resolve_peer` at startup** to read
   `row.get("trust", {}).get("score", 0)`. Legitimate compat shim (same
   category as `agt_sdk._kernel_compat`), but ugly.
3. **Wait for the SDK fix.** The change is one line — `resolve_peer` should
   prefer the nested field and fall back to the flat one for backwards compat.

**Reproducer.** With this repo:
1. Run any clean mission with `AGT_A2A_MIN_TRUST=0`.
2. Watch the leaderboard: `GET /api/v1/orgs/demodevelop/trust/leaderboard`
   returns score 699 for both `Financial Agent` and `Research Agent`.
3. Set `AGT_A2A_MIN_TRUST=500`, restart.
4. Run a mission. Every delegation is refused with
   `trust 0 < required 500`.
5. `GET /api/v1/orgs/demodevelop/agents?name=Financial+Agent` — the row's
   `trust.score` is 699, `trust_score` is absent.

---

## 2. Reward-engine background loop is not started by the SDK

**Symptom.** With `AGT_A2A_MIN_TRUST=0` (missions actually run), the CP shows
`trust_score: 0` in the trust-handshake rows forever, even after dozens of
allowed tool calls. The SDK never publishes a score update.

**Root cause.** In `agentmesh/reward/engine.py`:

- `record_signal` (line 158) recalculates the score only when a signal's
  value is `< 0.3` — i.e. only bad signals (denies) trigger recalc.
- `_recalculate_score` (line 313) fires the `on_score_change` callbacks —
  which is what `agt_sdk._lifecycle._maybe_wire_mesh` wired to
  `CPTrustPublisher.publish_score`.
- `start_background_updates` (line 416) *would* recalculate periodically for
  every known agent (every `REWARD_UPDATE_INTERVAL_SECONDS` = 30 s).
- **Nothing in `agt_sdk` ever calls `start_background_updates()`.** grep the
  SDK: no hits. The drift publisher's sampler loop is scheduled explicitly
  by `_lifecycle._maybe_wire_mesh` (~line 540). The reward engine's isn't.

Net result: signals accumulate locally, the local `total_score` on the worker
reaches 699, but the CP is never told.

**Workaround (this repo).** `governed_ops/worker_app.py` starts the loop
itself at FastAPI startup:

```python
@app.on_event("startup")
async def _start_reward_pump():
    from agt_sdk import AutoKernel
    engines = AutoKernel.instance().mesh_engines
    eng = getattr(engines, "reward_engine", None) if engines else None
    if eng is None:
        return
    asyncio.create_task(eng.start_background_updates())
```

Removes cleanly the moment the SDK wires this loop itself (mirror of what it
already does for the drift publisher).

---

## 3. Agent `PATCH /agents/{did}` silently drops `trust_score`

**Symptom.** `PATCH /api/v1/orgs/{org}/agents/{did}` with body
`{"trust_score": 699}` returns HTTP 200 with the agent record, but the
`trust_score` field on the returned record is `None` (or the nested
`trust.score` is unchanged).

**Cause.** Presumably deliberate — the CP owns the trust score, and it comes
in via `POST /trust/update` (which does write to the leaderboard/history but,
per issue 1, not to the field the SDK reads). But the OpenAPI spec advertises
`trust_score` on the `AgentPatch` schema:

```json
"trust_score": {"anyOf": [{"type": "integer"}, {"type": "null"}]}
```

If PATCH can't set it, either the field should be removed from `AgentPatch`
or the endpoint should return HTTP 400 when it's supplied.

**Impact on this app.** None directly — noted so nobody tries to seed via
PATCH thinking it's the fix for issue 1.

---

## 4. `governance.py`'s legacy `@governed`+`_a(**kw)` wrapper mangles params

Not present in this app — it *was* present in the sibling `agentic_ops/`
vanilla app and was fixed there. Kept for posterity because a naive
integration is likely to repeat it.

**The trap.** If you wrap the SDK's `@governed` around a variadic function

```python
@governed(action="fin_mcp.get_quote")
def _authorize(**kwargs):     # <- catches everything under `kwargs`
    return kwargs
```

then `inspect.signature(fn).bind_partial(**caller_kwargs)` folds every kwarg
into a single `kwargs` argument. The kernel receives
`params = {"kwargs": {"entity": "SBI"}}` and rules like
`field: entity` never see the entity — it's one level too deep.

**Fix.** Call `AutoKernel.instance().check(action, params_dict)` directly
with the caller's flat kwargs; skip the variadic wrapper. The clean app does
this via the two decorators applied directly to the tool functions, so the
kernel receives `params = {"entity": "SBI"}` unmangled.

---

## Reporting these upstream

The three real defects (1, 2, 3) live in different repos:

- Issue 1 — `agt-sdk` package: `agt_sdk/_a2a.py::resolve_peer`, ~line 139.
  Reader needs to prefer nested `row["trust"]["score"]` with a fallback to
  the flat field.
- Issue 2 — `agt-sdk` package: `agt_sdk/_lifecycle.py::_maybe_wire_mesh`.
  Add one `_schedule(reward_engine.start_background_updates())` call next to
  the existing drift-publisher schedule.
- Issue 3 — CP / agents-registry service. Either accept `PATCH.trust_score`
  or drop it from `AgentPatch`.
