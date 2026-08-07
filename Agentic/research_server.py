from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import httpx
import json
from guardrails import guardrails





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
os.environ.setdefault("AGT_AGENT_NAME", "Research-AGENT-5")

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for local development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)











import httpx
import uuid



'''def get_news(topic: str):

    print(f"[Research MCP] Executing get_news() for: {topic}")

    return {
        "topic": topic,
        "total_articles": 3,
        "articles": [
            {
                "headline": f"{topic} Reports Strong Quarterly Performance",
                "source": "Reuters",
                "published_date": "2026-07-01",
                "summary": f"{topic} announced better-than-expected quarterly results, driven by strong revenue growth and increased customer demand.",
                "url": f"https://example.com/news/{topic.lower().replace(' ', '-')}/quarterly-results"
            },
            {
                "headline": f"Analysts Upgrade {topic} Following Positive Outlook",
                "source": "Bloomberg",
                "published_date": "2026-06-30",
                "summary": f"Several analysts raised their ratings on {topic}, citing improved financial guidance and expansion plans.",
                "url": f"https://example.com/news/{topic.lower().replace(' ', '-')}/analyst-upgrade"
            },
            {
                "headline": f"{topic} Announces New Strategic Initiative",
                "source": "CNBC",
                "published_date": "2026-06-29",
                "summary": f"{topic} unveiled a new strategic initiative focused on innovation, AI adoption, and long-term business growth.",
                "url": f"https://example.com/news/{topic.lower().replace(' ', '-')}/strategy"
            }
        ]
    }





def search_web(query: str):

    print(f"[Research MCP] Executing search_web() for: {query}")

    return {
        "query": query,
        "total_results": 3,
        "search_time_ms": 142,
        "results": [
            {
                "title": f"{query} - Overview and Market Analysis",
                "url": f"https://example.com/search/{query.lower().replace(' ', '-')}/overview",
                "snippet": (
                    f"Comprehensive information about {query}, including "
                    "market trends, company performance, financial highlights, "
                    "and recent developments."
                ),
                "source": "Example Search"
            },
            {
                "title": f"Latest Updates on {query}",
                "url": f"https://example.com/search/{query.lower().replace(' ', '-')}/latest",
                "snippet": (
                    f"Recent news, announcements, and analyst commentary "
                    f"related to {query}."
                ),
                "source": "Business News"
            },
            {
                "title": f"{query} - Investor Insights",
                "url": f"https://example.com/search/{query.lower().replace(' ', '-')}/insights",
                "snippet": (
                    f"Expert insights covering business strategy, market "
                    f"position, investment outlook, and future opportunities "
                    f"for {query}."
                ),
                "source": "Financial Insights"
            }
        ]
    }'''







def search_web(query: str):

    print(f"[Research MCP] Executing search_web() for: {query}")

    return {
        "query": query,
        "total_results": 3,
        "search_time_ms": 142,
        "results": [

            {
                "rank": 1,
                "title": f"{query} - Strong Quarterly Growth Report",
                "url": f"https://example.com/search/{query.lower().replace(' ', '-')}/growth",
                "domain": "reuters.com",
                "source": "Reuters",
                "published_date": "2026-07-03",
                "author": "Reuters Staff",
                "category": "Business News",
                "relevance_score": 0.98,
                "confidence": "High",
                "sentiment": "Positive",
                "summary": (
                    f"{query} reported stronger-than-expected quarterly "
                    "performance driven by increasing revenue, improved "
                    "operating margins and strong customer demand."
                )
            },

            {
                "rank": 2,
                "title": f"{query} - Industry Outlook for 2026",
                "url": f"https://example.com/search/{query.lower().replace(' ', '-')}/industry",
                "domain": "bloomberg.com",
                "source": "Bloomberg",
                "published_date": "2026-07-02",
                "author": "Bloomberg Markets",
                "category": "Market Analysis",
                "relevance_score": 0.95,
                "confidence": "High",
                "sentiment": "Positive",
                "summary": (
                    f"Industry analysts expect continued growth for "
                    f"{query}, supported by expanding market demand "
                    "and strategic investments."
                )
            },

            {
                "rank": 3,
                "title": f"{query} - Risk Factors Investors Should Watch",
                "url": f"https://example.com/search/{query.lower().replace(' ', '-')}/risk",
                "domain": "cnbc.com",
                "source": "CNBC",
                "published_date": "2026-07-01",
                "author": "CNBC Research",
                "category": "Investment Analysis",
                "relevance_score": 0.91,
                "confidence": "Medium",
                "sentiment": "Neutral",
                "summary": (
                    f"Although {query} continues to perform well, "
                    "analysts highlight valuation pressure, competition, "
                    "and macroeconomic uncertainty as potential risks."
                )
            }

        ]
    }




