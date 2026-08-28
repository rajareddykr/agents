"""Distributed launcher — spawns three uvicorn processes, one per role.

  fin_agent worker  ->  http://127.0.0.1:FIN_PORT   (AGENT_ROLE=fin_agent)
  res_agent worker  ->  http://127.0.0.1:RES_PORT   (AGENT_ROLE=res_agent)
  coordinator       ->  http://127.0.0.1:COORD_PORT (AGENT_ROLE=coordinator)

Each process gets its own AGENT_ROLE and inherits the rest of the env from
``.env`` — governed_ops/config.py in each child maps the role to that role's
durable credential + registered agent name, so each process attaches its own
DID from CP escrow.

Ctrl-C stops all three.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

# Pin CWD to this file's directory so ``governed_ops`` is importable in each
# child regardless of where the user invoked ``python run.py`` from (repo
# root, absolute path, IDE run-button, …). Without this the workers crash
# with ModuleNotFoundError before any of our code prints anything useful.
_ROOT = Path(__file__).resolve().parent


# Load .env into THIS process's env so the children inherit it. Use the same
# setdefault semantics governed_ops.config uses — process env wins.
def _load_dotenv() -> None:
    p = Path(__file__).with_name(".env")
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

HOST       = os.environ.get("HOST", "127.0.0.1")
FIN_PORT   = os.environ.get("FIN_PORT",   "8101")
RES_PORT   = os.environ.get("RES_PORT",   "8102")
COORD_PORT = os.environ.get("COORD_PORT", os.environ.get("PORT", "8100"))

# Where the children's own logging lands (governed_ops/logging_setup.py) and
# where this launcher tees their raw console streams. Same resolution order as
# ``logging_setup.log_dir`` — keep the two in step.
_LOG_DIR = Path(
    (os.environ.get("AGT_LOG_DIR") or "").strip() or (_ROOT / "logs")
).expanduser()


def _log(msg: str) -> None:
    """Print to the console AND append to ``logs/launcher.log``.

    The launcher's own lines matter as much as the children's: "child exited
    (1)" was previously the only record that a worker died, and it was
    console-only. Best-effort — a launcher must not fail because a log write
    did.
    """
    print(msg, flush=True)
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with (_LOG_DIR / "launcher.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {msg}\n")
    except OSError:
        pass


def _tee(proc: subprocess.Popen, role: str) -> threading.Thread:
    """Pump a child's merged stdout/stderr to console AND to a file.

    Why tee rather than redirect: a plain ``stdout=<file>`` would take the three
    workers' output off the operator's screen, and inheriting the parent's
    stdout (the previous behaviour) left no file at all. Both streams are needed
    — the file is the only place an import-time traceback or a bare ``print``
    survives, since neither goes through ``logging`` and so neither reaches
    ``logs/<role>.log``.

    Byte-oriented on purpose: the child writes UTF-8 (``PYTHONIOENCODING``) but
    a partial write or a non-UTF-8 dependency should not kill the pump, so the
    file gets the raw bytes and only the console copy is lossily decoded.
    """
    def pump() -> None:
        path = _LOG_DIR / f"{role}.console.log"
        try:
            _LOG_DIR.mkdir(parents=True, exist_ok=True)
            sink = path.open("ab")
        except OSError as exc:
            sink = None
            _log(f"[governed_app] WARNING: cannot tee {role} console to {path}: {exc}")
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                sys.stdout.write(raw.decode("utf-8", "replace"))
                sys.stdout.flush()
                if sink is not None:
                    sink.write(raw)
                    sink.flush()
        except (ValueError, OSError):
            # Closed/broken pipe — the ordinary way this ends at shutdown.
            pass
        except Exception as exc:  # noqa: BLE001
            # Anything else is a bug in the pump, and swallowing it would
            # silently truncate the log while the child kept running — the
            # exact "healthy-looking silence" this whole change exists to
            # remove. Say so in both destinations and let the child continue.
            _log(f"[governed_app] ERROR: tee for {role} died: {exc!r}")
        finally:
            if sink is not None:
                sink.close()

    t = threading.Thread(target=pump, name=f"tee-{role}", daemon=True)
    t.start()
    return t


def _spawn(app: str, port: str, role: str,
           extras: dict[str, str] | None = None) -> subprocess.Popen:
    env = os.environ.copy()
    env["AGENT_ROLE"] = role
    env["PORT"] = port
    env["PYTHONIOENCODING"] = "utf-8"
    # Belt-and-braces: prepend the app root to PYTHONPATH so uvicorn's
    # ``AppLoader`` finds ``governed_ops`` even if some launcher (IDE, docker
    # entrypoint, …) changed the child's CWD before Python started.
    env["PYTHONPATH"] = str(_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env.update(extras or {})
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", app,
         "--host", HOST, "--port", port, "--log-level", "info"],
        env=env,
        cwd=str(_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    _tee(proc, role)
    return proc


def main() -> None:
    procs: list[subprocess.Popen] = []

    _log(f"[governed_app] logs -> {_LOG_DIR}")

    _log(f"[governed_app] fin_agent  worker -> http://{HOST}:{FIN_PORT}")
    procs.append(_spawn("governed_ops.worker_app:app", FIN_PORT, "fin_agent"))

    _log(f"[governed_app] res_agent  worker -> http://{HOST}:{RES_PORT}")
    procs.append(_spawn("governed_ops.worker_app:app", RES_PORT, "res_agent"))

    # Give workers a head start so /whoami is warm by the time the coordinator
    # runs its first mission.
    time.sleep(2)

    _log(f"[governed_app] coordinator      -> http://{HOST}:{COORD_PORT}")
    procs.append(_spawn(
        "governed_ops.server:app", COORD_PORT, "coordinator",
        extras={
            "FIN_AGENT_URL": f"http://{HOST}:{FIN_PORT}",
            "RES_AGENT_URL": f"http://{HOST}:{RES_PORT}",
        },
    ))

    _log(f"[governed_app] ready. POST http://{HOST}:{COORD_PORT}/mission")

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
                    _log(f"[governed_app] child exited ({p.returncode}); shutting down.")
                    _shutdown()
            time.sleep(1)
    except KeyboardInterrupt:
        _shutdown()


if __name__ == "__main__":
    main()
