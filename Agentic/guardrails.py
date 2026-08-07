# guardrails.py — single point of control for all guardrail traffic.
#
# Everything now goes through test's INLINE proxy endpoint:
#     {scans_base_url}/openai/{provider}/chat/completions
# (OpenAI-compatible). This is the same endpoint other working agent/MCP/LLM
# apps use — NOT the /backend/v1/scans or /backend/v1/prompts management APIs.
#
# Why inline: the inline proxy is the only endpoint that (a) reads the
# x-cai-metadata-session-id header to link an agent's calls into a single
# Agent Session / fingerprint, and (b) runs guardrails transparently on the
# real model traffic. The management /scan and /prompts APIs do neither, which
# is why the Calypso UI showed "n/a" session IDs and no guardrail logs.
#
# Two entry points, both hitting the same inline endpoint:
#   generate(prompt, agent)        -> real LLM completion (planning/synthesis).
#   scan(text, layer, ..., agent)  -> routes the content (tool call, MCP
#                                     request/response, delegation payload)
#                                     through the same proxy so it is scanned
#                                     inline and recorded under the agent's
#                                     session. Returns a blocked/cleared verdict.
#
# Layers routed via scan():
#   "model"  "mcp"  "mcp_inbound"  "mcp_outbound"  "agent_to_agent"
#
# Each logical agent (coordinator, financial-agent, research-agent,
# financial-mcp, research-mcp, ...) gets its own session ID, sent as a header
# on every request, so they appear as distinct sessions in Calypso.
#
# Every call and its outcome is printed to stdout (flush=True) so guardrail
# activity is visible live in each process's terminal.
#
# All tokens/URLs/session IDs/enabled flags live in guardrails_config.json.

import json
import os
import re
import time
import asyncio
import traceback
import uuid
from datetime import datetime, timezone
import httpx
import certifi

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "guardrails_config.json")


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def _log(msg: str) -> None:
    print(f"[GUARDRAILS {_ts()}] {msg}", flush=True)


def _truncate(value, limit: int = 4000) -> str:
    if not isinstance(value, str):
        try:
            value = json.dumps(value)
        except Exception:
            value = str(value)
    return value if len(value) <= limit else value[:limit] + f"… (+{len(value) - limit} chars)"







# ── No-LLM deterministic planner ─────────────────────────────────────────────
# When AGT_NO_LLM=1, the LLM call is replaced by a deterministic planner so the
# tools actually execute (and AGT governance + A2A are exercised) with zero
# external dependencies. It returns an OpenAI-shaped response: tool_calls when
# the request offered tools, otherwise a text synthesis.
def _det_last_user(payload: dict) -> str:
    for m in reversed(payload.get("messages", []) or []):
        if m.get("role") == "user":
            return m.get("content", "") or ""
    return ""


def _det_symbol(text: str) -> str:
    m = re.search(r"\b([A-Z]{2,6})\b", text or "")
    return m.group(1) if m else "AAPL"


def _det_args(props: dict, text: str, explicit) -> dict:
    if explicit:
        return explicit
    args: dict = {}
    if "symbol" in props:
        args["symbol"] = _det_symbol(text)
    if "query" in props:
        args["query"] = (text or "").strip()[:160]
    if "topic" in props:
        args["topic"] = (text or "").strip()[:160]
    # single-string tools with an unknown arg name: pass the task through
    if not args and props:
        first = next(iter(props))
        args[first] = (text or "").strip()[:160]
    return args


def _deterministic_body(payload: dict) -> dict:
    user_text = _det_last_user(payload)
    explicit = None
    m = re.search(r"Arguments:\s*(\{.*\})", user_text, re.S)
    if m:
        try:
            explicit = json.loads(m.group(1))
        except Exception:
            explicit = None

    tools = payload.get("tools") or []
    if tools:
        calls = []
        for i, t in enumerate(tools):
            fn = t.get("function", t)
            name = fn.get("name")
            props = (fn.get("parameters", {}) or {}).get("properties", {})
            calls.append({
                "id": f"call_{i+1}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(_det_args(props, user_text, explicit))},
            })
        return {"choices": [{"index": 0, "finish_reason": "tool_calls",
                             "message": {"role": "assistant", "content": "", "tool_calls": calls}}]}

    # No tools -> synthesis or guardrail scan: return deterministic text.
    text = None
    for m in payload.get("messages", []) or []:
        c = m.get("content", "") or ""
        if "Tool Results" in c:
            text = "Deterministic synthesis (no LLM). Combined tool outputs:\n\n" + c
            break
    if text is None:
        if user_text.startswith("[guardrail-check"):
            text = "cleared"
        else:
            text = f"Deterministic response (no LLM) for: {user_text[:300]}"
    return {"choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": text}}]}


