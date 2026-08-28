"""FastAPI surface for the coordinator process.

Deliberately thin — the interesting behaviour lives in ``coordinator.py``
where two ``@peer_verified`` decorators gate delegation to the workers.

  * POST /mission        → run a mission, return the fused result
  * GET  /events         → this session's event trail (mission demo)
  * GET  /whoami         → this process's registered agent identity
  * GET  /health         → liveness
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from . import config, policy_report
from .coordinator import Coordinator
from .events import BUS

log = logging.getLogger("governed_ops.server")

if config.ROLE != "coordinator":
    raise RuntimeError(
        f"server was started with AGENT_ROLE={config.ROLE!r} — the "
        f"coordinator server only runs under AGENT_ROLE=coordinator. Use "
        f"worker_app.py for fin_agent / res_agent."
    )

app = FastAPI(title="coordinator")
_coord = Coordinator()


@app.on_event("startup")
async def _report_policies() -> None:
    """Name the bound policies in ``logs/governance.log``.

    The coordinator's own bundle is worth naming separately from the workers':
    the three processes have three DIDs, so the CP resolves three independently
    scoped bundles and they can legitimately differ.
    """
    policy_report.install()


class Mission(BaseModel):
    mission: str


_DASHBOARD_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>governed_app</title>
<style>
 body{font:14px/1.5 ui-sans-serif,system-ui,Segoe UI,Roboto,sans-serif;
      margin:0;padding:2rem;background:#0f1115;color:#e6e8ee}
 h1{font-size:1.2rem;margin:0 0 1.2rem;letter-spacing:.02em}
 .row{display:grid;grid-template-columns:repeat(3,1fr);gap:.8rem;margin:.6rem 0 1.4rem}
 .card{background:#171b22;border:1px solid #262c36;border-radius:8px;padding:.9rem 1rem}
 .card .role{font-size:.72rem;text-transform:uppercase;letter-spacing:.09em;color:#8a94a6}
 .card .name{font-weight:600;margin:.2rem 0}
 .card .did{font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;color:#9fd0a7;word-break:break-all}
 form{display:flex;gap:.5rem;margin:1rem 0 .6rem}
 input[type=text]{flex:1;padding:.55rem .7rem;font:14px inherit;background:#0f1115;
   color:#e6e8ee;border:1px solid #2f3641;border-radius:6px}
 button{padding:.55rem 1rem;background:#3d5afe;color:#fff;border:0;border-radius:6px;
   font:600 14px inherit;cursor:pointer}
 button:hover{background:#3247d1}
 pre{background:#0f1115;border:1px solid #262c36;border-radius:6px;padding:.9rem 1rem;
   font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;overflow:auto;max-height:60vh}
 .tag{display:inline-block;padding:.05rem .5rem;border-radius:999px;
   font-size:.72rem;background:#262c36;color:#a4adc0}
 .tag.blocked{background:#4a1220;color:#ffbdc7}
 .tag.a2a{background:#122a4a;color:#bdd7ff}
 .tag.mcp_in,.tag.mcp_out{background:#122a1d;color:#bdffce}
 .tag.flow{background:#3a2c14;color:#ffdcae}
 .ev{display:flex;gap:.6rem;padding:.35rem 0;border-bottom:1px solid #1c222b}
 .ev .who{color:#8a94a6;min-width:11rem}
 .hint{color:#8a94a6;font-size:.85rem;margin:1.2rem 0 .3rem}
</style></head><body>
<h1>governed_app — coordinator dashboard</h1>

<div class="hint">Identity (from each process's <code>/whoami</code>)</div>
<div class="row" id="ids">
  <div class="card" id="c-coord"><div class="role">Coordinator</div><div class="name">…</div><div class="did">…</div></div>
  <div class="card" id="c-fin"><div class="role">Financial Agent</div><div class="name">…</div><div class="did">…</div></div>
  <div class="card" id="c-res"><div class="role">Research Agent</div><div class="name">…</div><div class="did">…</div></div>
</div>

<form id="f" onsubmit="event.preventDefault(); runMission();">
  <input id="m" type="text" placeholder='e.g. Analyze the market trends for HDFC bank'
    value="Analyze the market trends for HDFC bank" />
  <button type="submit">Run mission</button>
</form>
<div class="hint">Try <code>HDFC bank</code> (allowed) vs <code>SBI</code> (denied by policy).</div>

<div class="hint">Result</div>
<pre id="out">—</pre>

<div class="hint">Events (this session)</div>
<pre id="ev">—</pre>

<script>
const $ = s => document.querySelector(s);
async function loadIds() {
  // All three go through the coordinator so no CORS worries.
  const urls = {'#c-coord': '/whoami', '#c-fin': '/whoami/fin', '#c-res': '/whoami/res'};
  for (const [sel, url] of Object.entries(urls)) {
    try {
      const r = await fetch(url); const d = await r.json();
      $(sel + ' .name').textContent = d.name || d.role || '(unknown)';
      $(sel + ' .did').textContent = d.did || d.error || 'no did (governance off?)';
    } catch (e) {
      $(sel + ' .did').textContent = 'unreachable';
    }
  }
}
async function runMission() {
  const mission = $('#m').value;
  $('#out').textContent = '…running…'; $('#ev').textContent = '…';
  const t0 = Date.now() / 1000;
  const r = await fetch('/mission', {method:'POST',
    headers:{'content-type':'application/json'},
    body: JSON.stringify({mission})});
  const d = await r.json();
  $('#out').textContent = JSON.stringify(d, null, 2);
  const evr = await fetch('/events?since=' + t0);
  const ev = (await evr.json()).events;
  $('#ev').innerHTML = ev.map(e =>
    `<div class="ev"><span class="tag ${e.type}">${e.type}</span>` +
    `<span class="who">${e.source}${e.target ? ' → ' + e.target : ''}</span>` +
    `<span>${(e.message||'').replace(/</g,'&lt;')}</span></div>`
  ).join('') || '(no events)';
}
loadIds();
</script>
</body></html>
"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _DASHBOARD_HTML


@app.get("/health")
def health() -> dict:
    return {"ok": True, "role": config.ROLE}


@app.get("/whoami/fin")
def whoami_fin() -> dict:
    return _proxy_whoami(config.FIN_AGENT_URL)


@app.get("/whoami/res")
def whoami_res() -> dict:
    return _proxy_whoami(config.RES_AGENT_URL)


def _proxy_whoami(base_url: str) -> dict:
    """Fetch a peer's ``/whoami`` server-side to sidestep browser CORS.

    Same-origin from the dashboard's perspective; a tiny GET on the
    coordinator that just forwards. Fail-open with an ``{error}`` payload so
    the dashboard can still render the other cards.
    """
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(f"{base_url}/whoami", timeout=3) as r:  # noqa: S310
            return json.loads(r.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — diagnostics endpoint
        return {"error": str(exc), "base_url": base_url}


@app.get("/whoami")
def whoami() -> dict:
    did = None
    name = config.AGENT_NAME
    try:
        from agt_sdk import AutoKernel     # noqa: PLC0415 — diagnostics-only
        engines = AutoKernel.instance().mesh_engines
        ident = getattr(engines, "identity", None) if engines else None
        if ident is not None:
            did = str(getattr(ident, "did", "") or "") or None
            name = getattr(ident, "name", name) or name
    except Exception as exc:  # noqa: BLE001 — governance may be off
        log.debug("whoami: SDK not wired (%s)", exc)
    return {"role": config.ROLE, "did": did, "name": name,
            "fin_agent_url": config.FIN_AGENT_URL,
            "res_agent_url": config.RES_AGENT_URL}


@app.post("/mission")
async def run_mission(body: Mission) -> dict:
    return await _coord.run_mission(body.mission)


@app.get("/events")
def events(since: float = 0.0) -> dict:
    return {"events": [e.to_dict() for e in BUS.history(since=since)]}
