"""FastAPI app: serves the dashboard, streams events over WebSocket,
and kicks off missions.

VANILLA build: no governance startup hook. The topology endpoint reports the
data mode (mock/live) and run mode (in-process/distributed) only.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .events import BUS
from .orchestrator import Orchestrator, NODES
from . import config
from . import governance

app = FastAPI(title="Agentic AI + MCP Agent Ops (vanilla)")
orchestrator = Orchestrator()

_DASHBOARD = (Path(__file__).parent / "dashboard.html").read_text(encoding="utf-8")


class Mission(BaseModel):
    mission: str


@app.on_event("startup")
async def _register_on_startup():
    # coordinator process announces itself; fail-open, non-fatal
    await asyncio.to_thread(governance.authorize, "agent_online", "coordinator")

@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return _DASHBOARD


@app.get("/api/topology")
async def topology() -> dict:
    return {
        "data_mode": config.data_mode(),
        "run_mode": config.run_mode(),
        "nodes": NODES,
        "tools": {
            "fin_mcp": orchestrator.fin_mcp.list_tools(),
            "news_mcp": orchestrator.news_mcp.list_tools(),
        },
    }


@app.get("/api/mesh")
async def mesh_status() -> dict:
    """Is THIS (coordinator) process mesh-wired? Handshakes only publish to the
    CP when mesh_wired is true. did:mesh:... = good; null = no wiring."""
    from .agents import mesh
    return mesh.mesh_status()


@app.post("/api/mission")
async def start_mission(m: Mission) -> dict:
    # Run the pipeline in the background; events stream over the WebSocket.
    asyncio.create_task(orchestrator.run_mission(m.mission))
    return {"accepted": True, "mission": m.mission}


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await websocket.accept()
    # Replay recent history so a fresh client sees current state.
    for ev in BUS.history():
        await websocket.send_text(json.dumps(ev.to_dict()))
    try:
        async for ev in BUS.subscribe():
            await websocket.send_text(json.dumps(ev.to_dict()))
    except WebSocketDisconnect:
        return
    except Exception:
        return
