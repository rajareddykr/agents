"""Local, offline safety net.

Runs BEFORE any CP call — cheap, deterministic, always available. Catches the
obviously-bad-inputs class ("insider tip", "ignore previous instructions") so
we do not spend a CP round-trip on nonsense. CP-driven policies (``@governed``)
handle the interesting cases.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_BLOCK_PATTERNS = [
    (re.compile(r"\b(insider\s+tip|guaranteed\s+return|pump\s+and\s+dump)\b", re.I),
     "financial-compliance"),
    (re.compile(r"\b(ignore\s+previous\s+instructions|system\s+prompt)\b", re.I),
     "prompt-injection"),
    (re.compile(r"\b(ssn|social\s+security\s+number)\b", re.I), "pii-leak"),
]


@dataclass
class Verdict:
    allowed: bool
    rule: str | None = None
    reason: str | None = None


def check(text: str) -> Verdict:
    for pattern, rule in _BLOCK_PATTERNS:
        if pattern.search(text or ""):
            return Verdict(False, rule, f"content matched {rule} guardrail")
    return Verdict(True)
