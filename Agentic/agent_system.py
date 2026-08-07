# agent_system.py — WITH  GUARDRAILS & GRANULAR EVENTS
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn
import httpx
import asyncio
import json
import re
import time
from typing import List
from guardrails import guardrails

import os
# Evaluate full policy rules + defaults.action on the agent (SDK defaults this
# 'off', which enforces only blocked_actions/blocked_patterns). Must be set
# BEFORE importing agt_sdk. See resources/agt.env AGT_POLICY_RULE_ENGINE.
os.environ.setdefault("AGT_POLICY_RULE_ENGINE", "on")
from agt_sdk import AGTBlocked, AutoKernel, governed
from agt_sdk.exceptions import AGTBlocked

# ── AGT config: LOCAL control plane ──────────────────────────────────────────
# Repointed from the Tata SaaS CP to the local AGT CP. Config now comes from
# resources/agt.env plus env injected by resources/run_customer_agents.py (which
# mints a fresh single-use bootstrap token per process). setdefault() means the
# launcher-injected values win and nothing secret is hardcoded here.
from pathlib import Path as _Path
def _load_agt_env():
    _p = _Path(__file__).resolve().parent / "resources" / "agt.env"
    if _p.exists():
        for _ln in _p.read_text(encoding="utf-8").splitlines():
            _ln = _ln.strip()
            if _ln and not _ln.startswith("#") and "=" in _ln:
                _k, _, _v = _ln.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip())
_load_agt_env()
os.environ.setdefault("AGT_AGENT_NAME", "CO-AGENT-2")
os.environ.setdefault("AGT_LOG_LEVEL", "DEBUG")




app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)








clients: List[WebSocket] = []


def parse_tool_calls(raw_plan: str) -> list:
    """Robustly extract tool-call dicts from an LLM plan.

    Handles: markdown ```json fences, a top-level JSON array/object, or loose
    objects with nested 'arguments' braces (which a naive {[^{}]*} regex breaks on).
    """
    if not raw_plan:
        return []

    text = raw_plan.strip()
    # Strip markdown code fences if present.
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()

    # First try: parse the whole thing as JSON (array or single object).
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [c for c in parsed if isinstance(c, dict)]
        if isinstance(parsed, dict):
            return [parsed]
    except json.JSONDecodeError:
        pass

    # Fallback: scan for balanced {...} objects, honoring nested braces.
    calls = []
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                snippet = text[start:i + 1]
                try:
                    obj = json.loads(snippet)
                    if isinstance(obj, dict):
                        calls.append(obj)
                except json.JSONDecodeError:
                    pass
                start = None
    return calls

# --- VISUALIZATION HELPERS ---
async def send_log(msg: str, level: str = "system"):
    """Helper to send a log entry to the dashboard, and print it to the console
    so what's happening server-side is visible without the dashboard open."""
    print(f"[{level.upper()}] {msg}", flush=True)
    await broadcast({"type": "log", "level": level, "message": msg})

async def broadcast(event: dict):
    for c in list(clients):
        try:
            await c.send_json(event)
        except:
            if c in clients:
                clients.remove(c)

FINANCIAL = os.environ.get("FINANCIAL_URL", f"http://127.0.0.1:{os.environ.get('FIN_PORT', '8001')}")
RESEARCH  = os.environ.get("RESEARCH_URL",  f"http://127.0.0.1:{os.environ.get('RES_PORT', '8002')}")

# Tool discovery
async def load_tools():
    tools = []
    async with httpx.AsyncClient() as c:
        for name, url in [("financial", FINANCIAL), ("research", RESEARCH)]:
            try:
                r = await c.get(f"{url}/tools", timeout=30)
                for t in r.json().get("tools", []):
                    t["server"] = name 
                    tools.append(t)
            except:
                pass
    return tools

TOOLS = asyncio.run(load_tools())

