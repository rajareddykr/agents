"""Agentic AI + MCP Agent Ops (VANILLA) — a plain multi-agent orchestration engine.

This is the un-governed baseline: a Coordinator delegates to two specialist
agents that call MCP tool servers, with every step streamed to a live
dashboard. There is NO agt-sdk, NO mesh identity, NO A2A trust handshake.

See docs/AGT_MIGRATION_GUIDE.md for how to add those back, step by step.
"""
__version__ = "0.1.0-vanilla"
