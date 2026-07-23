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

import os
import signal
import subprocess
import sys
import time
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


def main() -> None:
    procs: list[subprocess.Popen] = []

    if MODE == "single":
        print("[run] single-process mode")
        procs.append(_uvicorn("agentic_ops.server:app", PORT,
                              {"AGENT_ROLE": "coordinator",
                               "FIN_AGENT_URL": "", "RES_AGENT_URL": ""}))
    else:
        print(f"[run] distributed mode — workers on {FIN_PORT}/{RES_PORT}, "
              f"dashboard on {PORT}")
        procs.append(_uvicorn("agentic_ops.agent_service:app", FIN_PORT,
                              {"AGENT_ROLE": "fin_agent"}))
        procs.append(_uvicorn("agentic_ops.agent_service:app", RES_PORT,
                              {"AGENT_ROLE": "res_agent"}))
        time.sleep(2)  # give workers a head start
        procs.append(_uvicorn("agentic_ops.server:app", PORT, {
            "AGENT_ROLE": "coordinator",
            "FIN_AGENT_URL": f"http://{HOST}:{FIN_PORT}",
            "RES_AGENT_URL": f"http://{HOST}:{RES_PORT}",
        }))

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
