"""Runtime config. Reads a .env file (if present) then environment variables.

Set FINNHUB_API_KEY to switch FIN MCP / NEWS MCP to live data. If no key is
found, the system runs on deterministic mock data (fully offline).

NOTE (vanilla build): there are NO AGT / mesh / bootstrap settings here. The
only knobs are the Finnhub key and the distributed-mode worker URLs. When you
follow the migration guide to add agt-sdk, this is one of the files you extend
(with AGT_CP_URL, AGT_ORG_CODE, per-agent tokens, etc.).
"""
from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        # strip an inline " # comment" on unquoted values
        if val and val[0] not in "\"'" and " #" in val:
            val = val.split(" #", 1)[0].strip()
        val = val.strip('"').strip("'")
        if val:
            os.environ[key] = val
        else:
            os.environ.setdefault(key, val)


_load_dotenv()

FINNHUB_API_KEY: str = os.environ.get("FINNHUB_API_KEY", "").strip()

# Live mode is on automatically when a key is present; force off with LIVE=0.
_live_flag = os.environ.get("LIVE", "").strip().lower()
if _live_flag in ("0", "false", "no"):
    LIVE = False
elif _live_flag in ("1", "true", "yes"):
    LIVE = True
else:
    LIVE = bool(FINNHUB_API_KEY)


def data_mode() -> str:
    return "live" if (LIVE and FINNHUB_API_KEY) else "mock"


# --- distributed (process-per-agent) mode --------------------------------
# When both worker URLs are set, the Coordinator dispatches to remote agent
# processes over HTTP. In the vanilla build this is purely an architectural
# choice (run each agent in its own process); it no longer implies a separate
# mesh identity per process — that concept returns with the mesh migration.
FIN_AGENT_URL: str = os.environ.get("FIN_AGENT_URL", "").strip().rstrip("/")
RES_AGENT_URL: str = os.environ.get("RES_AGENT_URL", "").strip().rstrip("/")
DISTRIBUTED: bool = bool(FIN_AGENT_URL and RES_AGENT_URL)


def run_mode() -> str:
    return "distributed" if DISTRIBUTED else "in-process"
