"""Redaction of sensitive values before anything reaches logs, audit or exports."""
from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

SENSITIVE_KEYS = re.compile(
    r"(pass(word)?|secret|token|auth|cookie|session|csrf|post-check-sum|checksum|"
    r"api[_-]?key|bearer|email|e-mail|beaker|sid|remid|credential)",
    re.IGNORECASE,
)
_PATTERNS = [
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer " + REDACTED),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), REDACTED),
    (re.compile(r"(?i)(beaker\.session\.id|remid|sid|post-check-sum)=([^;&\s]+)"), r"\1=" + REDACTED),
]


def redact_text(text: str) -> str:
    for pat, repl in _PATTERNS:
        text = pat.sub(repl, text)
    return text


def redact(value: Any) -> Any:
    """Recursively redact dict keys that look sensitive and secret-looking strings."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if isinstance(k, str) and SENSITIVE_KEYS.search(k):
                out[k] = REDACTED
            else:
                out[k] = redact(v)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
