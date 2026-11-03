import base64
import json
import urllib.error

import pytest

from blpanel.connector import BattlelogConnector, ConnectorError, load_har, persona_from_url
from conftest import FIX, FakeTransport


def test_urls_and_headers(connector, transport):
    connector.fetch_overview("111111111", "pc")
    call = transport.calls[0]
    assert call["url"] == "https://battlelog.battlefield.com/bf4/warsawoverviewpopulate/111111111/1/"
    lowered = {k.lower() for k in call["headers"]}
    assert "cookie" not in lowered and "authorization" not in lowered
    assert call["headers"]["X-AjaxNavigation"] == "1"


def test_transport_has_no_write_method():
    # The connector API exposes only fetch_* / get_json, all GET.
    public = [n for n in dir(BattlelogConnector) if not n.startswith("_")]
    assert not [n for n in public if any(w in n for w in ("post", "put", "delete", "save", "write", "update"))]


def test_credential_headers_rejected(connector):
    with pytest.raises(ConnectorError) as e:
        connector._headers({"Cookie": "beaker.session.id=abc"})
    assert e.value.kind == "invalid_input"


def test_platform_and_input_validation(connector):
    with pytest.raises(ValueError):
        connector.fetch_overview("111111111", "switch")
    with pytest.raises(ConnectorError):
        connector.fetch_overview("12; DROP", "pc")
    with pytest.raises(ConnectorError):
        connector.fetch_identity("../../etc")


def test_network_blocked_is_classified(settings):
    exc = urllib.error.URLError("Tunnel connection failed: 403 Forbidden")
    c = BattlelogConnector(settings, FakeTransport(exc=exc))
    with pytest.raises(ConnectorError) as e:
        c.fetch_overview("1", "pc")
    assert e.value.kind == "network_blocked"


@pytest.mark.parametrize("status,body,kind", [
    (404, b"", "not_found"),
    (500, b"", "http"),
    (200, b"<html>login</html>", "not_json"),
    (200, json.dumps({"type": "error", "message": "persona not found"}).encode(), "battlelog_error"),
])
def test_error_kinds(settings, status, body, kind):
    url = "https://battlelog.battlefield.com/bf4/warsawoverviewpopulate/1/1/"
    c = BattlelogConnector(settings, FakeTransport({url: (status, body)}))
    with pytest.raises(ConnectorError) as e:
        c.fetch_overview("1", "pc")
    assert e.value.kind == kind


def test_throttle(settings):
    settings.min_request_interval = 2.0
    now = [100.0]
    slept = []
    c = BattlelogConnector(settings, FakeTransport(), clock=lambda: now[0], sleep=slept.append)
    c.fetch_overview("111111111", "pc")
    c.fetch_detailed("111111111", "pc")
    assert slept == [2.0]


def test_partial_failure_is_reported(settings):
    t = FakeTransport()
    del t.routes["https://battlelog.battlefield.com/bf4/warsawdetailedstatspopulate/111111111/1/"]
    responses, errors = BattlelogConnector(settings, t).fetch_profile("111111111", "pc", "FixtureSoldier")
    assert [r.resource for r in responses] == ["overview", "dogtags"]
    assert errors[0]["resource"] == "detailed" and errors[0]["kind"] == "not_found"


def test_har_import(tmp_path):
    overview = (FIX / "overview.json").read_text()
    har = {"log": {"entries": [
        {"startedDateTime": "2026-01-01T00:00:00Z",
         "request": {"url": "https://battlelog.battlefield.com/bf4/warsawoverviewpopulate/111111111/1/",
                     "headers": [{"name": "Cookie", "value": "beaker.session.id=SECRET"}]},
         "response": {"status": 200, "content": {"text": base64.b64encode(overview.encode()).decode(),
                                                 "encoding": "base64"}}},
        {"request": {"url": "https://evil.example/bf4/warsawoverviewpopulate/1/1/"},
         "response": {"status": 200, "content": {"text": overview}}},
        {"request": {"url": "https://battlelog.battlefield.com/bf4/user/FixtureUser/"},
         "response": {"status": 200, "content": {"text": "<html>page</html>"}}},
    ]}}
    p = tmp_path / "c.har"
    p.write_text(json.dumps(har))
    out = load_har(p)
    assert len(out) == 1
    assert out[0].resource == "overview" and out[0].source_kind == "manual-import"
    assert "SECRET" not in json.dumps(out[0].meta()) + json.dumps(out[0].body)


def test_persona_from_url():
    assert persona_from_url("https://battlelog.battlefield.com/bf4/warsawoverviewpopulate/42/32/") == ("42", "ps4")