# --- LLM WRAPPER WITH GUARDRAIL VISUALIZATION ---
async def llm_call(prompt: str, context: str = "planning") -> str:
    """Scans the prompt, generates via the LLM gateway, then scans the response.
    The scan step and the generation step are two separate AI calls —
    scanning never substitutes for generation."""

    # 1. PRE-SCAN: validate the prompt before it goes anywhere.
    await broadcast({"type": "guardrail_scan", "status": "Scanning Prompt..."})
    await send_log(f" AI: Scanning {context} prompt for risks...", "guardrail")

    pre_verdict = await guardrails.scan(prompt, layer="model", context=f"{context}:input", agent="coordinator")
    if pre_verdict["blocked"]:
        await send_log(f" Blocked input ({context}): {pre_verdict.get('error', pre_verdict['verdict'])}", "err")
        return ""

    await broadcast({"type": "guardrail_verdict", "status": f"Input verdict: {pre_verdict['verdict'].capitalize()}"})
    await send_log(f" AI: Input cleared ({pre_verdict['verdict']}). Generating...", "guardrail")

    # 2. GENERATE: the actual LLM call. Not a guardrail step.
    try:
        response_text = await guardrails.generate(prompt, agent="coordinator")
    except Exception as e:
        await send_log(f"LLM generation failed: {e}", "err")
        return ""

    if not response_text:
        await send_log(f" AI: Generation returned empty text for {context}.", "err")
        return ""

    # 3. POST-SCAN: validate what the LLM produced before it's used downstream.
    '''post_verdict = await guardrails.scan(response_text, layer="model", context=f"{context}:output", agent="coordinator")
    if post_verdict["blocked"]:
        await send_log(f" Blocked output ({context}): {post_verdict.get('error', post_verdict['verdict'])}", "err")
        return "" '''

    await send_log(f" AI: Output cleared ({post_verdict['verdict']}). Proceeding.", "guardrail")
    return response_text


@governed(action="run_tool_execution")
async def run_tool_execution(tool_name: str, args: dict):
    # 1. Identify tool and server
    tool_def = next((t for t in TOOLS if t["name"] == tool_name), None)
    if not tool_def: 
        return {"error": "tool not found"}
    
    server_name = tool_def["server"]
    url = FINANCIAL if server_name == "financial" else RESEARCH
    
    # Map to Dashboard Names
    if server_name == "financial":
        agent_visual = "FinancialAgent"
        mcp_visual = "FINMCP" #"FinancialAgent"  # #
        agent_slug = "financial-agent"
    else:
        agent_visual = "ResearchAgent"
        mcp_visual = "NEWSMCP" #"ResearchAgent"  # #
        agent_slug = "research-agent"

    # 2. AGENT-TO-AGENT GUARDRAIL: Coordinator -> sub-agent delegation.
    # There is no inline hook between agents, so this goes out-of-path
    # (same token, scans_base_url + scans_path) per guardrails_config.json.
    # Tagged under the coordinator's session ID since it's the delegator.
    '''a2a_verdict = await guardrails.scan(
        json.dumps({"tool": tool_name, "arguments": args}),
        layer="agent_to_agent",
        context=f"coordinator->{agent_visual}",
        agent="coordinator"
    )
    if a2a_verdict["blocked"]:
        await send_log(f"Guardrails: delegation to {agent_visual} blocked", "err")
        return {"error": "blocked_by_guardrails", "detail": a2a_verdict.get("error")}'''

    # 3. VISUAL: Agent receives request
    await send_log(f"{agent_visual}: Received task '{tool_name}'", "agent")
    await broadcast({"type": "agent_start", "agent": agent_visual})
    await asyncio.sleep(0.8)

    # 4. VISUAL: Establishing MCP Connection
    await send_log(f"{agent_visual}: Handshaking with {mcp_visual}...", "sys")
    await broadcast({
        "type": "mcp_connect", # NEW EVENT
        "source": agent_visual,
        "target": mcp_visual
    })
    await asyncio.sleep(0.8)

    # 5. VISUAL: Sending Data (The Request)
    await broadcast({
        "type": "mcp_request",
        "source": agent_visual,
        "target": mcp_visual,
        "tool": tool_name,
        "arguments": args,
        "agent_session": guardrails.session_id_for(agent_slug),
    })
    await send_log(f"MCP: Executing {tool_name}...", "mcp")

    # 6. MCP GUARDRAIL: inline scan of the tool call before it leaves the agent.
    # Tagged under this sub-agent's own session ID, distinct from the coordinator's.
    '''mcp_verdict = await guardrails.scan(
        json.dumps({"tool": tool_name, "arguments": args}),
        layer="mcp",
        context=f"{agent_visual}->{mcp_visual}",
        agent=agent_slug
    )
    if mcp_verdict["blocked"]:
        await send_log(f"Guardrails: MCP call '{tool_name}' blocked", "err")
        return {"error": "blocked_by_guardrails", "detail": mcp_verdict.get("error")}'''

    # 7. ACTUAL HTTP CALL


    if "get_stock_price" in tool_name:
        peer_name="Finance-AGENT-5"
    elif "get_financial_metrics" in tool_name:
        peer_name="Finance-AGENT-5"
    elif "search_web" in tool_name:
        peer_name="Research-AGENT-5"
    else:
        peer_name="Research-AGENT-5"
    

    peer=resolve_peer_did(kernel, peer_name) # need to provide agent name being called by the agent
    maybe_publish_handshake(kernel,peer)
    async with httpx.AsyncClient() as c:
        try:
            r = await c.post(f"{url}/call_tool", json={"name": tool_name, "arguments": args}, timeout=60)
            result = r.json()
            
            # 6. VISUAL: Response
            await broadcast({
                "type": "mcp_response",
                "source": mcp_visual,
                "target": agent_visual,
                "tool": tool_name,
                "result": result
            })
            await send_log(f"{mcp_visual}: Data retrieval successful", "mcp")
            await asyncio.sleep(0.5)

            return result
        except Exception as e:
            await send_log(f"MCP Connection Failed: {e}", "err")
            return {"error": str(e)}



