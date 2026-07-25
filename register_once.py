"""One-shot agent registration against the AGT control plane.

Bootstrap tokens are SINGLE-USE. Consuming one registers this agent and mints a
long-lived api_token. Run this ONCE, then switch .env to the long-lived
credential so every later run (smoke_test.py / run.py) reuses it instead of
re-consuming the spent bootstrap token (which 401s).

Usage
-----
1. Mint a fresh bootstrap token (as demoadmin) and set in .env:
       AGT_CP_URL=http://localhost:20355
       AGT_ORG_CODE=demodevelop
       AGT_BOOTSTRAP_TOKEN=agt_boot_...
2. python register_once.py
3. Copy the printed AGT_AGENT_ID / AGT_CP_TOKEN into .env and REMOVE
   AGT_BOOTSTRAP_TOKEN. Done — registration persists across runs.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _load_env() -> None:
    p = Path(__file__).with_name(".env")
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    _load_env()
    cp = os.environ.get("AGT_CP_URL", "").strip()
    org = os.environ.get("AGT_ORG_CODE", "").strip()
    tok = os.environ.get("AGT_BOOTSTRAP_TOKEN", "").strip()
    if not (cp and org and tok):
        print("ERROR: set AGT_CP_URL, AGT_ORG_CODE and AGT_BOOTSTRAP_TOKEN in .env first.")
        return 1

    try:
        from agt_sdk._bootstrap import exchange, BootstrapAlreadyConsumed
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: agt_sdk not importable ({e}). Install it into this venv "
              f"(pip install -e <sdk>[mesh,bootstrap]).")
        return 1

    print(f"Consuming bootstrap token against {cp} (org={org}) ...")
    try:
        r = exchange(
            cp, tok,
            name=os.environ.get("AGT_AGENT_NAME", "vanilla-agent"),
            agent_type=os.environ.get("AGT_AGENT_TYPE", "langgraph-agent"),
            org_code=org,
        )
    except BootstrapAlreadyConsumed:
        print("ERROR: this bootstrap token was ALREADY consumed. Mint a fresh one and retry.")
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: registration failed: {e}")
        return 2

    print("\n=== REGISTERED ✓ ===")
    print(f"  DID            : {r.did}")
    print(f"  agent_id       : {r.agent_id}")
    print(f"  org_slug       : {r.organization_slug or org}")
    print(f"  api_token      : {r.api_token or '(none — older CP; mint an agent API token instead)'}")
    print(f"  token_expires  : {r.api_token_expires_at}")
    print("\nPut these in .env, then REMOVE AGT_BOOTSTRAP_TOKEN (it's spent):")
    print(f"  AGT_AGENT_ID={r.did}")
    if r.api_token:
        print(f"  AGT_CP_TOKEN={r.api_token}")
    print("\nVerify in the dashboard: Agent Registry should now list this DID.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
