import csv
import io
import json

import pytest

from blpanel.modification import LabError, WATERMARK


@pytest.fixture
def lab(synced):
    panel, r = synced
    sid = panel.lab.create("t", r["snapshot_id"])
    return panel, r, sid


def stat(ev, side, key):
    return ev[side]["stats"][key]


def test_set_kills_recomputes_kd_and_is_marked_simulated(lab):
    panel, _, sid = lab
    panel.lab.add_modification(sid, "stat", "kills", "set", 1500)
    ev = panel.lab.evaluate(sid)
    assert ev["mode"] == "SIMULATED" and ev["modified"]["mode"] == "SIMULATED"
    assert stat(ev, "modified", "kills") ["value"] == 1500 and stat(ev, "modified", "kills")["origin"] == "simulated"
    assert stat(ev, "modified", "kd_ratio")["value"] == 3.0
    assert stat(ev, "modified", "kd_ratio")["origin"] == "simulated-derived"
    assert stat(ev, "original", "kills")["value"] == 1000 and stat(ev, "original", "kills")["origin"] == "source"
    keys = {c["key"] for c in ev["diff"]}
    assert {"kills", "kd_ratio", "headshot_rate", "kills_per_minute"} <= keys
    assert "deaths" not in keys


def test_kd_adjust_kills_strategy(lab):
    panel, _, sid = lab
    panel.lab.add_modification(sid, "stat", "kd_ratio", "set", 4.0)
    ev = panel.lab.evaluate(sid)
    assert stat(ev, "modified", "kills")["value"] == 2000
    assert stat(ev, "modified", "kd_ratio")["value"] == 4.0
    assert ev["warnings"] == []


def test_kd_override_flags_inconsistency(lab):
    panel, _, sid = lab
    panel.lab.add_modification(sid, "stat", "kd_ratio", "set", 9.0, strategy="override")
    ev = panel.lab.evaluate(sid)
    assert stat(ev, "modified", "kd_ratio")["value"] == 9.0
    assert any("inconsistent" in w for w in ev["warnings"])


def test_add_multiply_and_clamp(lab):
    panel, _, sid = lab
    panel.lab.add_modification(sid, "stat", "deaths", "add", -10000)
    panel.lab.add_modification(sid, "stat", "skill", "multiply", 1.5)
    ev = panel.lab.evaluate(sid)
    assert stat(ev, "modified", "deaths")["value"] == 0
    assert stat(ev, "modified", "kd_ratio")["value"] is None   # division by zero -> missing, never inf
    assert stat(ev, "modified", "skill")["value"] == 450
    assert any("clamped" in w for w in ev["warnings"])


def test_invalid_modifications(lab):
    panel, _, sid = lab
    for args in [("stat", "nope", "set", 1), ("stat", "kills", "pow", 1), ("stat", "kills", "set", "x"),
                 ("stat", "headshot_rate", "set", 1), ("dogtag", "missing", "unlock", None),
                 ("dogtag", "fx-adv-1", "set", None), ("x", "kills", "set", 1)]:
        with pytest.raises(LabError):
            panel.lab.add_modification(sid, *args)


def test_dogtag_unlock_simulation(lab):
    panel, _, sid = lab
    m = panel.lab.add_modification(sid, "dogtag", "fx-dev-1", "unlock")
    assert m["lab_status"] == "SIMULATED" and m["live_status"] == "UNSUPPORTED"
    ev = panel.lab.evaluate(sid)
    orig = {t["id"]: t for t in ev["original"]["dogtags"]}
    mod = {t["id"]: t for t in ev["modified"]["dogtags"]}
    assert orig["fx-dev-1"]["status"] == "LOCKED"
    assert mod["fx-dev-1"]["status"] == "UNLOCKED" and mod["fx-dev-1"]["simulated"] is True
    assert ev["dogtag_diff"] == [{"id": "fx-dev-1", "name": "Fixture DICE Tag", "before": "LOCKED",
                                  "after": "UNLOCKED", "after_origin": "simulated"}]


def test_every_live_status_is_unsupported(lab):
    panel, _, sid = lab
    m = panel.lab.add_modification(sid, "stat", "kills", "set", 5)
    assert m["live_status"] == "UNSUPPORTED" and "telemetry" in m["live_reason"]


def test_restore_and_remove(lab):
    panel, _, sid = lab
    m = panel.lab.add_modification(sid, "stat", "kills", "set", 5)
    panel.lab.add_modification(sid, "stat", "deaths", "set", 5)
    panel.lab.remove_modification(sid, m["id"])
    assert [x["target"] for x in panel.lab.evaluate(sid)["modifications"]] == ["deaths"]
    panel.lab.restore(sid)
    ev = panel.lab.evaluate(sid)
    assert ev["modifications"] == [] and ev["diff"] == []


def test_export_is_watermarked(lab):
    panel, _, sid = lab
    panel.lab.add_modification(sid, "stat", "kills", "set", 1234)
    text, media = panel.lab.export(sid, "csv")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0][0] == f"# {WATERMARK}" and media == "text/csv"
    assert any(r[:4] == ["kills", "Kills", "1000", "1234"] for r in rows)
    j = json.loads(panel.lab.export(sid, "json")[0])
    assert j["mode"] == "SIMULATED" and j["watermark"] == WATERMARK


def test_lab_never_touches_real_data(lab):
    """Isolation: snapshots, profile export and sync history are byte-identical after lab activity."""
    panel, r, sid = lab
    export = panel.settings.export_dir / "profile.json"
    before = (panel.storage.snapshot_digest_table(), export.read_bytes(),
              panel.storage.query("SELECT * FROM sync_runs"))
    panel.lab.add_modification(sid, "stat", "kills", "set", 999999)
    panel.lab.add_modification(sid, "stat", "kd_ratio", "set", 50, strategy="override")
    panel.lab.add_modification(sid, "dogtag", "fx-dev-1", "unlock")
    panel.lab.evaluate(sid)
    panel.lab.export(sid, "csv")
    panel.lab.restore(sid)
    after = (panel.storage.snapshot_digest_table(), export.read_bytes(),
             panel.storage.query("SELECT * FROM sync_runs"))
    assert before == after
    cur = panel.storage.latest_snapshots(r["profile_id"], 1)[0]["normalized"]
    assert cur["stats"]["kills"]["value"] == 1000
    assert {t["id"]: t["status"] for t in cur["dogtags"]}["fx-dev-1"] == "LOCKED"
    assert "SIMULATED" not in export.read_text()