def get_news(topic: str):

    print(f"[Research MCP] Executing get_news() for: {topic}")

    return {
        "topic": topic,
        "total_articles": 3,
        "articles": [

            {
                "rank": 1,
                "headline": f"{topic} Reports Strong Quarterly Performance",
                "source": "Reuters",
                "domain": "reuters.com",
                "published_date": "2026-07-03",
                "author": "Reuters Staff",
                "category": "Earnings",
                "relevance_score": 0.99,
                "confidence": "High",
                "sentiment": "Positive",
                "summary": (
                    f"{topic} exceeded analyst expectations with strong "
                    "revenue growth and improved profitability."
                ),
                "url": f"https://timesnews.com/news/{topic.lower().replace(' ','-')}/earnings"
            },

            {
                "rank": 2,
                "headline": f"Analysts Upgrade {topic}",
                "source": "Bloomberg",
                "domain": "bloomberg.com",
                "published_date": "2026-07-02",
                "author": "Bloomberg Markets",
                "category": "Analyst Report",
                "relevance_score": 0.96,
                "confidence": "High",
                "sentiment": "Positive",
                "summary": (
                    f"Several investment firms raised price targets "
                    f"for {topic} following positive financial guidance."
                ),
                "url": f"https://thehindu.com/news/{topic.lower().replace(' ','-')}/upgrade"
            },

            {
                "rank": 3,
                "headline": f"{topic} Faces Increasing Market Competition",
                "source": "CNBC",
                "domain": "cnbc.com",
                "published_date": "2026-07-01",
                "author": "CNBC Research",
                "category": "Industry News",
                "relevance_score": 0.92,
                "confidence": "Medium",
                "sentiment": "Neutral",
                "summary": (
                    f"Market analysts believe {topic} maintains a strong "
                    "position, although competition is expected to increase."
                ),
                "url": f"https://ndfcnews.com/news/{topic.lower().replace(' ','-')}/competition"
            }

        ]
    }














def build_llm_tools(tool_name, arguments):

    if tool_name == "search_web":

        return [
            {
                "type": "function",
                "function": {
                    "name": "search_web",
                    "description": "Search the web for information.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string"
                            }
                        },
                        "required": ["query"]
                    }
                }
            }
        ]

    elif tool_name == "get_news":

        return [
            {
                "type": "function",
                "function": {
                    "name": "get_news",
                    "description": "Get latest news articles.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "topic": {
                                "type": "string"
                            }
                        },
                        "required": ["topic"]
                    }
                }
            }
        ]

    else:
        raise ValueError(f"Unknown tool: {tool_name}")

class ToolRequest(BaseModel):
    name: str
    arguments: dict







@app.post("/call_tool")
@governed(action="research-tool-call")
async def call_tool(request: ToolRequest):
    """
    Receive tool request from Financial Agent and forward
    the request to the LLM with the available tools.
    """

    await guardrails.emit({
    "type": "guardrail_event",
    "kind": "agent_to_agent",
    "agent": "CoordinatorAgent",
    "layer": "agent_to_agent",
    "context": "CoordinatorAgent->ResearchAgent",
    "direction": "outbound",
    "target_agent": "coordinator",
    "blocked": False
    })

    #"response": final_response,


    messages = [
        {
            "role": "system",
            "content": (
                "You are the Research MCP Agent in a multi-agent system. "
                "Your responsibility is to identify and invoke the most appropriate research tool. "
                "Always use the provided tools when available. "
                "Do not answer from your own knowledge. "
                "Do not fabricate information. "
                "If a suitable tool exists, call it and wait for the tool result."
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
    agent="research-mcp",
    context="research-mcp:inbound",
    return_raw=True

    )


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
    "agent": "research-mcp",
    "session_id": guardrails.session_id_for("research-mcp"),
    "layer": "mcp_inbound",
    "context": "research-mcp:inbound",
    "direction": "inbound",
    "tool": tool_name,
    "request": tool_args,
    "blocked": False
    })




    print("Tool Name :", tool_name)

    print("Tool Args :", tool_args)




    await guardrails.emit({
    "type": "guardrail_event",
    "agent": "research-mcp",
    "session_id": guardrails.session_id_for("research-mcp"),
    "layer": "mcp",
    "context": "research-mcp:inbound",
    "direction": "inbound",
    "tool": tool_name,
    "request": tool_args,
    "blocked": False
    })

	#    "kind": "mcp",

    # ============================================================
    # Execute requested tool
    # ============================================================

    if tool_name == "search_web":

        tool_result = search_web(
            tool_args["query"]
        )

    elif tool_name == "get_news":

        tool_result = get_news(
            tool_args["topic"]
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
    "agent": "research-mcp",
    "session_id": guardrails.session_id_for("research-mcp"),
    "layer": "mcp_outbound",
    "context": "research-mcp:outbound",
    "direction": "outbound",
    "tool": tool_name,
    "response": tool_result,
    "blocked": False
    })

#    "kind": "mcp",



    final_response = await guardrails.generate(

        prompt=payload,

        agent="research-mcp",

        context="research-mcp:outbound",

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
    "agent": "research-agent",
    "layer": "agent_to_agent",
    "context": "ResearchAgent->CoordinatorAgent",
    "direction": "outbound",
    "target_agent": "coordinator",
    "response": final_response,
    "blocked": blocked
    })
    #    "session_id": guardrails.session_id_for("research-mcp"),



  





    return {

        "success": True,
        "llm_response": final_response

    }










@app.get("/health")
async def health():
    return {"status": "healthy", "service": "research-mcp"}












# Define available tools
TOOLS = [
    {
        "name": "search_web",
        "description": "Search the web for information",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_news",
        "description": "Get latest news articles about a topic",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "News topic"}
            },
            "required": ["topic"]
        }
    }
]



@app.get("/tools")
async def list_tools():
    return {"tools": TOOLS}




import threading
import time


def heartbeat():
    while True:
        with open("research.heartbeat", "w") as f:
            f.write(str(time.time()))
        time.sleep(5)


threading.Thread(target=heartbeat, daemon=True).start()




if __name__ == "__main__":
    kernel = AutoKernel.instance()   
    print("=" * 60)
    print("Starting Research MCP Server")
    print("URL: http://localhost:8002")
    print("Health Check: http://localhost:8002/health")
    print("=" * 60)
    uvicorn.run(app, host=os.environ.get("AGENT_HOST", "127.0.0.1"),
                port=int(os.environ.get("RES_PORT", "8002")))
