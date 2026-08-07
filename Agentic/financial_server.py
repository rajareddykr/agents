from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import httpx
import json
from guardrails import guardrails
import random



import os
# Evaluate full policy rules + defaults.action (SDK defaults this 'off'). Must be
# set BEFORE importing agt_sdk. See resources/agt.env AGT_POLICY_RULE_ENGINE.
os.environ.setdefault("AGT_POLICY_RULE_ENGINE", "on")
from agt_sdk import AGTBlocked, AutoKernel, governed
from agt_sdk.exceptions import AGTBlocked

# ── AGT config: LOCAL control plane ──────────────────────────────────────────
# Repointed from the Tata SaaS CP to the local AGT CP (see resources/agt.env).
# Launcher-injected env wins via setdefault(); nothing secret is hardcoded here.
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
os.environ.setdefault("AGT_AGENT_NAME", "Finance-AGENT-5")

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for local development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)





def build_llm_tools(tool_name, arguments):
    """
    Build OpenAI/LLM tool definitions based on the requested tool.
    """

    if tool_name == "get_stock_price":

        return [
            {
                "type": "function",
                "function": {
                    "name": "get_stock_price",
                    "description": "Get the latest stock price for a company symbol.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "symbol": {
                                "type": "string",
                                "description": "Stock ticker symbol (e.g. TSLA, AAPL, MSFT)"
                            }
                        },
                        "required": ["symbol"]
                    }
                }
            }
        ]

    elif tool_name == "get_financial_metrics":

        return [
            {
                "type": "function",
                "function": {
                    "name": "get_financial_metrics",
                    "description": "Retrieve key financial metrics for a company.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "symbol": {
                                "type": "string",
                                "description": "Stock ticker symbol (e.g. TSLA, AAPL, MSFT)"
                            }
                        },
                        "required": ["symbol"]
                    }
                }
            }
        ]

    else:

        raise ValueError(f"Unknown tool: {tool_name}")











class ToolRequest(BaseModel):
    name: str
    arguments: dict

class ToolListResponse(BaseModel):
    tools: list

# Define available tools
TOOLS = [
    {
        "name": "get_stock_price",
        "description": "Get current stock price and metrics",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Stock ticker symbol"}
            },
            "required": ["symbol"]
        }
    },
    {
        "name": "get_financial_metrics",
        "description": "Get financial metrics like P/E ratio, market cap",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Stock ticker symbol"}
            },
            "required": ["symbol"]
        }
    }
]

@app.get("/health")
async def health():
    return {"status": "healthy", "service": "financial-mcp"}

@app.get("/tools")
async def list_tools():
    return {"tools": TOOLS}






def get_stock_price(symbol: str):

    print(f"[Financial MCP] Executing get_stock_price() for: {symbol}")

    symbol = symbol.upper()

    mock_prices = {
        "TSLA": {
            "price": 238.45,
            "change": "+3.2%",
            "volume": "89.5M"
        },
        "AAPL": {
            "price": 214.82,
            "change": "+1.1%",
            "volume": "54.8M"
        },
        "MSFT": {
            "price": 517.66,
            "change": "+0.8%",
            "volume": "28.3M"
        },
        "NVDA": {
            "price": 182.37,
            "change": "+4.5%",
            "volume": "71.4M"
        },
        "AMZN": {
            "price": 241.20,
            "change": "-0.6%",
            "volume": "36.1M"
        },
        "META": {
            "price": 742.15,
            "change": "+2.3%",
            "volume": "19.4M"
        },
        "GOOGL": {
            "price": 196.75,
            "change": "+0.9%",
            "volume": "22.7M"
        }
    }

    data = mock_prices.get(symbol)



    if not data:

        data = {
        "price": round(random.uniform(20, 1200), 2),
        "change": f"{random.choice(['+','-'])}{round(random.uniform(0.1, 8.0), 2)}%",
        "volume": f"{round(random.uniform(1, 150), 1)}M"
        }

    return {
        "symbol": symbol,
        "price": data["price"],
        "change": data["change"],
        "volume": data["volume"],
        "currency": "USD",
        "exchange": "NASDAQ",
        "timestamp": "2026-07-03T09:30:00Z"
    }


