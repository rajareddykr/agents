"""AGT governance wiring for the vanilla stack.

Bridges ``AGENT_ROLE`` (coordinator / fin_agent / res_agent) to the SDK's
IMPL-067 register-once + key-escrow flow. Each role must be registered ONCE in
the CP UI ("Register Agent") to receive a durable ``agt_...`` credential; that
credential goes in the role-specific env var below. Combined with the org-wide
``AGT_AGENT_PASSPHRASE`` (min 32 chars), the SDK recovers the same private key
— and therefore the same ``did:mesh:...`` DID — from escrow on every restart.

Env vars read here:

* ``AGENT_ROLE`` — which spec row applies to this process.
* ``AGT_COORDINATOR_TOKEN`` / ``AGT_FIN_AGENT_TOKEN`` / ``AGT_RES_AGENT_TOKEN``
  — the durable agent credential for that role. Displayed once by
  Register Agent.
* ``AGT_AGENT_PASSPHRASE`` — organisation-wide, host-local, ≥32 chars. Never
  transmitted; encrypts the escrowed private key.
* ``AGT_GOVERNANCE_ENABLED`` / ``AGT_FAIL_OPEN`` — knobs.

Legacy path: a token that still looks like a single-use bootstrap token
(``agt_boot_...``) falls back to the deprecated bootstrap exchange, so migration
is not a hard cut-over. Every restart on that path mints a NEW DID — the very
thing IMPL-067 exists to stop — so a warning is emitted.
"""
from __future__ import annotations

import asyncio
import functools
import inspect
import logging
import os
import time

log = logging.getLogger("agentic_ops.governance")

_ENABLED = os.environ.get("AGT_GOVERNANCE_ENABLED", "true").lower() not in ("0", "false", "no")
FAIL_OPEN = os.environ.get("AGT_FAIL_OPEN", "true").lower() not in ("0", "false", "no")

_ROLE = (os.environ.get("AGENT_ROLE") or "coordinator").strip()

# Per role: (env var holding this role's DURABLE credential, registered display
# name, and a stable agent_id label). The name MUST match the name the operator
# used at Register Agent — escrow is keyed on (org, name), so a mismatch fails
# to decrypt.
_SPECS = {
    "coordinator": ("AGT_COORDINATOR_TOKEN", "Coordinator Agent", "agentic-coordinator"),
    "fin_agent":   ("AGT_FIN_AGENT_TOKEN",   "Financial Agent",   "agentic-fin-agent"),
    "res_agent":   ("AGT_RES_AGENT_TOKEN",   "Research Agent",    "agentic-res-agent"),
}
_tok_env, _name, _id = _SPECS.get(_ROLE, _SPECS["coordinator"])
_token = (os.environ.get(_tok_env) or "").strip()
_passphrase = (os.environ.get("AGT_AGENT_PASSPHRASE") or "").strip()

# The registered agent name — used by the SDK as half of the escrow-key handle
# ``(org, name)``. Overrides anything already in the environment so a stale
# value cannot silently decrypt-nothing on restart.
os.environ["AGT_AGENT_NAME"] = _name
# Placeholder ``agent_id`` for the pre-attach window. The SDK replaces it with
# the recovered DID once identity attach succeeds, so this only appears in
# early logs / heartbeats before attach lands.
os.environ.setdefault("AGT_AGENT_ID", _id)


def _looks_like_bootstrap(tok: str) -> bool:
    """Very light heuristic — a legacy single-use token, not a durable one.

    Bootstrap tokens carry the ``agt_boot_`` prefix; durable IMPL-067 credentials
    do not. We branch on it so a mid-migration deployment with an old token in
    the env still works instead of failing at attach with an unhelpful 401.
    """
    return tok.startswith("agt_boot_")


if _token and not _looks_like_bootstrap(_token):
    # IMPL-067 — durable credential + passphrase. On first start the SDK
    # generates an Ed25519 keypair, attaches the public half, and escrows the
    # private half (encrypted with the passphrase). On every restart it fetches
    # the ciphertext, decrypts it, and attaches the SAME DID with no new epoch.
    os.environ["AGT_AGENT_TOKEN"] = _token
    # Any leftover bootstrap token from a previous env would still take
    # precedence in _lifecycle if left set — the SDK deliberately honours it
    # while explicitly configured. Clear it so this process runs the new flow.
    os.environ.pop("AGT_BOOTSTRAP_TOKEN", None)

    if not _passphrase:
        log.error(
            "%s: %s is set but AGT_AGENT_PASSPHRASE is not — the SDK will "
            "refuse to attach identity without both, so this process cannot "
            "recover its escrowed key. Set the org-wide passphrase.",
            _ROLE, _tok_env,
        )
    elif len(_passphrase) < 32:
        log.error(
            "%s: AGT_AGENT_PASSPHRASE is shorter than 32 chars — the SDK will "
            "refuse it at construction (HKDF stretches, it does not create "
            "entropy). Regenerate a ≥32 char passphrase.", _ROLE,
        )
    else:
        log.info(
            "%s: IMPL-067 identity attach configured (durable credential from "
            "%s + org passphrase). Same DID will survive restart.",
            _ROLE, _tok_env,
        )