async def maybe_publish_handshake(kernel: AutoKernel,peer: str) -> None:
    pub = kernel.cp_handshake_publisher
    if pub is None:
        print("handshake  : skipped (mesh off)")
        return

    from agentmesh.trust.handshake import HandshakeResult

    #peer = os.environ.get("AGT_PEER_DID", peer)
    #peer = os.environ.get("AGT_PEER_DID", "did:mesh:demo-peer")
    result = HandshakeResult.success(
        peer_did=peer,
        trust_score=780,
        capabilities=["search data", "test"],
        peer_name=peer,
    )
    ok = await pub.publish_handshake(
        initiator_did=kernel.config.agent_id,
        transport="a2a",
        result=result,
    )
    print(f"handshake  : {kernel.config.agent_id} <-> {peer} -> {'OK' if ok else 'failed'}")





async def resolve_peer_did(kernel, peer_name: str) -> str | None:
    cfg = kernel.config
    # LOCAL CP, env-driven — no hardcoded host or token.
    _cp = os.environ.get("AGT_CP_URL", "http://localhost:20355").rstrip("/")
    _org = os.environ.get("AGT_ORG_CODE", "demodevelop")
    _tok = (getattr(cfg, "cp_token", None)
            or os.environ.get("AGT_CP_TOKEN")
            or os.environ.get("AGT_BOOTSTRAP_TOKEN", ""))
    url = f"{_cp}/api/v1/orgs/{_org}/agents?limit=200"
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers={"X-AGT-Token": _tok})
        r.raise_for_status()
        items = r.json().get("items", [])
        for a in items:
            did = a.get("did")
            if not did or did == my_did:
                continue
            if name is not None and a.get("name") != name:
                continue
            if not _is_active(a):
                continue
            return did          # first active match wins

        return None

#peer = await resolve_peer_did(kernel, os.environ["AGT_PEER_NAME"]) or "did:mesh:demo-peer









def build_openai_tools():
    tools = []

    for tool in TOOLS:

        # ✅ SUPPORT BOTH parameter formats
        schema = tool.get("parameters") or tool.get("inputSchema", {})

        properties = schema.get("properties", {})
        required = schema.get("required", [])

        tools.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required
                }
            }
        })

    return tools





