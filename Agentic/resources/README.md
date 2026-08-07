# Customer agents — run against the LOCAL AGT control plane

This folder repoints the customer's agents (`agent_system.py`, `financial_server.py`,
`research_server.py`) from the Tata SaaS control plane to **our local AGT CP**, and
runs only the customer agents. Nothing in the agents' business logic changed — only
the AGT config was externalized here.

**Location:** `D:\workspaces\agt_governace_agent_demo\agentic-mcp-ops-vanilla\Agentic`

## Contents

| File | Purpose |
|---|---|
| `agt.env` | Local CP config (URL, org, operator creds for auto-mint). No secrets in source. |
| `run_customer_agents.py` | Mints 3 fresh bootstrap tokens and launches the 3 agents. |
| `agt_customer_policy.yaml` | Per-function governance policy to import in the dashboard. |
| `guardrails_config.example.json` | Optional — copy to `../guardrails_config.json` to enable the external scanner. |

## Agents & governed functions

| Process | Port | Agent name | Governed action | Tools |
|---|---|---|---|---|
| `agent_system.py` (Coordinator + UI) | 8080 | CO-AGENT-1 | `run_tool_execution` | — |
| `financial_server.py` | 8001 | Finance-AGENT-2 | `finagent-tool-call` | `get_stock_price`, `get_financial_metrics` |
| `research_server.py` | 8002 | Research-AGENT-2 | `research-tool-call` | `search_web`, `get_news` |

## Prerequisites

- Local AGT control plane running at `http://localhost:20355`, org `demodevelop`,
  operator `demoadmin` / `changeme` (edit `agt.env` if yours differ).
- The customer agents' Python deps installed (agt-sdk, fastapi, uvicorn, httpx,
  pydantic, certifi).
- **Guardrails config (optional):** the agents' external guardrail scanner needs
  `Agentic/guardrails_config.json`. If it's absent, scanning is automatically
  disabled and the agents still run — AGT governance is unaffected. To enable it,
  copy `resources/guardrails_config.example.json` to `Agentic/guardrails_config.json`
  and fill in `token`.

## Step 1 — Import the policy (register the rules)

1. Dashboard → **Governance → Policies → New Policy**.
2. Paste the contents of **`agt_customer_policy.yaml`**, **Save**, then set status =
   **Active (Publish)**.
3. (If your build scopes policies) bind it to org `demodevelop` or to the three
   agents; org-wide is fine.

## Step 2 — Run the customer agents (registers them with the local CP)

```powershell
cd D:\workspaces\agt_governace_agent_demo\agentic-mcp-ops-vanilla\Agentic
python resources\run_customer_agents.py
```

The launcher mints a fresh single-use bootstrap token per process, then starts all
three. On start each agent consumes its token and **registers with the local CP**.
Open the UI at **http://localhost:8080**.

> Bootstrap tokens are single-use, so the launcher mints new ones every run — no
> "already consumed" errors on restart. Disable with `AGT_AUTO_MINT=0` and set your
> own `AGT_BOOTSTRAP_TOKEN` if you prefer.

## Step 3 — Verify functionality

1. In the local dashboard, open **Agent Registry** — you should see
   `CO-AGENT-1`, `Finance-AGENT-2`, `Research-AGENT-2`.
2. Run a task from the UI (http://localhost:8080).
3. Open **Decision Explorer** — you should see **ALLOW** decisions for
   `run_tool_execution`, `finagent-tool-call`, and `research-tool-call`, each
   attributed to the right agent.

### Verify a BLOCK (before / after)

- Edit `agt_customer_policy.yaml` → under `defaults.blocked_actions` add e.g.
  `research-tool-call`, re-paste & re-Publish in the dashboard.
- Re-run a task → the research function now shows **DENY** in Decision Explorer,
  while the others stay ALLOW. Revert to re-allow.
- Content blocks: any request whose arguments contain `confidential` or `restricted`
  is denied by policy (independent of which function).

## Local check (already verified)

- Moved to the new path intact; all four modules (`guardrails`, `financial_server`,
  `research_server`, `agent_system`) compile and import cleanly.
- The config loader resolves `resources/agt.env` and picks up the local CP URL.
- `guardrails.py` no longer crashes when `guardrails_config.json` is missing
  (falls back to defaults, external scanning disabled).

## Troubleshooting

**`WinError 10013 — an attempt was made to access a socket in a way forbidden by
its access permissions`** (on bind to `0.0.0.0:8080`):

- The servers now bind `127.0.0.1` by default (set in `agt.env`), which avoids the
  `0.0.0.0` permission case.
- If it persists, **port 8080 is reserved** on your machine (Docker Desktop /
  Hyper-V / WinNAT reserve ranges). Either:
  1. Change the port — edit `AGENT_PORT` in `resources/agt.env` (e.g. `8090`). The
     UI, guardrail event ingest, and worker URLs all follow it automatically. Then
     open the UI at `http://localhost:<AGENT_PORT>`.
  2. Or free the reserved range (Admin PowerShell):
     ```powershell
     netsh interface ipv4 show excludedportrange protocol=tcp   # see reserved ranges
     net stop winnat ; net start winnat                          # releases Hyper-V reserved ports
     netstat -ano | findstr :8080                                # find a process holding 8080
     ```
- `FIN_PORT` (8001) and `RES_PORT` (8002) are configurable the same way.

## Notes

- The three agent files load AGT config from `resources/agt.env` via `setdefault`,
  so the launcher's injected token/name win and no Tata secrets remain in source.
- To point at a different CP/org, edit `agt.env` only — no code change.
