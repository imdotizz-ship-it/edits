"""AuthorizedWriteAdapter: the only place a write to a live account could ever happen.

State shipped in this project: the registry of official write capabilities is
EMPTY, because no officially documented, authorized write interface exists for
Battlelog profile data (see docs/03-viabilidade-gravacao.md). Every request is
therefore answered UNSUPPORTED, with the precise reason, and audited.

If EA/DICE ever publishes an official write API, a capability can be registered
here. Execution then still requires, in order:
  1. BLPANEL_WRITES_ENABLED=true;
  2. the capability is registered with an official documentation URL;
  3. the current environment is listed in the capability's environments;
  4. an explicit typed confirmation phrase matching the exact operation/target;
  5. the executor returns a server-side confirmation; otherwise the result is an error.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .audit import AuditLogger
from .config import Settings

UNSUPPORTED_REASONS = {
    "stat": (
        "Battlefield statistics (kills, deaths, skill, score, K/D, W/L, accuracy, time) are computed by the "
        "game backend from match telemetry. Battlelog only displays them. There is no official, documented or "
        "authorized interface that writes them; changing them on a real account is not legitimately possible."
    ),
    "dogtag_unlock": (
        "Dogtag ownership is granted by the game backend when the unlock criteria are met (or by EA/DICE for "
        "developer/event items). No official interface lets a player or third-party tool grant a dogtag."
    ),
    "dogtag_equip": (
        "Equipping an already owned dogtag is done by the owner while logged in to Battlelog / in game. The web "
        "app uses private, session-authenticated endpoints for this; they are not an official API and this "
        "project does not reuse sessions or tokens, so the operation is not offered."
    ),
}


@dataclass(frozen=True)
class WriteCapability:
    op_id: str
    kind: str
    description: str
    documentation_url: str
    environments: tuple[str, ...]
    executor: Callable[[dict[str, Any]], dict[str, Any]]

    def confirmation_phrase(self, target: str) -> str:
        return f"CONFIRM {self.op_id} {target}"


# Official capabilities. Intentionally empty.
OFFICIAL_CAPABILITIES: dict[str, WriteCapability] = {}


class AuthorizedWriteAdapter:
    def __init__(self, settings: Settings, audit: AuditLogger,
                 registry: dict[str, WriteCapability] | None = None):
        self.settings = settings
        self.audit = audit
        self.registry = dict(OFFICIAL_CAPABILITIES if registry is None else registry)

    def capability_for(self, kind: str) -> WriteCapability | None:
        return next((c for c in self.registry.values() if c.kind == kind), None)

    def assess(self, kind: str) -> dict:
        cap = self.capability_for(kind)
        if cap is None:
            return {"status": "UNSUPPORTED", "kind": kind,
                    "reason": UNSUPPORTED_REASONS.get(kind, "No official write mechanism is registered.")}
        if not self.settings.writes_enabled:
            return {"status": "UNSUPPORTED", "kind": kind, "op_id": cap.op_id,
                    "reason": "Writes are disabled (BLPANEL_WRITES_ENABLED is not true)."}
        if self.settings.environment not in cap.environments:
            return {"status": "UNSUPPORTED", "kind": kind, "op_id": cap.op_id,
                    "reason": f"Capability not authorized for environment {self.settings.environment!r}."}
        return {"status": "AVAILABLE", "kind": kind, "op_id": cap.op_id,
                "documentation_url": cap.documentation_url,
                "reason": "Official capability registered; requires explicit confirmation."}

    def matrix(self) -> list[dict]:
        return [self.assess(k) for k in UNSUPPORTED_REASONS]

    def execute(self, kind: str, target: str, payload: dict, confirmation: str, actor: str) -> dict:
        assessment = self.assess(kind)
        details = {"kind": kind, "target": target, "payload": payload}
        if assessment["status"] != "AVAILABLE":
            self.audit.record("live_write_request", "UNSUPPORTED", "refused", actor, target,
                              {**details, "reason": assessment["reason"]})
            return {"status": "UNSUPPORTED", "reason": assessment["reason"]}
        cap = self.registry[assessment["op_id"]]
        expected = cap.confirmation_phrase(target)
        if confirmation != expected:
            self.audit.record("live_write_request", "AUTHORIZED_WRITE", "refused", actor, target,
                              {**details, "reason": "confirmation phrase mismatch"})
            return {"status": "REFUSED", "reason": f"Type exactly: {expected}"}
        try:
            result = cap.executor({"target": target, **payload})
        except Exception as exc:  # report, never swallow
            self.audit.record("live_write", "AUTHORIZED_WRITE", "error", actor, target,
                              {**details, "error": f"{type(exc).__name__}: {exc}"})
            return {"status": "ERROR", "reason": str(exc)}
        if not (isinstance(result, dict) and result.get("confirmed") is True):
            self.audit.record("live_write", "AUTHORIZED_WRITE", "error", actor, target,
                              {**details, "result": result, "reason": "no server-side confirmation"})
            return {"status": "ERROR", "reason": "The official interface did not confirm the write."}
        self.audit.record("live_write", "AUTHORIZED_WRITE", "ok", actor, target, {**details, "result": result})
        return {"status": "AUTHORIZED_WRITE", "confirmation": result}
