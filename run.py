"""Launch the Agentic AI + MCP Agent Ops stack (VANILLA).

Distributed (default): starts THREE processes so each agent runs in its own
process:
  * Financial Agent worker  -> 127.0.0.1:FIN_PORT   (AGENT_ROLE=fin_agent)
  * Research Agent worker   -> 127.0.0.1:RES_PORT   (AGENT_ROLE=res_agent)
  * Coordinator + dashboard -> 127.0.0.1:PORT       (AGENT_ROLE=coordinator)

Single-process (set MODE=single): runs everything in one process — simplest
for quick local dev and the recommended way to start reading the code.

Env overrides: HOST, PORT (dashboard, default 8000), FIN_PORT (8071),
RES_PORT (8072), MODE (distributed|single).
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if val and val[0] not in "\"'" and " #" in val:
            val = val.split(" #", 1)[0].strip()
        val = val.strip('"').strip("'")
        if val:
            os.environ[key] = val
        else:
            os.environ.setdefault(key, val)


_load_dotenv()

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = os.environ.get("PORT", "8000")
FIN_PORT = os.environ.get("FIN_PORT", "8071")
RES_PORT = os.environ.get("RES_PORT", "8072")
MODE = os.environ.get("MODE", "distributed").strip().lower()


def _uvicorn(app: str, port: str, extra_env: dict) -> subprocess.Popen:
    env = os.environ.copy()
    env.update(extra_env)
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", app, "--host", HOST, "--port", port],
        env=env,
    )


def _post(url: str, body: dict, headers: dict | None = None, timeout: int = 10) -> dict:
    data = json.dumps(body).encode("utf-8")
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, method="POST", headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _mint_bootstrap_tokens(roles: list[str]) -> dict[str, str]:
    """Auto-mint one FRESH single-use bootstrap token per role each launch.

    Bootstrap tokens are single-use, so mesh wiring on restart needs new tokens.
    This logs in as the operator and mints one per process. Fail-open: on any
    error it returns {} and the launcher falls back to whatever's in .env.
    Disable with AGT_AUTO_MINT=0.
    """
    if os.environ.get("AGT_AUTO_MINT", "1").strip().lower() in ("0", "false", "no"):
        return {}
    cp = os.environ.get("AGT_CP_URL", "").strip().rstrip("/")
    org = os.environ.get("AGT_ORG_CODE", "").strip()
    user = os.environ.get("AGT_OPERATOR_USER", "demoadmin")
    pw = os.environ.get("AGT_OPERATOR_PASSWORD", "changeme")
    if not (cp and org):
        print("[run] auto-mint skipped (AGT_CP_URL / AGT_ORG_CODE not set)")
        return {}
    try:
        login = _post(f"{cp}/api/v1/auth/login", {"username": user, "password": pw})
        tok = (login.get("data") or login).get("access_token") or login.get("access_token")
        if not tok:
            print("[run] auto-mint: login returned no access_token; skipping")
            return {}
        out: dict[str, str] = {}
        for role in roles:
            r = _post(f"{cp}/api/v1/orgs/{org}/agents/bootstrap-tokens",
                      {"agent_type": role, "ttl_hours": 24, "note": f"auto-mint {role}"},
                      headers={"Authorization": f"Bearer {tok}"})
            body = r.get("data") or r
            out[role] = body.get("token")
        print(f"[run] auto-minted fresh bootstrap tokens for: {', '.join(roles)}")
        return out
    except Exception as e:  # noqa: BLE001 — fail-open
        print(f"[run] auto-mint failed ({e}); falling back to .env tokens")
        return {}


# governance.py maps AGENT_ROLE -> which *_TOKEN env var it reads at import.
_ROLE_TOKEN_ENV = {
    "coordinator": "AGT_COORDINATOR_TOKEN",
    "fin_agent": "AGT_FIN_AGENT_TOKEN",
    "res_agent": "AGT_RES_AGENT_TOKEN",
}


def main() -> None:
    procs: list[subprocess.Popen] = []

    if MODE == "single":
        print("[run] single-process mode")
        tokens = _mint_bootstrap_tokens(["coordinator"])
        env = {"AGENT_ROLE": "coordinator", "FIN_AGENT_URL": "", "RES_AGENT_URL": ""}
        if tokens.get("coordinator"):
            env["AGT_COORDINATOR_TOKEN"] = tokens["coordinator"]
        procs.append(_uvicorn("agentic_ops.server:app", PORT, env))
    else:
        print(f"[run] distributed mode — workers on {FIN_PORT}/{RES_PORT}, "
              f"dashboard on {PORT}")
        tokens = _mint_bootstrap_tokens(["fin_agent", "res_agent", "coordinator"])

        fin_env = {"AGENT_ROLE": "fin_agent"}
        if tokens.get("fin_agent"):
            fin_env["AGT_FIN_AGENT_TOKEN"] = tokens["fin_agent"]
        procs.append(_uvicorn("agentic_ops.agent_service:app", FIN_PORT, fin_env))

        res_env = {"AGENT_ROLE": "res_agent"}
        if tokens.get("res_agent"):
            res_env["AGT_RES_AGENT_TOKEN"] = tokens["res_agent"]
        procs.append(_uvicorn("agentic_ops.agent_service:app", RES_PORT, res_env))

        time.sleep(2)  # give workers a head start
        coord_env = {
            "AGENT_ROLE": "coordinator",
            "FIN_AGENT_URL": f"http://{HOST}:{FIN_PORT}",
            "RES_AGENT_URL": f"http://{HOST}:{RES_PORT}",
        }
        if tokens.get("coordinator"):
            coord_env["AGT_COORDINATOR_TOKEN"] = tokens["coordinator"]
        procs.append(_uvicorn("agentic_ops.server:app", PORT, coord_env))

    print(f"[run] open http://{HOST}:{PORT}  (Ctrl+C to stop)")

    def _shutdown(*_a):
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        while True:
            for p in procs:
                if p.poll() is not None:
                    print(f"[run] process {p.args[-1]} exited ({p.returncode}); "
                          "shutting down.")
                    _shutdown()
            time.sleep(1)
    except KeyboardInterrupt:
        _shutdown()


if __name__ == "__main__":
    main()
