"""Public showcase: publish a lab scenario on the site, visible to everyone,
always labelled as a custom build.

The showcase is written to its own file (site_export/showcase.json) and never
to profile.json, which keeps holding only REAL data. Every showcase carries
data_classification="SIMULATED" and a disclosure text; the bundled widget
always renders that disclosure and has no option to hide it.
"""
from __future__ import annotations

import json
from pathlib import Path

from .audit import AuditLogger, utcnow
from .config import Settings
from .modification import ModificationEngine

SCHEMA = "blpanel.showcase/1"
DISCLOSURE = "Build personalizado · não é estatística oficial do Battlelog"
SHOWN_KEYS = ("kills", "deaths", "kd_ratio", "skill", "score", "wl_ratio", "headshots", "accuracy",
              "time_played", "rounds")


class ShowcasePublisher:
    def __init__(self, settings: Settings, lab: ModificationEngine, audit: AuditLogger):
        self.settings = settings
        self.lab = lab
        self.audit = audit

    @property
    def path(self) -> Path:
        return self.settings.export_dir / "showcase.json"

    def build(self, scenario_id: int, title: str | None = None) -> dict:
        ev = self.lab.evaluate(scenario_id)
        if not ev["modifications"]:
            raise ValueError("o cenário não tem alterações; não há o que publicar como build personalizado")
        mod, orig = ev["modified"], ev["original"]
        ident = orig["identity"]
        stats = {}
        for k in SHOWN_KEYS:
            m = mod["stats"].get(k) or {}
            o = orig["stats"].get(k) or {}
            stats[k] = {"value": m.get("value"), "unit": m.get("unit"),
                        "custom": m.get("origin") in ("simulated", "simulated-derived"),
                        "real_value": o.get("value")}
        tags = [{"id": t["id"], "name": t["name"], "status": t["status"], "custom": bool(t.get("simulated"))}
                for t in (mod.get("dogtags") or []) if t.get("simulated")]
        return {
            "schema": SCHEMA,
            "data_classification": "SIMULATED",
            "disclosure": DISCLOSURE,
            "title": (title or ev["scenario"]["name"]).strip()[:80],
            "published_at": utcnow(),
            "persona_name": ident.get("persona_name"),
            "platform": ident.get("platform"),
            "scenario_id": ev["scenario"]["id"],
            "based_on_snapshot": ev["base_snapshot"],
            "stats": stats,
            "custom_dogtags": tags,
            "modifications": [{k: m[k] for k in ("kind", "target", "op", "value")} for m in ev["modifications"]],
        }

    def publish(self, scenario_id: int, title: str | None = None, actor: str = "system") -> dict:
        data = self.build(scenario_id, title)
        self.settings.export_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.path)
        self.audit.record("publish_showcase", "SIMULATED", "ok", actor, f"scenario:{scenario_id}",
                          {"title": data["title"], "modifications": len(data["modifications"])})
        return data

    def current(self) -> dict | None:
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else None

    def unpublish(self, actor: str = "system") -> bool:
        existed = self.path.exists()
        if existed:
            self.path.unlink()
        self.audit.record("unpublish_showcase", "SIMULATED", "ok", actor, None, {"existed": existed})
        return existed