class Guardrails:
    def _log_payload(self, agent: str, payload: dict):
        print("\n" + "=" * 70)
        print(f"LLM PAYLOAD -> {agent}")
        print("=" * 70)
        print(json.dumps(payload, indent=2))
        print("=" * 70 + "\n")

    def __init__(self, config_path: str = CONFIG_PATH):
        self.config_path = config_path
        self.reload()

    def reload(self):
        try:
            with open(self.config_path, "r") as f:
                self.config = json.load(f)
        except FileNotFoundError:
            # Local run: no guardrails_config.json present. Fall back to an empty
            # config so the AGT governance flow can be exercised without the
            # external guardrail scanner. Copy resources/guardrails_config.example.json
            # to guardrails_config.json and fill in `token` to enable scanning.
            _log(f"guardrails config not found at {self.config_path}; "
                 f"using defaults (external scanning disabled)")
            self.config = {}

        self.token = self.config.get("token", "")
        self.base_url = (
            self.config.get("scans_base_url") or "https://us1.test.app"
        ).rstrip("/")
        self.provider = self.config.get("provider", "azure-gpt4o-mini")
        self.layers = self.config.get("layers", {})

        # Central dashboard ingest. Every process (coordinator + MCP servers)
        # POSTs its structured guardrail events here so the dashboard shows a
        # single unified stream across all processes.
        self.dashboard_event_url = (
            self.config.get("dashboard_event_url")
            or os.environ.get("DASHBOARD_EVENT_URL")
            or f"http://127.0.0.1:{os.environ.get('AGENT_PORT', '8080')}/event"
        )

        self.session_id_header = self.config.get("session_id_header", "x-cai-metadata-session-id")
        self.session_id_overrides = {
            k: v for k, v in self.config.get("session_ids", {}).items() if not k.startswith("_")
        }
        self._session_ids = {}   # agent -> resolved session id (generated or overridden)

        _log(f"Config loaded. inline endpoint = {self._chat_url()}")

    def _chat_url(self) -> str:
        return f"{self.base_url}/openai/{self.provider}/chat/completions"

    def session_id_for(self, agent: str) -> str:
        """Each agent gets one stable session ID for the lifetime of this process."""
        if agent not in self._session_ids:
            sid = self.session_id_overrides.get(agent) or str(uuid.uuid4())
            self._session_ids[agent] = sid
            _log(f"Session ID assigned -> agent='{agent}' session_id={sid}")
        return self._session_ids[agent]

    def _headers(self, agent: str) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            self.session_id_header: self.session_id_for(agent),
        }




    async def emit(self, event: dict) -> None:
        """Fire a structured event to the central dashboard ingest."""
        event.setdefault("ts", datetime.now(timezone.utc).isoformat())
        event.setdefault("ts_short", _ts())

        try:
            async with httpx.AsyncClient(timeout=3) as client:
                # ✅ Log cleanly instead of broken call
                print("\n[GUARDRAILS EVENT]")
                print(json.dumps(event, indent=2))
                print()

                await client.post(self.dashboard_event_url, json=event)
        except Exception:
            pass  # dashboard failures should not break system
    async def _inline_chat(self, payload, agent: str):

        # Backward compatibility
        if isinstance(payload, list):
            payload = {
            "model": "llama3.1",
            "messages": payload
            }

        elif isinstance(payload, str):
            payload = {
                "model": "llama3.1",
                "messages": [
                    {
                        "role": "user",
                        "content": payload
                    }
                ]
            }

        elif isinstance(payload, dict):

            payload = payload.copy()

            payload.setdefault("model", "llama3.1")

        else:
            raise ValueError("Unsupported payload type")

        # ── No-LLM deterministic mode ────────────────────────────────────────
        # Skip the external LLM entirely; return a deterministic OpenAI-shaped
        # response so tools execute and AGT governance + A2A are exercised.
        if os.environ.get("AGT_NO_LLM", "").strip().lower() in ("1", "true", "yes"):
            return 200, _deterministic_body(payload)

        print("\n==============================")
        print("GUARDRAILS REQUEST")
        print("==============================")
        print(json.dumps(payload, indent=2))
        print("==============================\n")

        async with httpx.AsyncClient(
        timeout=60,
        verify=False
        ) as client:
            self._log_payload(agent, payload)
            r = await client.post(
                self._chat_url(),
                headers=self._headers(agent),
                json=payload
            )

            try:
                body = r.json()
            except Exception:
                body = {
                "_raw_text": r.text
                }

            return r.status_code, body

    @staticmethod
    def _extract_text(body: dict) -> str:
        try:
            return (body["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError):
            return ""

    @staticmethod
    def _is_blocked(status: int, body: dict) -> bool:
        # Calypso signals an inline guardrail block via a non-2xx status carrying
        # a cai_error, or an error object in the body.
        if status == 200:
            return False
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict) and "cai_error" in err:
            return True
        return status >= 400




    async def generate(self, prompt, agent: str, context: str = "",return_raw: bool = False):

        session_id = self.session_id_for(agent)
        print("Generate data resposne")
        print(prompt)
        print("Generate ad@##########################session")
        print(session_id)
        print("Generate ad@##########################data resposne")
        if isinstance(prompt, dict):
            payload = prompt
        else:
            payload = {
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            }

        _log(
            f"GENERATE -> agent={agent} session={session_id}"
        )

        t0 = time.monotonic()

        status, body = await self._inline_chat(
            payload,
            agent
        )

        dur_ms = int(
            (time.monotonic()-t0)*1000
        )

        text = self._extract_text(body)
        print("########################debug text#################################")
        print("########################debug text#################################")
        #print(text)
        #print(agent)
        #print(payload)
        print("########################debug text#################################")
        print("########################debug text#################################")

        await self.emit({
        "type":"guardrail_event",
        "kind":"generate",
        "agent":agent,
        "session_id":session_id,
        "layer":"model",
        "context":context,
        "status":status,
        "blocked":False,
        "duration_ms":dur_ms
        })
        if return_raw:
            return body
        return text

    def _layer(self, layer: str) -> dict:
        return self.layers.get(layer, {})

    def enabled_for(self, layer: str) -> bool:
        return self._layer(layer).get("enabled", True)

    async def scan(self, text: str, layer: str, context: str = "", agent: str = "default") -> dict:
        """Routes `text` through the inline proxy so it's scanned by Calypso and
        recorded under `agent`'s session. Returns a blocked/cleared verdict."""
        if not self.enabled_for(layer):
            _log(f"SKIP layer={layer} agent={agent} context='{context}' (layer disabled)")
            await self.emit({
                "type": "guardrail_event", "kind": "scan", "agent": agent,
                "session_id": self.session_id_for(agent), "layer": layer,
                "context": context, "direction": "guardrail", "request": _truncate(text),
                "response": "(layer disabled)", "status": 0, "outcome": "skipped",
                "blocked": False, "duration_ms": 0,
            })
            return {"verdict": "skipped", "blocked": False, "response": text}

        session_id = self.session_id_for(agent)
        _log(f"SCAN  -> layer={layer} agent={agent} session_id={session_id} context='{context}'")

        # Frame the payload as a guardrail-check message. Calypso scans the
        # content inline regardless of the wrapper; the wrapper just keeps the
        # session's transcript readable in the fingerprint view.

        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": f"[guardrail-check layer={layer} context={context}]\n{text}"
                }
            ]
        }

        t0 = time.monotonic()
        try:
            #status, body = await self._inline_chat(messages, agent)

            status, body = await self._inline_chat(payload,agent)
            dur_ms = int((time.monotonic() - t0) * 1000)
            blocked = self._is_blocked(status, body)
            verdict = "blocked" if blocked else "cleared"
            _log(
                f"RESULT <- layer={layer} agent={agent} session_id={session_id} "
                f"status={status} outcome={verdict} blocked={blocked} dur={dur_ms}ms"
            )
            if blocked:
                _log(f"RESULT detail layer={layer} body={json.dumps(body)[:400]}")

            await self.emit({
                "type": "guardrail_event", "kind": "scan", "agent": agent,
                "session_id": session_id, "layer": layer, "context": context,
                "endpoint": self._chat_url(), "direction": "guardrail",
                "request": _truncate(text),
                "response": _truncate(self._extract_text(body) or body),
                "status": status, "outcome": verdict, "blocked": blocked,
                "duration_ms": dur_ms,
            })
            return {"verdict": verdict, "blocked": blocked, "response": text, "raw": body}
        except Exception as e:
            dur_ms = int((time.monotonic() - t0) * 1000)
            _log(f"ERROR <- layer={layer} agent={agent} session_id={session_id} exception={e!r}")
            traceback.print_exc()
            await self.emit({
                "type": "guardrail_event", "kind": "scan", "agent": agent,
                "session_id": session_id, "layer": layer, "context": context,
                "direction": "guardrail", "request": _truncate(text),
                "response": f"ERROR: {e}", "status": -1, "outcome": "error",
                "blocked": True, "duration_ms": dur_ms,
            })
            # Fail CLOSED: if we can't confirm the content is safe, treat as blocked.
            return {"verdict": "error", "blocked": True, "response": "", "error": str(e)}


guardrails = Guardrails()
