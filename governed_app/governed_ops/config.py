"""Environment plumbing — the ONLY module that touches ``os.environ``.

Runs at import time (see ``governed_ops/__init__.py``), before any module that
uses ``@governed`` / ``@peer_verified``. Three jobs:

1. Load ``.env`` next to the app root (with ``setdefault`` semantics — process
   env wins over the file, standard dotenv precedence).
2. Map ``AGENT_ROLE`` → the durable ``AGT_AGENT_TOKEN`` + registered
   ``AGT_AGENT_NAME`` the SDK's IMPL-067 identity-attach flow expects.
3. Expose per-role ports and the coordinator's view of worker URLs.

The agent code never reads env directly. Change the layout here, not there.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("governed_ops.config")

# ── .env loading ────────────────────────────────────────────────────────────

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def _load_dotenv() -> None:
    if not _ENV_PATH.exists():
        return
    for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if val and val[0] not in "\"'" and " #" in val:
            val = val.split(" #", 1)[0].strip()
        val = val.strip('"').strip("'")
        # setdefault so process env wins over .env — critical for tests and
        # for launchers that inherit AGENT_ROLE via subprocess env.
        os.environ.setdefault(key, val)


_load_dotenv()


# ── AGENT_ROLE → SDK env mapping ────────────────────────────────────────────
#
# Distributed topology: three processes, three roles, three DIDs.
#   coordinator  ← users hit this; delegates over HTTP with @peer_verified
#   fin_agent    ← worker, hosts FIN MCP tools with @governed
#   res_agent    ← worker, hosts NEWS MCP tools with @governed
#
# Each process reads AGENT_ROLE, picks its own credential + registered name,
# and hands those to the SDK via AGT_AGENT_TOKEN + AGT_AGENT_NAME. Escrow is
# keyed on (org, name), so name MUST match the name typed at Register Agent.

_ROLE_SPECS: dict[str, tuple[str, str, str, int]] = {
    # role_key: (token_env_var, name_env_var, default_registered_name, default_port)
    # Setting the name_env_var in .env overrides the default — handy when
    # re-registering agents under new names during testing.
    "coordinator": ("AGT_COORDINATOR_TOKEN", "AGT_COORDINATOR_NAME", "Coordinator Agent_2026-08-12", 8100),
    "fin_agent":   ("AGT_FIN_AGENT_TOKEN",   "AGT_FIN_AGENT_NAME",   "Financial Agent_2026-08-12",  8101),
    "res_agent":   ("AGT_RES_AGENT_TOKEN",   "AGT_RES_AGENT_NAME",   "Research Agent_2026-08-12",   8102),
}

ROLE = (os.environ.get("AGENT_ROLE") or "coordinator").strip().lower()
_tok_env, _name_env, _default_name, _default_port = _ROLE_SPECS.get(ROLE, _ROLE_SPECS["coordinator"])
AGENT_NAME = (os.environ.get(_name_env) or "").strip() or _default_name
_token = (os.environ.get(_tok_env) or "").strip()
_passphrase = (os.environ.get("AGT_AGENT_PASSPHRASE") or "").strip()

# The name is fed to the SDK via ``AGT_AGENT_NAME``. Always overwrite — a
# stale value from a previous role would break escrow-key derivation.
os.environ["AGT_AGENT_NAME"] = AGENT_NAME

if _token:
    os.environ["AGT_AGENT_TOKEN"] = _token
    if not _passphrase:
        log.error(
            "%s: %s is set but AGT_AGENT_PASSPHRASE is not — identity attach "
            "will refuse to run. Set the org passphrase.", ROLE, _tok_env,
        )
    elif len(_passphrase) < 32:
        log.error(
            "%s: AGT_AGENT_PASSPHRASE is shorter than 32 chars — the SDK "
            "will refuse it. Regenerate a >=32 char passphrase.", ROLE,
        )
else:
    log.warning(
        "%s: no credential set (%s empty). SDK falls back to env-derived "
        "identity — no attach, no escrow, no stable DID across restart.",
        ROLE, _tok_env,
    )


# ── App knobs (nothing SDK-adjacent) ────────────────────────────────────────

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT") or _default_port)

# Per-role ports the launcher uses. Overridable individually.
COORD_PORT = int(os.environ.get("COORD_PORT", "8100"))
FIN_PORT   = int(os.environ.get("FIN_PORT",   "8101"))
RES_PORT   = int(os.environ.get("RES_PORT",   "8102"))

# Coordinator's view of the workers. Only meaningful in the coordinator
# process; workers set them but do not use them.
FIN_AGENT_URL = os.environ.get("FIN_AGENT_URL") or f"http://{HOST}:{FIN_PORT}"
RES_AGENT_URL = os.environ.get("RES_AGENT_URL") or f"http://{HOST}:{RES_PORT}"

GOVERNANCE_ON = os.environ.get("AGT_GOVERNANCE_ENABLED", "true").lower() not in (
    "0", "false", "no",
)
