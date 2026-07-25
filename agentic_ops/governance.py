import asyncio, functools, inspect, logging, os
log = logging.getLogger("agentic_ops.governance")

_ENABLED = os.environ.get("AGT_GOVERNANCE_ENABLED", "true").lower() not in ("0","false","no")
FAIL_OPEN = os.environ.get("AGT_FAIL_OPEN", "true").lower() not in ("0","false","no")

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