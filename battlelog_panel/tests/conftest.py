import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from blpanel.config import Settings  # noqa: E402
from blpanel.connector import BattlelogConnector  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
BASE = "https://battlelog.battlefield.com"
URLS = {
    f"{BASE}/bf4/user/FixtureUser/": "identity.json",
    f"{BASE}/bf4/warsawoverviewpopulate/111111111/1/": "overview.json",
    f"{BASE}/bf4/warsawdetailedstatspopulate/111111111/1/": "detailed.json",
    f"{BASE}/bf4/soldier/FixtureSoldier/dogtags/111111111/1/": "dogtags.json",
}


def fixture_json(name: str) -> dict:
    return json.loads((FIX / name).read_text())


class FakeTransport:
    """Offline transport for tests. Records every request it receives."""

    def __init__(self, routes=None, exc=None):
        self.routes = dict(routes if routes is not None else
                           {u: (200, (FIX / f).read_bytes()) for u, f in URLS.items()})
        self.exc = exc
        self.calls = []

    def get(self, url, headers, timeout):
        self.calls.append({"url": url, "headers": dict(headers)})
        if self.exc:
            raise self.exc
        if url not in self.routes:
            return 404, b"<html>not found</html>"
        return self.routes[url]


@pytest.fixture
def settings(tmp_path):
    return Settings(environment="test", data_dir=tmp_path, min_request_interval=0)


@pytest.fixture
def transport():
    return FakeTransport()


@pytest.fixture
def connector(settings, transport):
    return BattlelogConnector(settings, transport)


@pytest.fixture
def panel(settings, connector):
    from blpanel.dogtags import DogtagManager
    from blpanel.server import Panel
    return Panel(settings, connector=connector, dogtags=DogtagManager(annotations={}))


@pytest.fixture
def synced(panel):
    r = panel.sync.sync_profile("111111111", "pc", "FixtureSoldier", "900000000000000001")
    assert r["status"] == "ok", r
    return panel, r
