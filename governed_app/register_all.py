"""Batch bootstrap-token exchange for all three roles.

The CP's Register Agent flow issues **single-use bootstrap tokens**, but
``governed_ops/config.py`` wires the durable ``agt_...`` credential slot
(``AGT_*_TOKEN``). This script closes that gap: it consumes one bootstrap
token per role and either prints or writes back the resulting durable
credentials so ``run.py`` can attach the IMPL-067 identity flow.

Usage
-----
1. Register three agents in the CP UI under org ``AGT_ORG_CODE`` with the
   exact names listed in ``_ROLE_SPECS`` below (escrow is keyed on
   (org, name)). Copy the bootstrap tokens the UI hands you.
2. Drop them into ``.env.bootstrap`` next to this script::

       AGT_COORDINATOR_BOOTSTRAP=agt_...
       AGT_FIN_AGENT_BOOTSTRAP=agt_...
       AGT_RES_AGENT_BOOTSTRAP=agt_...

3. Run::

       python register_all.py            # dry-run — prints results
       python register_all.py --write    # also rewrites .env in place

Each token is single-use; a successful call consumes it. If a call fails
partway, the tokens already consumed cannot be re-used — mint fresh ones
for the remaining roles and re-run.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# Kept in lock-step with governed_ops/config.py:_ROLE_SPECS. Not imported
# because that module has env-mutation side effects at import time.
_ROLE_SPECS: dict[str, tuple[str, str]] = {
    # role: (registered_agent_name, env_var_holding_durable_token)
    "coordinator": ("Coordinator Agent", "AGT_COORDINATOR_TOKEN"),
    "fin_agent":   ("Financial Agent",   "AGT_FIN_AGENT_TOKEN"),
    "res_agent":   ("Research Agent",    "AGT_RES_AGENT_TOKEN"),
}
_BOOTSTRAP_ENV = {
    "coordinator": "AGT_COORDINATOR_BOOTSTRAP",
    "fin_agent":   "AGT_FIN_AGENT_BOOTSTRAP",
    "res_agent":   "AGT_RES_AGENT_BOOTSTRAP",
}

_HERE = Path(__file__).resolve().parent
_ENV_FILE = _HERE / ".env"
_BOOTSTRAP_FILE = _HERE / ".env.bootstrap"


def _load_kv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _write_token_back(role: str, durable: str) -> None:
    env_var = _ROLE_SPECS[role][1]
    text = _ENV_FILE.read_text(encoding="utf-8")
    pattern = re.compile(rf"^{env_var}=.*$", re.MULTILINE)
    if pattern.search(text):
        text = pattern.sub(f"{env_var}={durable}", text)
    else:
        # Append with a trailing newline if the file doesn't end with one.
        sep = "" if text.endswith("\n") else "\n"
        text = f"{text}{sep}{env_var}={durable}\n"
    _ENV_FILE.write_text(text, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true",
                    help="Rewrite AGT_*_TOKEN lines in .env with the durable creds.")
    ap.add_argument("--roles", nargs="+", choices=list(_ROLE_SPECS),
                    default=list(_ROLE_SPECS),
                    help="Subset of roles to register (default: all three).")
    args = ap.parse_args()

    env = _load_kv(_ENV_FILE)
    bootstrap = _load_kv(_BOOTSTRAP_FILE)

    cp = env.get("AGT_CP_URL", "").strip()
    org = env.get("AGT_ORG_CODE", "").strip()
    gw = env.get("AGT_GATEWAY_API_KEY", "").strip()
    if not (cp and org):
        print(f"ERROR: AGT_CP_URL and AGT_ORG_CODE must be set in {_ENV_FILE}")
        return 1

    try:
        from agt_sdk._bootstrap import exchange, BootstrapAlreadyConsumed, BootstrapError
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: agt_sdk not importable ({e}). Activate the venv.")
        return 1

    print(f"CP  : {cp}")
    print(f"Org : {org}")
    print(f"Gwy : {'set' if gw else '(none — direct-CP)'}")
    print()

    any_failure = False
    for role in args.roles:
        name, env_var = _ROLE_SPECS[role]
        tok = bootstrap.get(_BOOTSTRAP_ENV[role], "").strip()
        print(f"-- {role} -> {name!r} ({env_var}) --")
        if not tok:
            print(f"  SKIP: {_BOOTSTRAP_ENV[role]} not set in {_BOOTSTRAP_FILE.name}")
            any_failure = True
            continue
        try:
            r = exchange(
                cp, tok,
                name=name,
                agent_type="langgraph-agent",
                org_code=org,
                gateway_api_key=gw,
            )
        except BootstrapAlreadyConsumed:
            print(f"  FAIL: bootstrap token already consumed. Mint a fresh one.")
            any_failure = True
            continue
        except BootstrapError as e:
            print(f"  FAIL: {e}")
            any_failure = True
            continue
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL: {type(e).__name__}: {e}")
            any_failure = True
            continue

        durable = r.api_token or ""
        print(f"  DID          : {r.did}")
        print(f"  agent_id     : {r.agent_id}")
        print(f"  org_slug     : {r.organization_slug or org}")
        print(f"  {env_var:20}: {durable or '(none — mint agent token in UI)'}")
        if durable and args.write:
            _write_token_back(role, durable)
            print(f"  -> wrote {env_var} into {_ENV_FILE.name}")
        print()

    if not args.write:
        print("Dry run. Re-run with --write to update .env in place.")
    print("Now delete .env.bootstrap (tokens are spent) and start: python run.py")
    return 1 if any_failure else 0


if __name__ == "__main__":
    sys.exit(main())