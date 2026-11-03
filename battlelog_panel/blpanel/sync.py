"""SyncManager: Battlelog -> local snapshots -> site export.

Direction is one-way. Nothing here sends data to the game servers: the only
network calls are the connector's anonymous GET requests.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .audit import AuditLogger, utcnow
from .config import Settings
from .connector import BattlelogConnector, ConnectorError, RawResponse, load_import, persona_from_url
from .dogtags import DogtagManager
from .parser import ProfileParser
from .redact import redact
from .statistics import StatisticsManager
from .storage import Storage

EXPORT_SCHEMA = "blpanel.site-export/1"
HISTORY_KEYS = ("kills", "deaths", "kd_ratio", "skill", "score", "wl_ratio", "accuracy", "time_played")


def _combined_digest(responses: list[RawResponse]) -> str:
    h = hashlib.sha256()
    for r in sorted(responses, key=lambda r: r.resource):
        h.update(f"{r.resource}:{r.sha256}\n".encode())
    return h.hexdigest()


class SyncManager:
    def __init__(self, settings: Settings, storage: Storage, audit: AuditLogger, connector: BattlelogConnector,
                 parser: ProfileParser | None = None, dogtags: DogtagManager | None = None):
        self.settings = settings
        self.storage = storage
        self.audit = audit
        self.connector = connector
        self.parser = parser or ProfileParser()
        self.dogtags = dogtags or DogtagManager()
        self._lock = threading.Lock()
        self.running = False

    # ---- read operations -----------------------------------------------------------
    def resolve(self, username: str, actor: str = "system") -> dict:
        try:
            raw = self.connector.fetch_identity(username)
        except ConnectorError as e:
            self.audit.record("resolve_user", "READ", "error", actor, username, e.to_dict())
            raise
        ident = self.parser.parse_identity(raw)
        self.audit.record("resolve_user", "READ", "ok", actor, username,
                          {"url": raw.url, "soldiers": len(ident["soldiers"])})
        return ident

    def _start_run(self, trigger: str, profile_id: int | None) -> int:
        return self.storage.execute(
            "INSERT INTO sync_runs (profile_id, started_at, trigger, status) VALUES (?,?,?, 'running')",
            (profile_id, utcnow(), trigger)).lastrowid

    def _finish_run(self, run_id: int, status: str, **kw) -> None:
        self.storage.execute(
            "UPDATE sync_runs SET finished_at=?, status=?, error_kind=?, error_message=?, snapshot_id=?, "
            "changes_json=?, profile_id=COALESCE(?, profile_id) WHERE id=?",
            (utcnow(), status, kw.get("error_kind"), kw.get("error_message"), kw.get("snapshot_id"),
             json.dumps(kw.get("changes")) if kw.get("changes") is not None else None, kw.get("profile_id"),
             run_id))

    def _store(self, responses: list[RawResponse], identity: dict, source_kind: str) -> tuple[int, int, dict]:
        normalized = self.parser.parse_profile(responses, identity)
        ident = normalized["identity"]
        if not ident.get("persona_id") or not ident.get("platform"):
            raise ValueError("could not determine persona id / platform from the data")
        now = utcnow()
        profile_id = self.storage.upsert_profile(ident["persona_id"], ident.get("persona_name") or ident["persona_id"],
                                                 ident["platform"], self.settings.game, ident.get("user_id"), now)
        previous = self.storage.latest_snapshots(profile_id, 1)
        captured_at = max(r.fetched_at for r in responses)
        raw = redact({r.resource: {"meta": r.meta(), "body": r.body} for r in responses})
        snapshot_id = self.storage.insert_snapshot(profile_id, captured_at, source_kind,
                                                   " | ".join(r.url for r in responses),
                                                   _combined_digest(responses), normalized, raw)
        changes = {"stats": [], "dogtags": [], "first_snapshot": not previous}
        if previous:
            prev = previous[0]["normalized"]
            changes["stats"] = StatisticsManager.diff(prev["stats"], normalized["stats"])
            pt = {t["id"]: t["status"] for t in prev.get("dogtags") or []}
            changes["dogtags"] = [{"id": t["id"], "name": t["name"], "before": pt.get(t["id"]), "after": t["status"]}
                                  for t in normalized.get("dogtags") or [] if pt.get(t["id"]) != t["status"]]
        return profile_id, snapshot_id, changes

    def sync_profile(self, persona_id: str, platform: str, persona_name: str | None = None,
                     user_id: str | None = None, trigger: str = "manual", actor: str = "system") -> dict:
        if not self._lock.acquire(blocking=False):
            return {"status": "busy", "message": "a sync is already running"}
        self.running = True
        run_id = self._start_run(trigger, None)
        try:
            try:
                responses, partial_errors = self.connector.fetch_profile(persona_id, platform, persona_name)
            except ConnectorError as e:
                self._finish_run(run_id, "error", error_kind=e.kind, error_message=str(e))
                self.audit.record("sync", "SYNC", "error", actor, f"{persona_id}/{platform}", e.to_dict())
                return {"status": "error", "error": e.to_dict()}
            identity = {"persona_id": str(persona_id), "platform": platform, "persona_name": persona_name,
                        "user_id": user_id, "game": self.settings.game}
            profile_id, snapshot_id, changes = self._store(responses, identity, "battlelog-live")
            changes["partial_errors"] = partial_errors
            self._finish_run(run_id, "ok", snapshot_id=snapshot_id, changes=changes, profile_id=profile_id)
            self.export(profile_id)
            self.audit.record("sync", "SYNC", "ok", actor, f"{persona_id}/{platform}",
                              {"snapshot_id": snapshot_id, "changed_stats": len(changes["stats"]),
                               "partial_errors": partial_errors, "urls": [r.url for r in responses]})
            return {"status": "ok", "profile_id": profile_id, "snapshot_id": snapshot_id, "changes": changes}
        except Exception as e:  # parser/storage problems are reported, not hidden
            self._finish_run(run_id, "error", error_kind="internal", error_message=f"{type(e).__name__}: {e}")
            self.audit.record("sync", "SYNC", "error", actor, f"{persona_id}/{platform}", {"error": str(e)})
            return {"status": "error", "error": {"kind": "internal", "message": str(e)}}
        finally:
            self.running = False
            self._lock.release()

    def import_capture(self, path: Path, persona_name: str | None = None, actor: str = "system") -> dict:
        run_id = self._start_run("import", None)
        try:
            responses = load_import(path)
            if not responses:
                raise ValueError("no Battlelog JSON responses found in the file")
            identity: dict = {"persona_name": persona_name, "game": self.settings.game}
            ident_resp = [r for r in responses if r.resource == "identity"]
            stat_resp = [r for r in responses if r.resource != "identity"]
            for r in stat_resp:
                pid, plat = persona_from_url(r.url)
                if pid:
                    identity.update(persona_id=pid, platform=plat)
                    break
            if ident_resp:
                parsed = self.parser.parse_identity(ident_resp[0])
                identity["user_id"] = parsed["user"].get("user_id")
                match = [s for s in parsed["soldiers"] if s["persona_id"] == identity.get("persona_id")]
                if match and not persona_name:
                    identity["persona_name"] = match[0]["persona_name"]
            profile_id, snapshot_id, changes = self._store(stat_resp, identity, "manual-import")
            self._finish_run(run_id, "ok", snapshot_id=snapshot_id, changes=changes, profile_id=profile_id)
            self.export(profile_id)
            self.audit.record("import_capture", "READ", "ok", actor, Path(path).name,
                              {"snapshot_id": snapshot_id, "responses": [r.meta() for r in responses]})
            return {"status": "ok", "profile_id": profile_id, "snapshot_id": snapshot_id, "changes": changes}
        except Exception as e:
            self._finish_run(run_id, "error", error_kind="import", error_message=str(e))
            self.audit.record("import_capture", "READ", "error", actor, Path(path).name, {"error": str(e)})
            return {"status": "error", "error": {"kind": "import", "message": str(e)}}

    # ---- site export ---------------------------------------------------------------
    def build_export(self, profile_id: int) -> dict:
        profile = self.storage.one("SELECT * FROM profiles WHERE id=?", (profile_id,))
        history = self.storage.snapshot_history(profile_id)
        if not profile or not history:
            raise ValueError("profile has no snapshots")
        latest = history[-1]
        norm = latest["normalized"]
        catalogue = self.dogtags.catalogue(norm.get("dogtags"))
        return {
            "schema": EXPORT_SCHEMA,
            "generated_at": utcnow(),
            "environment": self.settings.environment,
            "data_classification": "REAL",
            "profile": {"persona_id": profile["persona_id"], "persona_name": profile["persona_name"],
                        "platform": profile["platform"], "game": profile["game"], "user_id": profile["user_id"]},
            "snapshot": {"id": latest["id"], "captured_at": latest["captured_at"],
                         "source_kind": latest["source_kind"], "raw_sha256": latest["raw_sha256"],
                         "sources": norm["sources"]},
            "stats": {k: v for k, v in norm["stats"].items()},
            "extra_stats": norm.get("extra_stats", {}),
            "dogtags": {"available": catalogue["available"], "counts": catalogue["counts"],
                        "total": catalogue.get("total", 0)},
            "history": [{"snapshot_id": s["id"], "captured_at": s["captured_at"], "source_kind": s["source_kind"],
                         **{k: s["normalized"]["stats"].get(k, {}).get("value") for k in HISTORY_KEYS}}
                        for s in history],
        }

    def export(self, profile_id: int) -> Path:
        data = self.build_export(profile_id)
        self.settings.export_dir.mkdir(parents=True, exist_ok=True)
        target = self.settings.export_dir / "profile.json"
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(target)  # atomic swap: the site never reads a half-written file
        (self.settings.export_dir / f"profile-{profile_id}.json").write_text(json.dumps(data, indent=2),
                                                                            encoding="utf-8")
        return target

    # ---- status ----------------------------------------------------------------------
    def runs(self, limit: int = 50) -> list[dict]:
        rows = self.storage.query("SELECT * FROM sync_runs ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["changes"] = json.loads(r.pop("changes_json")) if r.get("changes_json") else None
        return rows

    def tracked_profile(self) -> dict | None:
        row = self.storage.one(
            "SELECT p.* FROM sync_runs r JOIN profiles p ON p.id = r.profile_id "
            "WHERE r.trigger IN ('manual','auto') ORDER BY r.id DESC LIMIT 1")
        return row

    def status(self) -> dict:
        last = self.storage.one("SELECT * FROM sync_runs WHERE status!='running' ORDER BY id DESC LIMIT 1")
        last_ok = self.storage.one("SELECT * FROM sync_runs WHERE status='ok' ORDER BY id DESC LIMIT 1")
        export_path = self.settings.export_dir / "profile.json"
        reasons = []
        if self.running:
            reasons.append("sync in progress")
        if last and last["status"] == "error":
            reasons.append(f"last attempt failed ({last['error_kind']})")
        if self.settings.auto_sync_minutes > 0 and self.tracked_profile():
            due_after = timedelta(minutes=self.settings.auto_sync_minutes)
            ref = last_ok["finished_at"] if last_ok else None
            if not ref or datetime.fromisoformat(ref) + due_after <= datetime.now(timezone.utc):
                reasons.append("automatic sync is due")
        latest_snap = self.storage.one("SELECT id, captured_at FROM snapshots ORDER BY id DESC LIMIT 1")
        if latest_snap and not export_path.exists():
            reasons.append("site export missing")
        if last and last["status"] == "error":
            connection = "error"
        elif last_ok and last_ok["trigger"] in ("manual", "auto"):
            connection = "ok"
        elif last_ok:
            connection = "offline-import-only"
        else:
            connection = "never-connected"
        return {
            "connection": connection,
            "last_attempt": last,
            "last_success": last_ok,
            "pending": bool(reasons),
            "pending_reasons": reasons,
            "auto_sync_minutes": self.settings.auto_sync_minutes,
            "export_path": str(export_path) if export_path.exists() else None,
        }
