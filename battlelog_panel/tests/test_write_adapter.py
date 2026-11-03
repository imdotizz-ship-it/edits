"""The shipped registry is empty. The test-only capability below exists solely to
exercise the safety gates; it is never registered by the application."""
from blpanel.audit import AuditLogger
from blpanel.storage import Storage
from blpanel.write_adapter import OFFICIAL_CAPABILITIES, AuthorizedWriteAdapter, WriteCapability


def make(settings, registry=None):
    audit = AuditLogger(Storage(":memory:"))
    return AuthorizedWriteAdapter(settings, audit, registry), audit


def test_shipped_registry_is_empty_and_everything_unsupported(settings):
    assert OFFICIAL_CAPABILITIES == {}
    settings.writes_enabled = True  # even with the flag on
    w, audit = make(settings)
    for row in w.matrix():
        assert row["status"] == "UNSUPPORTED" and row["reason"]
    r = w.execute("stat", "kills", {"op": "set", "value": 1}, "CONFIRM anything", "admin@panel")
    assert r["status"] == "UNSUPPORTED"
    e = audit.entries()[0]
    assert e["classification"] == "UNSUPPORTED" and e["outcome"] == "refused"


def _cap(executor, envs=("test",)):
    return {"t": WriteCapability("t", "stat", "test-only", "https://example.invalid/doc", envs, executor)}


def test_gates(settings):
    calls = []
    good = _cap(lambda p: calls.append(p) or {"confirmed": True, "receipt": "r1"})
    w, _ = make(settings, good)
    assert w.assess("stat")["status"] == "UNSUPPORTED"          # writes disabled
    settings.writes_enabled = True
    w, _ = make(settings, _cap(good["t"].executor, envs=("production",)))
    assert "environment" in w.assess("stat")["reason"]          # wrong environment
    w, audit = make(settings, good)
    assert w.execute("stat", "kills", {}, "yes", "a")["status"] == "REFUSED"
    assert calls == []
    r = w.execute("stat", "kills", {"value": 1}, "CONFIRM t kills", "a")
    assert r["status"] == "AUTHORIZED_WRITE" and calls
    assert audit.entries()[0]["classification"] == "AUTHORIZED_WRITE"


def test_unconfirmed_write_is_error_not_success(settings):
    settings.writes_enabled = True
    w, audit = make(settings, _cap(lambda p: {"ok": True}))
    assert w.execute("stat", "kills", {}, "CONFIRM t kills", "a")["status"] == "ERROR"
    assert audit.entries()[0]["outcome"] == "error"
