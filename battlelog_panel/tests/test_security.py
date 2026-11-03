import pytest
from starlette.testclient import TestClient

from blpanel.auth import Auth, token_digest
from blpanel.redact import redact
from blpanel.server import create_app

ADMIN, VIEWER = "admin-token-for-tests", "viewer-token-for-tests"
H = {"X-Panel-Request": "1"}


@pytest.fixture
def client(synced):
    panel, r = synced
    panel.settings.admin_token_sha256 = token_digest(ADMIN)
    panel.settings.viewer_token_sha256 = token_digest(VIEWER)
    panel.auth = Auth(panel.settings)
    return TestClient(create_app(panel)), panel, r


def login(c, token):
    assert c.post("/api/login", json={"token": token}).status_code == 200


def test_redaction():
    out = redact({"Cookie": "x", "nested": {"post-check-sum": "y", "note": "mail me@x.com Bearer abc.def"}})
    assert out["Cookie"] == "[REDACTED]" and out["nested"]["post-check-sum"] == "[REDACTED]"
    assert "me@x.com" not in out["nested"]["note"] and "abc.def" not in out["nested"]["note"]


def test_requires_login(client):
    c, _, _ = client
    assert c.get("/api/status").status_code == 401
    assert c.post("/api/login", json={"token": "wrong"}).status_code == 401


def test_viewer_cannot_mutate(client):
    c, _, r = client
    login(c, VIEWER)
    assert c.get("/api/status").status_code == 200
    assert c.post("/api/lab/scenarios/new", json={"base_snapshot_id": r["snapshot_id"]}, headers=H).status_code == 403


def test_csrf_header_required(client):
    c, _, r = client
    login(c, ADMIN)
    assert c.post("/api/lab/scenarios/new", json={"base_snapshot_id": r["snapshot_id"]}).status_code == 400
    assert c.post("/api/lab/scenarios/new", json={"base_snapshot_id": r["snapshot_id"]}, headers=H).status_code == 200


def test_tampered_cookie_rejected(client):
    c, panel, _ = client
    forged = panel.auth.issue("admin")[:-4] + "0000"
    c.cookies.set("blp_session", forged)
    assert c.get("/api/status").status_code == 401


def test_apply_live_is_unsupported_and_audited(client):
    c, panel, r = client
    login(c, ADMIN)
    ev = c.post("/api/lab/scenarios/new", json={"name": "x", "base_snapshot_id": r["snapshot_id"]}, headers=H).json()
    sid = ev["scenario"]["id"]
    ev = c.post(f"/api/lab/scenarios/{sid}/modifications",
                json={"kind": "stat", "target": "kills", "op": "set", "value": 5}, headers=H).json()
    mid = ev["modifications"][0]["id"]
    res = c.post(f"/api/lab/scenarios/{sid}/apply-live", json={"modification_id": mid, "confirmation": "x"},
                 headers=H)
    assert res.status_code == 409 and res.json()["status"] == "UNSUPPORTED"
    audit = c.get("/api/audit?classification=UNSUPPORTED").json()
    assert audit and audit[0]["action"] == "live_write_request"
    feas = c.get("/api/feasibility").json()
    assert feas["registered_capabilities"] == [] and all(m["status"] == "UNSUPPORTED" for m in feas["matrix"])


def test_public_site_endpoint_serves_only_real_export(client):
    c, _, _ = client
    data = c.get("/site/profile.json").json()
    assert data["data_classification"] == "REAL" and data["stats"]["kills"]["value"] == 1000


def test_insecure_dev_refused_in_production(panel):
    panel.settings.environment = "production"
    with pytest.raises(RuntimeError):
        create_app(panel, insecure_dev=True)


def test_environments_are_separated(tmp_path):
    from blpanel.config import Settings
    a = Settings(environment="test", data_dir=tmp_path)
    b = Settings(environment="production", data_dir=tmp_path)
    assert a.db_path != b.db_path and a.export_dir != b.export_dir
