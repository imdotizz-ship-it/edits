import json

import pytest
from starlette.testclient import TestClient

from blpanel.server import create_app
from blpanel.showcase import DISCLOSURE


@pytest.fixture
def scenario(synced):
    panel, r = synced
    sid = panel.lab.create("Meu build", r["snapshot_id"])
    panel.lab.add_modification(sid, "stat", "kills", "set", 5000)
    panel.lab.add_modification(sid, "dogtag", "fx-dev-1", "unlock")
    return panel, sid


def test_showcase_is_labelled_and_separate(scenario):
    panel, sid = scenario
    real = panel.settings.export_dir / "profile.json"
    before = real.read_bytes()
    data = panel.showcase.publish(sid, "Build dos sonhos")
    assert data["data_classification"] == "SIMULATED" and data["disclosure"] == DISCLOSURE
    assert data["stats"]["kills"] == {"value": 5000, "unit": "count", "custom": True, "real_value": 1000}
    assert data["stats"]["deaths"]["custom"] is False
    assert data["custom_dogtags"] == [{"id": "fx-dev-1", "name": "Fixture DICE Tag", "status": "UNLOCKED",
                                       "custom": True}]
    on_disk = json.loads(panel.showcase.path.read_text())
    assert on_disk["title"] == "Build dos sonhos"
    assert real.read_bytes() == before                       # real export untouched
    assert json.loads(before)["data_classification"] == "REAL"
    assert panel.audit.entries()[0]["action"] == "publish_showcase"


def test_empty_scenario_cannot_be_published(synced):
    panel, r = synced
    sid = panel.lab.create("vazio", r["snapshot_id"])
    with pytest.raises(ValueError):
        panel.showcase.publish(sid)


def test_unpublish(scenario):
    panel, sid = scenario
    panel.showcase.publish(sid)
    assert panel.showcase.unpublish() is True
    assert panel.showcase.current() is None


def test_public_endpoints(scenario):
    panel, sid = scenario
    c = TestClient(create_app(panel, insecure_dev=True))
    assert c.get("/site/showcase.json").status_code == 404
    assert c.post(f"/api/lab/scenarios/{sid}/publish", json={}, headers={"X-Panel-Request": "1"}).status_code == 200
    pub = c.get("/site/showcase.json").json()
    assert pub["data_classification"] == "SIMULATED" and pub["disclosure"] == DISCLOSURE
    assert c.get("/site/profile.json").json()["data_classification"] == "REAL"