def get_financial_metrics(symbol: str):

    print(f"[Financial MCP] Executing get_financial_metrics() for: {symbol}")

    symbol = symbol.upper()

    mock_metrics = {

        "TSLA": {
            "market_cap": "758B",
            "pe_ratio": 67.8,
            "eps": 3.52,
            "beta": 2.14
        },

        "AAPL": {
            "market_cap": "3.18T",
            "pe_ratio": 31.6,
            "eps": 6.81,
            "beta": 1.18
        },

        "MSFT": {
            "market_cap": "3.75T",
            "pe_ratio": 38.2,
            "eps": 13.42,
            "beta": 0.92
        },

        "NVDA": {
            "market_cap": "4.15T",
            "pe_ratio": 56.4,
            "eps": 3.11,
            "beta": 1.94
        },

        "AMZN": {
            "market_cap": "2.75T",
            "pe_ratio": 42.8,
            "eps": 5.64,
            "beta": 1.27
        },

        "META": {
            "market_cap": "1.95T",
            "pe_ratio": 28.4,
            "eps": 24.81,
            "beta": 1.32
        },

        "GOOGL": {
            "market_cap": "2.40T",
            "pe_ratio": 25.3,
            "eps": 8.14,
            "beta": 1.06
        }

    }

    data = mock_metrics.get(symbol)



    if not data:

        data = {
        "market_cap": f"{round(random.uniform(20, 3500), 1)}B",
        "pe_ratio": round(random.uniform(8, 65), 2),
        "eps": round(random.uniform(0.5, 25), 2),
        "beta": round(random.uniform(0.6, 2.5), 2)
        }


    return {
        "symbol": symbol,
        "market_cap": data["market_cap"],
        "pe_ratio": data["pe_ratio"],
        "eps": data["eps"],
        "dividend_yield": "0%",
        "52_week_high": round(data["eps"] * 85, 2),
        "52_week_low": round(data["eps"] * 42, 2),
        "beta": data["beta"]
    }





























@app.post("/call_tool")
@governed(action="finagent-tool-call")
async def call_tool(request: ToolRequest):
    """
    Receive tool request from Financial Agent and forward
    the request to the LLM with the available tools.
    """



    await guardrails.emit({
    "type": "guardrail_event",
    "kind": "agent_to_agent",
    "agent": "FinancialAgent",
    "layer": "agent_to_agent",
    "context": "CoordinatorAgent->FinancialAgent",
    "direction": "outbound",
    "target_agent": "coordinator",
    "blocked": False
    })



    messages = [
        {
            "role": "system",
            "content": (
                "You are a Financial MCP Agent. "
                "Use the available tools whenever required. "
                "Do NOT ask follow-up questions.\n"
                "Do NOT ask the user what they want.\n"
                "Summarize the financial data.\n"
                "Highlight important metrics.\n"
                "Explain whether the stock appears strong, weak, or neutral.\n"
                "Provide a concise investment-style observation based ONLY on the supplied tool data."
            )
        },
        {
            "role": "user",
            "content": (
                f"Requested Tool: {request.name}\n"
                f"Arguments: {json.dumps(request.arguments)}"
            )
        }
    ]

    # Build available tool definitions
    tools = build_llm_tools(request.name, request.arguments)


    payload = {

            "messages": messages,

            "tools": tools,

            "tool_choice": "auto"

        }

    body = await guardrails.generate(
    prompt=payload,
    agent="financial-mcp",
    context="financial-mcp:inbound",
    return_raw=True

    )
    print("########################debug text#################################")
    print("########################debug text#################################")
    print(body)
    print("########################debug text#################################")
    print("########################debug text#################################")

    tool_calls = body["choices"][0]["message"].get("tool_calls", [])






    if not tool_calls:
    	return {
        "success": True,
        "llm_response": body
    	}


    tool_call = tool_calls[0]


    tool_name = tool_call["function"]["name"]


    tool_args = json.loads(
    tool_call["function"]["arguments"])



    await guardrails.emit({
    "type": "guardrail_event",
    "kind": "mcp",
    "agent": "financial-mcp",
    "session_id": guardrails.session_id_for("financial-mcp"),
    "layer": "mcp_inbound",
    "context": "financial-mcp:inbound",
    "direction": "inbound",
    "tool": tool_name,
    "request": tool_args,
    "blocked": False
    })




    print("Tool Name :", tool_name)

    print("Tool Args :", tool_args)




    await guardrails.emit({
    "type": "guardrail_event",
    "agent": "financial-mcp",
    "session_id": guardrails.session_id_for("financial-mcp"),
    "layer": "mcp",
    "context": "financial-mcp:inbound",
    "direction": "inbound",
    "tool": tool_name,
    "request": tool_args,
    "blocked": False
    })

	#    "kind": "mcp",

    # ============================================================
    # Execute requested tool
    # ============================================================

    if tool_name == "get_stock_price":

        tool_result = get_stock_price(
            tool_args["symbol"]
        )

    elif tool_name == "get_financial_metrics":

        tool_result = get_financial_metrics(
            tool_args["symbol"]
        )
    else:

        raise HTTPException(
            status_code=404,
            detail=f"Unknown tool : {tool_name}"
        )

    messages = [

        {
        "role":"system",
        "content":"You are a Research MCP Agent."
        },

        {
            "role":"user",
            "content":json.dumps(request.arguments)
        },

        body["choices"][0]["message"],

        {
            "role":"tool",
            "tool_call_id":tool_call["id"],
            "content":json.dumps(tool_result)
        }

    ]


    payload = {

        "model":"llama3.1",

        "messages":messages

    }
    await guardrails.emit({
    "type": "guardrail_event",
    "agent": "financial-mcp",
    "session_id": guardrails.session_id_for("financial-mcp"),
    "layer": "mcp_outbound",
    "context": "financial-mcp:outbound",
    "direction": "outbound",
    "tool": tool_name,
    "response": tool_result,
    "blocked": False
    })

