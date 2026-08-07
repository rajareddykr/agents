"""Run the CUSTOMER agents (Agentic) against the LOCAL AGT control plane.

Launches only the customer's three processes:
  * Financial server   -> 127.0.0.1:8001   (financial_server.py, Finance-AGENT-2)
  * Research server     -> 127.0.0.1:8002   (research_server.py,  Research-AGENT-2)
  * Coordinator + UI    -> 127.0.0.1:8080   (agent_system.py,     CO-AGENT-1)

On each launch it:
  1. loads resources/agt.env (local CP config),
  2. auto-mints a FRESH single-use bootstrap token per process from the local CP
     (bootstrap tokens are single-use, so restarts need new tokens),
  3. injects the token + agent name per process and starts it.

Usage:  python resources/run_customer_agents.py
Disable auto-mint with AGT_AUTO_MINT=0 (then set AGT_BOOTSTRAP_TOKEN yourself).
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

HERE = Path(__file__).resolve().parent          # .../Agentic/resources
APP_DIR = HERE.parent                            # .../Agentic  (holds the .py agents)


def _load_env() -> None:
    p = HERE / "agt.env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


_load_env()
HOST = os.environ.get("HOST", "127.0.0.1")


def _post(url: str, body: dict, headers: dict | None = None, timeout: int = 10) -> dict:
    data = json.dumps(body).encode("utf-8")
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, method="POST", headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _mint_tokens(roles: list[str]) -> dict[str, str]:
    """Mint one fresh single-use bootstrap token per role from the local CP."""
    if os.environ.get("AGT_AUTO_MINT", "1").strip().lower() in ("0", "false", "no"):
        return {}
    cp = os.environ.get("AGT_CP_URL", "").rstrip("/")
    org = os.environ.get("AGT_ORG_CODE", "")
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
                      {"agent_type": role, "ttl_hours": 24, "note": f"customer {role}"},
                      headers={"Authorization": f"Bearer {tok}"})
            out[role] = (r.get("data") or r).get("token")
        print(f"[run] minted fresh bootstrap tokens for: {', '.join(roles)}")
        return out
    except Exception as e:  # noqa: BLE001 — fail-open
        print(f"[run] auto-mint failed ({e}); agents will fail-open / use .env token")
        return {}


def _spawn(script: str, extra_env: dict) -> subprocess.Popen:
    env = os.environ.copy()
    env.update({k: v for k, v in extra_env.items() if v})
    return subprocess.Popen([sys.executable, str(APP_DIR / script)], cwd=str(APP_DIR), env=env)


def main() -> None:
    tokens = _mint_tokens(["coordinator", "financial-agent", "research-agent"])
    procs: list[subprocess.Popen] = []

    print("[run] starting Financial server on :8001")
    procs.append(_spawn("financial_server.py", {
        "AGT_AGENT_NAME": "Finance-AGENT-5",
        "AGT_BOOTSTRAP_TOKEN": tokens.get("financial-agent", ""),
    }))
    print("[run] starting Research server on :8002")
    procs.append(_spawn("research_server.py", {
        "AGT_AGENT_NAME": "Research-AGENT-5",
        "AGT_BOOTSTRAP_TOKEN": tokens.get("research-agent", ""),
    }))
    time.sleep(2)  # give the sub-agent servers a head start (coordinator calls them)
    print("[run] starting Coordinator + UI on :8080")
    procs.append(_spawn("agent_system.py", {
        "AGT_AGENT_NAME": "CO-AGENT-5",
        "AGT_BOOTSTRAP_TOKEN": tokens.get("coordinator", ""),
    }))

    print(f"[run] open the UI:  http://{HOST}:8080   (Ctrl+C to stop all)")

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
                    print(f"[run] a process exited ({p.returncode}); shutting down.")
                    _shutdown()
            time.sleep(1)
    except KeyboardInterrupt:
        _shutdown()


if __name__ == "__main__":
    main()
