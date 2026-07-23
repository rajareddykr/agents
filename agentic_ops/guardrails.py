"""Lightweight, LOCAL guardrail layer (pure regex, offline).

Guardrails inspect content flowing between agents and MCP tools. If a rule
trips, the caller emits a BLOCKED event and drops/redacts the content.

This is the ONLY policy mechanism in the vanilla build. It is deliberately
simple and self-contained: no control plane, no network, no SDK. In the
governed build this local check is the natural seam where an AGT policy
check is added alongside (or in front of) it — see docs/AGT_MIGRATION_GUIDE.md
step 3.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# Naive patterns that stand in for real safety / compliance checks.
_BLOCK_PATTERNS = [
    (re.compile(r"\b(insider\s+tip|guaranteed\s+return|pump\s+and\s+dump)\b", re.I),
     "financial-compliance"),
    (re.compile(r"\b(ignore\s+previous\s+instructions|system\s+prompt)\b", re.I),
     "prompt-injection"),
    (re.compile(r"\b(ssn|social\s+security\s+number)\b", re.I),
     "pii-leak"),
]


@dataclass
class GuardrailResult:
    allowed: bool
    rule: str | None = None
    reason: str | None = None


def check(text: str) -> GuardrailResult:
    """Return whether `text` is allowed to pass between nodes."""
    for pattern, rule in _BLOCK_PATTERNS:
        if pattern.search(text or ""):
            return GuardrailResult(
                allowed=False,
                rule=rule,
                reason=f"content matched {rule} guardrail",
            )
    return GuardrailResult(allowed=True)
