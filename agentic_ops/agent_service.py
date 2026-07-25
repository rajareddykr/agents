"""Agent worker service — hosts ONE agent in its own process.

Selected by the AGENT_ROLE env var (``fin_agent`` or ``res_agent``). The
Coordinator process calls ``POST /analyze``; the worker runs the analysis,
captures the events it emitted for that session, and returns them so the
coordinator can replay them onto the dashboard's live stream.

VANILLA build: the worker has no mesh identity/DID. ``/whoami`` returns just
its role + name. (In the governed build, the process seeds its AGT identity
from AGENT_ROLE before agt_sdk imports, and ``/whoami`` returns the real
``did:mesh`` so the coordinator can handshake against it.)
"""
from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI
from pydantic import BaseModel

from .events import BUS
from .agents.financial_agent import FinancialAgent
from .agents.research_agent import ResearchAgent
from .mcp.fin_mcp import FinMCP
from .mcp.news_mcp import NewsMCP
from . import governance

ROLE = (os.environ.get("AGENT_ROLE") or "fin_agent").strip()
app = FastAPI(title=f"Agent Worker: {ROLE}")

_fin_mcp = FinMCP()
_news_mcp = NewsMCP()


def _make_agent(session: str):
    if ROLE == "res_agent":
        return ResearchAgent(session, _news_mcp)
    return FinancialAgent(session, _fin_mcp)


class AnalyzeReq(BaseModel):
    entity: str
    session: str


@app.on_event("startup")
async def _register_on_startup():
    await asyncio.to_thread(governance.authorize, "agent_online", ROLE)



@app.get("/health")
async def health() -> dict:
    return {"role": ROLE}


@app.get("/whoami")
async def whoami() -> dict:
    """Return THIS worker's real mesh identity so the coordinator can handshake
    against the bootstrap DID (Step 5). Fail-open: role+name only if mesh is off."""
    did = None
    name = os.environ.get("AGT_AGENT_NAME") or os.environ.get("AGENT_NAME") or ROLE
    capabilities: list[str] = []
    try:
        from agt_sdk import AutoKernel
        engines = getattr(AutoKernel.instance(), "mesh_engines", None)
        ident = getattr(engines, "identity", None) if engines else None
        if ident is not None:
            did = str(getattr(ident, "did", "") or "") or None
            name = getattr(ident, "name", name) or name
            capabilities = list(getattr(ident, "capabilities", []) or [])
    except Exception:
        pass
    return {"role": ROLE, "did": did, "name": name, "capabilities": capabilities}


@app.post("/analyze")
async def analyze(req: AnalyzeReq) -> dict:
    collected: list[dict] = []

    async def collector():
        async for ev in BUS.subscribe():
            d = ev.to_dict()
            if d.get("session") == req.session:
                collected.append(d)

    task = asyncio.create_task(collector())
    await asyncio.sleep(0)  # let the subscriber register
    agent = _make_agent(req.session)
    result = await agent.analyze(req.entity)
    await asyncio.sleep(0.1)  # drain trailing events
    task.cancel()
    return {"result": result, "events": collected}
