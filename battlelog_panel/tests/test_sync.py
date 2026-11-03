import json
import urllib.error

from blpanel.connector import BattlelogConnector
from blpanel.server import Panel
from conftest import FIX, URLS, FakeTransport


def test_sync_stores_snapshot_and_export(synced):
    panel, r = synced
    snap = panel.storage.snapshot(r["snapshot_id"])
    assert snap["source_kind"] == "battlelog-live"
    assert len(snap["raw_sha256"]) == 64
    export = json.loads((panel.settings.export_dir / "profile.json").read_text())
    assert export["data_classification"] == "REAL"
    assert export["profile"]["persona_name"] == "FixtureSoldier"
    assert export["stats"]["kills"]["value"] == 1000
    assert export["dogtags"]["counts"] == {"UNLOCKED": 2, "LOCKED": 2, "UNKNOWN": 1}
    assert r["changes"]["first_snapshot"] is True
    st = panel.sync.status()
    assert st["connection"] == "ok" and st["pending"] is False


def test_second_sync_diff(panel, transport):
    panel.sync.sync_profile("111111111", "pc", "FixtureSoldier")
    body = json.loads((FIX / "overview.json").read_text())
    body["data"]["overviewStats"].update(kills=1010, kdRatio=2.02)
    transport.routes["https://battlelog.battlefield.com/bf4/warsawoverviewpopulate/111111111/1/"] = (
        200, json.dumps(body).encode())
    r = panel.sync.sync_profile("111111111", "pc", "FixtureSoldier")
    changes = {c["key"]: c for c in r["changes"]["stats"]}
    assert changes["kills"]["delta"] == 10
    assert changes["kd_ratio"]["after"] == 2.02
    assert len(json.loads((panel.settings.export_dir / "profile.json").read_text())["history"]) == 2


def test_blocked_network_is_an_error_not_fake_data(settings):
    t = FakeTransport(exc=urllib.error.URLError("Tunnel connection failed: 403 Forbidden"))
    panel = Panel(settings, connector=BattlelogConnector(settings, t))
    r = panel.sync.sync_profile("111111111", "pc")
    assert r["status"] == "error" and r["error"]["kind"] == "network_blocked"
    assert panel.storage.query("SELECT COUNT(*) AS n FROM snapshots")[0]["n"] == 0
    assert not (settings.export_dir / "profile.json").exists()
    st = panel.sync.status()
    assert st["pending"] is True and st["connection"] == "error"
    a = panel.audit.entries()[0]
    assert a["classification"] == "SYNC" and a["outcome"] == "error"


def test_import_har(panel, tmp_path):
    entries = []
    for url, f in URLS.items():
        entries.append({"startedDateTime": "2026-02-02T10:00:00Z", "request": {"url": url},
                        "response": {"status": 200, "content": {"text": (FIX / f).read_text()}}})
    p = tmp_path / "x.har"
    p.write_text(json.dumps({"log": {"entries": entries}}))
    r = panel.sync.import_capture(p)
    assert r["status"] == "ok", r
    snap = panel.storage.snapshot(r["snapshot_id"])
    assert snap["source_kind"] == "manual-import"
    assert snap["captured_at"] == "2026-02-02T10:00:00Z"
    prof = panel.storage.one("SELECT * FROM profiles WHERE id=?", (r["profile_id"],))
    assert prof["persona_name"] == "FixtureSoldier" and prof["user_id"] == "900000000000000001"
    assert panel.sync.status()["connection"] == "offline-import-only"


def test_import_rejects_unrelated_file(panel, tmp_path):
    p = tmp_path / "x.har"
    p.write_text(json.dumps({"log": {"entries": []}}))
    assert panel.sync.import_capture(p)["status"] == "error"


def test_sync_makes_only_get_requests_without_credentials(synced, transport):
    assert transport.calls
    for c in transport.calls:
        assert {k.lower() for k in c["headers"]}.isdisjoint({"cookie", "authorization"})


def test_raw_responses_are_redacted(panel, transport):
    body = json.loads((FIX / "overview.json").read_text())
    body["data"]["email"] = "someone@example.com"
    transport.routes["https://battlelog.battlefield.com/bf4/warsawoverviewpopulate/111111111/1/"] = (
        200, json.dumps(body).encode())
    panel.sync.sync_profile("111111111", "pc", "FixtureSoldier")
    raw = panel.storage.query("SELECT raw_json FROM snapshots")[0]["raw_json"]
    assert "someone@example.com" not in raw