class SmartAgent:

    async def process(self, task: str):

        start_global = time.time()

        # -----------------------------
        # Coordinator Start
        # -----------------------------
        await broadcast({
            "type": "analysis_start",
            "message": "Coordinator initializing...",
            "task": task,
            "coordinator_session": guardrails.session_id_for("coordinator"),
        })

        # -----------------------------
        # Planning
        # -----------------------------
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a coordinator agent.\n"
                    "You MUST use ALL relevant tools to answer the question.\n"
                    "Available tools include financial data and research.\n"
                    "- get_stock_price\n"
                    "- get_financial_metrics\n"
                    "For analysis or context, ALSO call:\n"
                    "- search_web\n"
                    "- get_news\n"
                    "Combine outputs from ALL tools before answering.\n"
                )
            },
            {
                "role": "user",
                "content": task
            }
        ]

        payload = {
            "messages": messages,
            "tools": build_openai_tools(),
            "tool_choice": "auto"
        }

        body = await guardrails.generate(
            payload,
            agent="coordinator",
            return_raw=True
        )

        print(json.dumps(body, indent=2), flush=True)




        if body.get("error", {}).get("message") == "CAI guardrails blocked the prompt":
            final_text = {
                    "message": "CAI guardrails blocked the prompt"
                }
            final_text = await guardrails.generate(final_text,agent="coordinator")
            total_time = time.time() - start_global

            await broadcast({

            "type": "analysis_complete",

            "total_time": total_time,

            "final_text":
                f"## Analysis\n\n{final_text}\n\n CAI guardrails blocked the prompt"

            })




            return final_text
        else:
            message = body["choices"][0]["message"]

        tool_calls = message.get("tool_calls", [])

        calls = []

        for tc in tool_calls:

            calls.append({
                "tool": tc["function"]["name"],
                "arguments": json.loads(
                    tc["function"]["arguments"]
                )
            })

        print(f"[PLAN] parsed {len(calls)} tool call(s)", flush=True)

        await send_log(
            "Coordinator: Plan generated.",
            "sys"
        )

        await broadcast({
            "type": "plan",
            "raw_plan": json.dumps(tool_calls, indent=2),
            "calls": calls,
            "coordinator_session": guardrails.session_id_for("coordinator"),
        })

        tool_outputs = []

        # -----------------------------
        # Execute tools
        # -----------------------------

        print("##################check_calls################")
        print(calls)
        print("##################check_calls_END################")
        for call in calls:

            try:

                t_name = call["tool"]

                t_args = call["arguments"]

                res = await run_tool_execution(
                    t_name,
                    t_args
                )

                tool_outputs.append({
                    "tool": t_name,
                    "result": res
                })

            except Exception as e:

                tool_outputs.append({
                    "tool": t_name,
                    "error": str(e)
                })

        # -----------------------------
        # No tools?
        # -----------------------------
        if not calls:

            final_text = message.get(
                "content",
                "No response."
            )

        else:

            await send_log(
                "Coordinator: Synthesizing final intelligence...",
                "sys"
            )

            synthesis_messages = [

                {
                    "role": "system",
                    "content":
                        "Write a final response using the tool outputs."
                },

                {
                    "role": "user",
                    "content":
                        f"Original Question:\n{task}"
                },

                {
                    "role": "user",
                    "content": f"Tool Results:\n{json.dumps(tool_outputs, indent=2)}"
                }

            ]

            synthesis_payload = {

                "messages": synthesis_messages

            }
            print("#########################synthesis_payload#######################")
            print(synthesis_payload)
            print("#########################synthesis_payload_END#######################")

            final_text = await guardrails.generate(synthesis_payload,agent="coordinator")

        total_time = time.time() - start_global

        await broadcast({

            "type": "analysis_complete",

            "total_time": total_time,

            "final_text":
                f"## Analysis\n\n{final_text}\n\n_Generated by Multi-AI Agent with MCP tools_"

        })

        return final_text

agent = SmartAgent()

class Task(BaseModel):
    task: str

@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    clients.append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except:
        if websocket in clients:
            clients.remove(websocket)

@app.post("/event")
async def ingest_event(event: dict):
    """Central ingest for structured guardrail events from ALL processes
    (this coordinator + both MCP servers). Re-broadcasts to dashboard clients."""
    await broadcast(event)
    return {"ok": True}

@app.get("/")
async def home():
    return FileResponse("dashboard_ops.html")

@app.get("/animated")
async def animated():
    return FileResponse("dashboard_animated.html")

@app.post("/analyze")
async def analyze(req: Task):
    result = await agent.process(req.task)
    return {"status": "success", "result": result}




import threading
import time


def heartbeat():
    while True:
        with open("agent.heartbeat", "w") as f:
            f.write(str(time.time()))
        time.sleep(5)


threading.Thread(target=heartbeat, daemon=True).start()

if __name__ == "__main__":
    kernel = AutoKernel.instance()
    print("AGENT SYSTEM RUNNING on http://localhost:8080")
    # Bind to 127.0.0.1 by default (avoids Windows WinError 10013 on 0.0.0.0) and
    # allow the port to be overridden via AGENT_PORT if 8080 is reserved.
    uvicorn.run(app, host=os.environ.get("AGENT_HOST", "127.0.0.1"),
                port=int(os.environ.get("AGENT_PORT", "8080")))