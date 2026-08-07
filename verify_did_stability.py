"""End-to-end self-test for IMPL-067 register-once + key-escrow.

Starts the three workers (fin_agent, res_agent, coordinator) in fresh OS
processes, captures the DID each one reports via ``/whoami`` (or
``/api/mesh``), stops everything, restarts, and asserts the DIDs are byte-
identical across the restart.

That assertion — three separate OS processes → same DID — is the whole
point of the escrow flow. Nothing can be carried in memory across restarts,
so a stable DID proves the private key was recovered from the CP and the
public-key hash was recomputed to the same value.

Prerequisites (once):
  1. A running control plane at ``AGT_CP_URL``.
  2. Each of the three roles registered in the CP UI (Register Agent). Names
     MUST match ``agentic_ops/governance.py``'s ``_SPECS``:
        coordinator  → "Coordinator Agent"
        fin_agent    → "Financial Agent"
        res_agent    → "Research Agent"
  3. The three durable ``agt_...`` credentials pasted into ``.env`` as
     ``AGT_COORDINATOR_TOKEN`` / ``AGT_FIN_AGENT_TOKEN`` / ``AGT_RES_AGENT_TOKEN``.
  4. ``AGT_AGENT_PASSPHRASE`` set to a ≥32-char org-wide value.
  5. ``AGT_AUTO_MINT=0`` so ``run.py`` does not overwrite the durable creds
     with fresh single-use bootstrap tokens on every launch.

Exit code 0 = PASS, 1 = FAIL.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


HOST = "127.0.0.1"
ROLES = [
    ("fin_agent",   8071, "agentic_ops.agent_service:app"),
    ("res_agent",   8072, "agentic_ops.agent_service:app"),
    ("coordinator", 8000, "agentic_ops.server:app"),
]


def _load_dotenv() -> None:
    p = Path(__file__).with_name(".env")
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ[k.strip()] = v.strip().strip('"').strip("'")


def _start(role: str, port: int, app: str) -> subprocess.Popen:
    env = os.environ.copy()
    env["AGENT_ROLE"] = role
    if role == "coordinator":
        env["FIN_AGENT_URL"] = f"http://{HOST}:8071"
        env["RES_AGENT_URL"] = f"http://{HOST}:8072"
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", app, "--host", HOST, "--port", str(port),
         "--log-level", "warning"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _whoami(role: str, port: int, timeout_s: float = 30.0) -> str | None:
    """Poll ``/whoami`` (or ``/api/mesh`` for coordinator) until a DID appears."""
    url = f"http://{HOST}:{port}/api/mesh" if role == "coordinator" else f"http://{HOST}:{port}/whoami"
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                info = json.loads(r.read())
                did = info.get("did") or info.get("process_did")
                if did:
                    return did
        except Exception:
            pass
        time.sleep(0.5)
    return None


def _round(label: str) -> dict[str, str | None]:
    procs: list[tuple[str, int, subprocess.Popen]] = []
    for role, port, app in ROLES:
        procs.append((role, port, _start(role, port, app)))
        # Workers should be up before the coordinator queries /whoami on them.
        if role != "coordinator":
            time.sleep(0.3)
    dids: dict[str, str | None] = {}
    try:
        for role, port, _ in procs:
            did = _whoami(role, port)
            dids[role] = did
            print(f"  [{label}] {role:12} -> {did}")
    finally:
        for _, _, p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        for _, _, p in procs:
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()
    return dids


def main() -> int:
    _load_dotenv()

    if not os.environ.get("AGT_AGENT_PASSPHRASE"):
        print("SKIP: AGT_AGENT_PASSPHRASE is not set — nothing to test.")
        return 0
    missing = [
        role for role in ("COORDINATOR", "FIN_AGENT", "RES_AGENT")
        if not os.environ.get(f"AGT_{role}_TOKEN")
    ]
    if missing:
        print(f"SKIP: missing durable credentials for: {', '.join(missing)}. "
              "Register each agent in the CP UI and paste the tokens into .env.")
        return 0

    print("=== ROUND 1 (first boot: generate keypair, attach, escrow) ===")
    r1 = _round("R1")
    time.sleep(1)

    print()
    print("=== ROUND 2 (restart: recover from escrow — SAME DID expected) ===")
    r2 = _round("R2")

    print()
    print("=== COMPARISON ===")
    ok = True
    for role, _, _ in ROLES:
        same = r1.get(role) and r1.get(role) == r2.get(role)
        mark = "PASS" if same else "FAIL"
        print(f"  [{mark}] {role:12}  R1={r1.get(role)}  R2={r2.get(role)}")
        if not same:
            ok = False

    print()
    print("RESULT:", "PASS — DIDs stable across restart" if ok else "FAIL — DIDs changed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
