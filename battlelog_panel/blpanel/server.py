"""HTTP API + admin UI + public site endpoints (Starlette)."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import tempfile
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .audit import AuditLogger
from .auth import COOKIE, SESSION_TTL, Auth
from .config import Settings
from .connector import ENDPOINTS, BattlelogConnector, ConnectorError
from .dogtags import DogtagManager
from .modification import LabError, ModificationEngine
from .platforms import PLATFORM_LABELS, PLATFORMS
from .statistics import StatisticsManager
from .storage import Storage
from .sync import SyncManager
from .write_adapter import AuthorizedWriteAdapter

STATIC = Path(__file__).resolve().parent / "static"
log = logging.getLogger("blpanel.server")


class Panel:
    """Wires the modules together; one instance per process/environment."""

    def __init__(self, settings: Settings, storage: Storage | None = None, connector: BattlelogConnector | None = None,
                 dogtags: DogtagManager | None = None):
        settings.ensure_dirs()
        self.settings = settings
        self.storage = storage or Storage(settings.db_path)
        self.audit = AuditLogger(self.storage)
        self.connector = connector or BattlelogConnector(settings)
        self.stats = StatisticsManager()
        self.dogtags = dogtags or DogtagManager()
        self.writer = AuthorizedWriteAdapter(settings, self.audit)
        self.lab = ModificationEngine(self.storage, self.audit, self.writer, self.stats)
        self.sync = SyncManager(settings, self.storage, self.audit, self.connector, dogtags=self.dogtags)
        self.auth = Auth(settings)


def _err(status: int, message: str, **extra) -> JSONResponse:
    return JSONResponse({"error": message, **extra}, status_code=status)


def create_app(panel: Panel, insecure_dev: bool = False) -> Starlette:
    s = panel.settings
    if insecure_dev and s.environment == "production":
        raise RuntimeError("--insecure-dev is refused in the production environment")

    def role_of(request: Request) -> str | None:
        if insecure_dev:
            return "admin"
        return panel.auth.verify(request.cookies.get(COOKIE))

    def guard(needed: str):
        def deco(fn):
            async def wrapper(request: Request):
                role = role_of(request)
                if not panel.auth.allows(role, needed):
                    return _err(401 if role is None else 403, "authentication required" if role is None
                                else f"{needed} role required")
                if request.method != "GET" and request.headers.get("x-panel-request") != "1":
                    return _err(400, "missing X-Panel-Request header")
                request.state.role = role
                request.state.actor = f"{role}@panel"
                try:
                    return await fn(request)
                except (LabError, ValueError) as e:
                    return _err(400, str(e))
                except ConnectorError as e:
                    return _err(502, str(e), connector_error=e.to_dict())
            return wrapper
        return deco

    async def body(request: Request) -> dict:
        try:
            data = await request.json()
        except (json.JSONDecodeError, ValueError):
            raise ValueError("invalid JSON body")
        if not isinstance(data, dict):
            raise ValueError("JSON object expected")
        return data

    # ---- session -----------------------------------------------------------------
    async def login(request: Request):
        if insecure_dev:
            return JSONResponse({"role": "admin", "insecure_dev": True})
        data = await request.json()
        role = panel.auth.role_for_token(str(data.get("token", "")))
        if not role:
            panel.audit.record("login", "ADMIN", "refused", "anonymous", None,
                               {"ip": request.client.host if request.client else None})
            return _err(401, "invalid token")
        panel.audit.record("login", "ADMIN", "ok", f"{role}@panel", None)
        resp = JSONResponse({"role": role})
        resp.set_cookie(COOKIE, panel.auth.issue(role), max_age=SESSION_TTL, httponly=True, samesite="strict",
                        secure=s.environment == "production", path="/")
        return resp

    async def logout(request: Request):
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    async def me(request: Request):
        return JSONResponse({"role": role_of(request), "auth_configured": panel.auth.configured,
                             "insecure_dev": insecure_dev})

    # ---- status / meta -----------------------------------------------------------
    @guard("viewer")
    async def status(request: Request):
        return JSONResponse({
            "environment": s.environment, "game": s.game, "base_url": s.base_url,
            "writes_enabled": s.writes_enabled, "sync": panel.sync.status(),
            "endpoints": ENDPOINTS | {"dogtags": {**ENDPOINTS["dogtags"], "path": s.dogtags_path}},
            "platforms": [{"key": k, "label": PLATFORM_LABELS[k], "id": v} for k, v in PLATFORMS.items()],
            "stat_catalogue": panel.stats.catalogue(), "role": request.state.role,
        })

    @guard("viewer")
    async def feasibility(request: Request):
        return JSONResponse({"writes_enabled": s.writes_enabled, "environment": s.environment,
                             "registered_capabilities": sorted(panel.writer.registry),
                             "matrix": panel.writer.matrix()})

    # ---- profiles ------------------------------------------------------------------
    @guard("viewer")
    async def profiles(request: Request):
        rows = panel.storage.query(
            "SELECT p.*, (SELECT COUNT(*) FROM snapshots s WHERE s.profile_id=p.id) AS snapshots, "
            "(SELECT MAX(captured_at) FROM snapshots s WHERE s.profile_id=p.id) AS last_captured "
            "FROM profiles p ORDER BY p.id")
        return JSONResponse(rows)

    @guard("viewer")
    async def profile_current(request: Request):
        pid = int(request.path_params["pid"])
        snaps = panel.storage.latest_snapshots(pid, 1)
        if not snaps:
            return _err(404, "no snapshot for this profile")
        snap = snaps[0]
        profile = panel.storage.one("SELECT * FROM profiles WHERE id=?", (pid,))
        return JSONResponse({"profile": profile, "snapshot": {k: snap[k] for k in (
            "id", "captured_at", "source_kind", "source_detail", "raw_sha256")},
            "data_classification": "REAL", **snap["normalized"]})

    @guard("viewer")
    async def profile_history(request: Request):
        pid = int(request.path_params["pid"])
        return JSONResponse(panel.sync.build_export(pid)["history"])

    @guard("viewer")
    async def profile_diff(request: Request):
        a = panel.storage.snapshot(int(request.query_params["a"]))
        b = panel.storage.snapshot(int(request.query_params["b"]))
        if not a or not b:
            return _err(404, "snapshot not found")
        return JSONResponse({"a": {k: a[k] for k in ("id", "captured_at", "source_kind")},
                             "b": {k: b[k] for k in ("id", "captured_at", "source_kind")},
                             "changes": StatisticsManager.diff(a["normalized"]["stats"], b["normalized"]["stats"])})

    @guard("viewer")
    async def dogtags(request: Request):
        pid = int(request.path_params["pid"])
        snaps = panel.storage.latest_snapshots(pid, 1)
        if not snaps:
            return _err(404, "no snapshot for this profile")
        q = request.query_params
        cat = panel.dogtags.catalogue(snaps[0]["normalized"].get("dogtags"), q.get("q", ""), q.get("status", ""),
                                      q.get("category", ""))
        cat["snapshot"] = {"id": snaps[0]["id"], "captured_at": snaps[0]["captured_at"],
                           "source_kind": snaps[0]["source_kind"]}
        return JSONResponse(cat)

    # ---- sync --------------------------------------------------------------------
    @guard("admin")
    async def resolve(request: Request):
        data = await body(request)
        result = await asyncio.to_thread(panel.sync.resolve, str(data.get("username", "")), request.state.actor)
        return JSONResponse(result)

    @guard("admin")
    async def sync_now(request: Request):
        d = await body(request)
        result = await asyncio.to_thread(panel.sync.sync_profile, str(d.get("persona_id", "")),
                                         str(d.get("platform", "")), d.get("persona_name"), d.get("user_id"),
                                         "manual", request.state.actor)
        return JSONResponse(result, status_code=200 if result["status"] == "ok" else 502)

    @guard("admin")
    async def import_capture(request: Request):
        name = Path(request.headers.get("x-filename", "capture.json")).name
        raw = await request.body()
        if len(raw) > 64 * 1024 * 1024:
            return _err(413, "file too large")
        suffix = ".har" if name.lower().endswith(".har") else ".json"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"upload{suffix}"
            path.write_bytes(raw)
            result = await asyncio.to_thread(panel.sync.import_capture, path,
                                             request.query_params.get("persona_name"), request.state.actor)
        return JSONResponse(result, status_code=200 if result["status"] == "ok" else 400)

    @guard("viewer")
    async def sync_runs(request: Request):
        return JSONResponse(panel.sync.runs())

    # ---- lab -----------------------------------------------------------------------
    @guard("viewer")
    async def lab_list(request: Request):
        return JSONResponse(panel.lab.list_scenarios())

    @guard("admin")
    async def lab_create(request: Request):
        d = await body(request)
        sid = panel.lab.create(str(d.get("name", "")), int(d["base_snapshot_id"]), request.state.actor)
        return JSONResponse(panel.lab.evaluate(sid))

    @guard("viewer")
    async def lab_get(request: Request):
        return JSONResponse(panel.lab.evaluate(int(request.path_params["sid"])))

    @guard("admin")
    async def lab_delete(request: Request):
        panel.lab.delete(int(request.path_params["sid"]), request.state.actor)
        return JSONResponse({"ok": True})

    @guard("admin")
    async def lab_add_mod(request: Request):
        sid = int(request.path_params["sid"])
        d = await body(request)
        panel.lab.add_modification(sid, d.get("kind", "stat"), str(d.get("target", "")), d.get("op", "set"),
                                   d.get("value"), d.get("strategy", "adjust_kills"), request.state.actor)
        return JSONResponse(panel.lab.evaluate(sid))

    @guard("admin")
    async def lab_remove_mod(request: Request):
        sid = int(request.path_params["sid"])
        panel.lab.remove_modification(sid, request.path_params["mid"], request.state.actor)
        return JSONResponse(panel.lab.evaluate(sid))

    @guard("admin")
    async def lab_restore(request: Request):
        sid = int(request.path_params["sid"])
        panel.lab.restore(sid, request.state.actor)
        return JSONResponse(panel.lab.evaluate(sid))

    @guard("viewer")
    async def lab_export(request: Request):
        sid = int(request.path_params["sid"])
        fmt = request.query_params.get("format", "json")
        content, media = panel.lab.export(sid, fmt, request.state.actor)
        return Response(content, media_type=media, headers={
            "Content-Disposition": f'attachment; filename="lab-scenario-{sid}-SIMULATED.{fmt}"'})

    @guard("admin")
    async def lab_apply_live(request: Request):
        """Ask the AuthorizedWriteAdapter to push a lab modification to the real account."""
        sid = int(request.path_params["sid"])
        d = await body(request)
        sc = next((x for x in panel.lab.list_scenarios() if x["id"] == sid), None)
        if not sc:
            return _err(404, "scenario not found")
        mod = next((m for m in sc["modifications"] if m["id"] == d.get("modification_id")), None)
        if not mod:
            return _err(404, "modification not found")
        kind = "stat" if mod["kind"] == "stat" else "dogtag_unlock"
        result = panel.writer.execute(kind, mod["target"], {"op": mod["op"], "value": mod["value"]},
                                      str(d.get("confirmation", "")), request.state.actor)
        return JSONResponse(result, status_code=200 if result["status"] == "AUTHORIZED_WRITE" else 409)

    # ---- audit -------------------------------------------------------------------
    @guard("viewer")
    async def audit(request: Request):
        return JSONResponse(panel.audit.entries(int(request.query_params.get("limit", 200)),
                                                request.query_params.get("classification") or None))

    # ---- public site endpoints (read-only, REAL data only) ---------------------------
    async def site_profile(request: Request):
        path = s.export_dir / "profile.json"
        if not path.exists():
            return _err(404, "no export yet")
        return Response(path.read_text(encoding="utf-8"), media_type="application/json",
                        headers={"Cache-Control": "public, max-age=60"})

    async def site_widget(request: Request):
        return FileResponse(STATIC / "widget.js", media_type="application/javascript")

    async def index(request: Request):
        return FileResponse(STATIC / "index.html")

    @contextlib.asynccontextmanager
    async def lifespan(app):
        task = None
        if s.auto_sync_minutes > 0:
            async def loop():
                while True:
                    await asyncio.sleep(60)
                    st = panel.sync.status()
                    tracked = panel.sync.tracked_profile()
                    if tracked and "automatic sync is due" in st["pending_reasons"] and not panel.sync.running:
                        await asyncio.to_thread(panel.sync.sync_profile, tracked["persona_id"], tracked["platform"],
                                                tracked["persona_name"], tracked["user_id"], "auto", "scheduler")
            task = asyncio.create_task(loop())
        yield
        if task:
            task.cancel()

    routes = [
        Route("/", index),
        Route("/api/login", login, methods=["POST"]),
        Route("/api/logout", logout, methods=["POST"]),
        Route("/api/me", me),
        Route("/api/status", status),
        Route("/api/feasibility", feasibility),
        Route("/api/profiles", profiles),
        Route("/api/profiles/{pid:int}/current", profile_current),
        Route("/api/profiles/{pid:int}/history", profile_history),
        Route("/api/profiles/{pid:int}/dogtags", dogtags),
        Route("/api/diff", profile_diff),
        Route("/api/resolve", resolve, methods=["POST"]),
        Route("/api/sync", sync_now, methods=["POST"]),
        Route("/api/import", import_capture, methods=["POST"]),
        Route("/api/sync/runs", sync_runs),
        Route("/api/lab/scenarios", lab_list),
        Route("/api/lab/scenarios/new", lab_create, methods=["POST"]),
        Route("/api/lab/scenarios/{sid:int}", lab_get),
        Route("/api/lab/scenarios/{sid:int}/delete", lab_delete, methods=["POST"]),
        Route("/api/lab/scenarios/{sid:int}/modifications", lab_add_mod, methods=["POST"]),
        Route("/api/lab/scenarios/{sid:int}/modifications/{mid}/delete", lab_remove_mod, methods=["POST"]),
        Route("/api/lab/scenarios/{sid:int}/restore", lab_restore, methods=["POST"]),
        Route("/api/lab/scenarios/{sid:int}/export", lab_export),
        Route("/api/lab/scenarios/{sid:int}/apply-live", lab_apply_live, methods=["POST"]),
        Route("/api/audit", audit),
        Route("/site/profile.json", site_profile),
        Route("/site/widget.js", site_widget),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ]
    middleware = []
    if s.site_cors_origins:
        # CORS only matters for the public read-only /site/* endpoints; the admin
        # API relies on a SameSite=Strict cookie, which cross-site requests never carry.
        middleware.append(Middleware(CORSMiddleware, allow_origins=s.site_cors_origins, allow_methods=["GET"]))
    return Starlette(routes=routes, middleware=middleware, lifespan=lifespan)