#    "kind": "mcp",



    '''final_response = await guardrails.generate(

        prompt=payload,

        agent="financial-mcp",

        context="financial-mcp:outbound",

        return_raw=True

    )

    await guardrails.emit({
    "type": "guardrail_event",
    "kind": "agent_to_agent",
    "agent": "Financial-agent",
    "layer": "agent_to_agent",
    "context": "FinancialAgent->CoordinatorAgent",
    "direction": "outbound",
    "target_agent": "coordinator",
    "response": final_response,
    "blocked": False
    })

    #    "session_id": guardrails.session_id_for("financial-mcp"),





    blocked = False

    try:
        blocked = (
        final_response.get("error", {})
        .get("cai_error", {})
        .get("outcome") == "blocked"
            )
    except AttributeError:
        blocked = False

    await guardrails.emit({
    "type": "guardrail_event",
    "kind": "agent_to_agent",
    "agent": "Financial-agent",
    "layer": "agent_to_agent",
    "context": "FinancialAgent->CoordinatorAgent",
    "direction": "outbound",
    "target_agent": "coordinator",
    "response": final_response,
    "blocked": blocked
    })'''








    final_response = await guardrails.generate(
    prompt=payload,
    agent="financial-mcp",
    context="financial-mcp:outbound",
    return_raw=True
    )

    outcome = (
    final_response.get("error", {})
    .get("cai_error", {})
    .get("outcome")
    )

    blocked = outcome == "blocked"
    print("###############################################%%%%%%%%%%%%%%%%%%%%%%%##############################")
    print("###############################################%%%%%%%%%%%%%%%%%%%%%%%##############################")
    print("###############################################%%%%%%%%%%%%%%%%%%%%%%%##############################")
    print(f"CAI outcome: {outcome}")
    print(f"Blocked: {blocked}")
    print("###############################################%%%%%%%%%%%%%%%%%%%%%%%##############################")
    print("###############################################%%%%%%%%%%%%%%%%%%%%%%%##############################")
    print("###############################################%%%%%%%%%%%%%%%%%%%%%%%##############################")
    await guardrails.emit({
    "type": "guardrail_event",
    "kind": "agent_to_agent",
    "agent": "Financial-agent",
    "layer": "agent_to_agent",
    "context": "FinancialAgent->CoordinatorAgent",
    "direction": "outbound",
    "target_agent": "coordinator",
    "response": final_response,
    "blocked": blocked
    })





















    return {

        "success": True,
        "llm_response": final_response

    }










import threading
import time


def heartbeat():
    while True:
        with open("financial.heartbeat", "w") as f:
            f.write(str(time.time()))
        time.sleep(5)


threading.Thread(target=heartbeat, daemon=True).start()

if __name__ == "__main__":
    kernel = AutoKernel.instance()
    print("=" * 60)
    print("Starting Financial MCP Server")
    print("URL: http://localhost:8001")
    print("Health Check: http://localhost:8001/health")
    print("=" * 60)
    uvicorn.run(app, host=os.environ.get("AGENT_HOST", "127.0.0.1"),
                port=int(os.environ.get("FIN_PORT", "8001")))
