"""AuditLogger: append-only history of every operation, with redaction."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from .redact import redact
from .storage import Storage

CLASSIFICATIONS = ("READ", "SIMULATED", "AUTHORIZED_WRITE", "UNSUPPORTED", "SYNC", "ADMIN")
log = logging.getLogger("blpanel.audit")


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class AuditLogger:
    def __init__(self, storage: Storage):
        self.storage = storage

    def record(self, action: str, classification: str, outcome: str = "ok", actor: str = "system",
               target: str | None = None, details: dict[str, Any] | None = None) -> int:
        if classification not in CLASSIFICATIONS:
            raise ValueError(f"unknown classification {classification!r}")
        if outcome not in ("ok", "refused", "error"):
            raise ValueError(f"unknown outcome {outcome!r}")
        safe = redact(details or {})
        cur = self.storage.execute(
            "INSERT INTO audit_log (ts, actor, action, classification, target, outcome, details_json) "
            "VALUES (?,?,?,?,?,?,?)",
            (utcnow(), actor, action, classification, target, outcome, json.dumps(safe, default=str)))
        log.info("audit %s %s %s %s", classification, action, outcome, target or "")
        return cur.lastrowid

    def entries(self, limit: int = 200, classification: str | None = None) -> list[dict]:
        if classification:
            rows = self.storage.query(
                "SELECT * FROM audit_log WHERE classification=? ORDER BY id DESC LIMIT ?",
                (classification, limit))
        else:
            rows = self.storage.query("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["details"] = json.loads(r.pop("details_json"))
        return rows