elif _token and _looks_like_bootstrap(_token):
    # DEPRECATED path — single-use bootstrap. Every restart consumes a fresh
    # token and mints a new DID (trust score reset, drift history orphaned).
    # Kept working so operators can migrate one role at a time.
    os.environ["AGT_BOOTSTRAP_TOKEN"] = _token
    os.environ.pop("AGT_AGENT_TOKEN", None)
    log.warning(
        "%s: %s looks like a single-use bootstrap token (agt_boot_...). This "
        "is the DEPRECATED path — every restart mints a NEW DID. Register "
        "this agent once in the UI, then set %s to the durable credential "
        "and AGT_AGENT_PASSPHRASE to enable restart-stable identity.",
        _ROLE, _tok_env, _tok_env,
    )
else:
    # No token at all — the SDK will run in pass-through / env-derived-identity
    # mode. Fine for the fully-offline demo (smoke_test.py); a real deployment
    # should register the agent and set the token.
    log.warning(
        "%s: no credential set (%s empty). The SDK will fall back to an "
        "env-derived agent_id — no identity attach, no key escrow, no stable "
        "DID across restarts. Set %s for governed operation.",
        _ROLE, _tok_env, _tok_env,
    )


try:
    if not _ENABLED:
        raise ImportError("governance disabled")
    from agt_sdk import AutoKernel as _AutoKernel
    from agt_sdk.exceptions import AGTBlocked
    AVAILABLE = True
except Exception as e:
    _AutoKernel = None
    AVAILABLE = False
    class AGTBlocked(Exception):
        ...
    log.warning("agt_sdk unavailable (%s) — running ungoverned", e)


def enabled() -> bool:
    return AVAILABLE


def _run_check(action: str, tool_args: dict):
    """Call ``AutoKernel.check`` from either sync or in-loop sync context.

    The SDK's ``@governed`` decorator wraps kwargs under the function's
    signature — for a ``def _a(**kw)`` bindable target that means the CP-side
    rule engine sees ``ctx["params"] = {"kw": {"entity": "SBI"}}``, which the
    ``CONTAINS`` operator (``target in dict``) treats as a KEY check. Calling
    ``kernel.check`` directly with a flat dict makes ``ctx["params"]`` the
    caller's kwargs verbatim and populates each kwarg at the top level via the
    kernel's own flatten, so rules like ``field: entity, operator: contains``
    behave the way the YAML reads.
    """
    import asyncio
    import concurrent.futures

    kernel = _AutoKernel.instance()
    coro_fn = lambda: kernel.check(action, tool_args)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_fn())
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro_fn()).result()


def authorize(action: str, node_id: str | None = None, **context) -> None:
    """Raises AGTBlocked on deny. Fails open on transport errors.

    ``context`` becomes the ``params`` dict the kernel evaluates. Each kwarg is
    passed as-is; the kernel flattens them into the top level of the policy
    context, so rules can address individual kwargs by name
    (``field: entity``) instead of only via the compound ``field: params``.
    """
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


def _blocked_result(self, e) -> dict:
    return {"agent": getattr(self, "label", "Agent"),
            "verdict": "blocked", "sentiment": "blocked",
            "blocked": True, "reason": str(e),
            "summary": f"blocked by governance ({e})"}


def _call_params(fn, self, a, k) -> dict:
    """Reconstruct the tool's call args as a params dict (drops ``self``)."""
    try:
        bound = inspect.signature(fn).bind(self, *a, **k)
        bound.apply_defaults()
        return {n: v for n, v in bound.arguments.items() if n != "self"}
    except Exception:
        return dict(k)


async def _await_grant(action: str, params: dict, *, timeout_s: float = 900.0) -> bool:
    """Block until a human grants the parked approval, else ``False``.

    R4.1 block-and-resume. Only waits for a ``require_approval`` HOLD — a hard
    ``deny`` returns ``False`` immediately, so the agent never hangs on
    something no human will approve. Requires ``AGT_HITL_APPROVALS=true`` (that
    is what gives the kernel its ``_approvals`` client).
    """
    if not AVAILABLE:
        return False
    kernel = _AutoKernel.instance()
    approvals = getattr(kernel, "_approvals", None)
    if approvals is None:
        return False  # HITL not enabled → cannot wait
    try:
        from agt_sdk._approvals import detect_require_approval
        if not detect_require_approval(kernel._kernel, action, params):
            return False  # a real deny, not an approval hold
    except Exception:
        return False
    await approvals.park(action, reason=f"{action}: awaiting human approval")
    log.info("%s: parked — waiting up to %ds for a human grant", action, int(timeout_s))
    interval = float(os.environ.get("AGT_APPROVAL_POLL_SECONDS", "10"))
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        await approvals.poll_once()
        if approvals.grant_active(action):
            log.info("%s: approval GRANTED — resuming", action)
            return True
        await asyncio.sleep(interval)
    log.warning("%s: not granted within %ds — staying blocked", action, int(timeout_s))
    return False


def governed(action: str):
    def deco(fn):
        is_async = inspect.iscoroutinefunction(fn)

        @functools.wraps(fn)
        async def awrapper(self, *a, **k):
            node = getattr(self, "node_id", None)
            try:
                await asyncio.to_thread(authorize, action, node)
            except AGTBlocked as e:
                # R4.1 — a require_approval HOLD parks a request; wait for the
                # human grant, then resume. A hard deny (or HITL off) stays
                # blocked immediately.
                params = _call_params(fn, self, a, k)
                if await _await_grant(action, params):
                    try:
                        # re-check WITH the grant — `approved=True` satisfies
                        # the require_approval rule.
                        await asyncio.to_thread(authorize, action, node, approved=True)
                    except AGTBlocked as e2:
                        return _blocked_result(self, e2)
                    return await fn(self, *a, **k)   # resume the real work
                return _blocked_result(self, e)
            return await fn(self, *a, **k)
        return awrapper if is_async else fn
    return deco
