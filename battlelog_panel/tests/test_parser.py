import hashlib
import json

from blpanel.connector import RawResponse
from blpanel.parser import ProfileParser
from conftest import fixture_json

URL = "https://battlelog.battlefield.com/bf4/warsaw{}populate/111111111/1/"
URL_NAMES = {"overview": "overview", "detailed": "detailedstats", "dogtags": "dogtags"}


def raw(resource, name, url=None):
    body = fixture_json(name)
    return RawResponse(resource, url or URL.format(URL_NAMES.get(resource, resource)), 200, "2026-01-01T00:00:00+00:00",
                       hashlib.sha256(json.dumps(body).encode()).hexdigest(), "battlelog-live", body)


def test_identity():
    ident = ProfileParser.parse_identity(raw("identity", "identity.json", "https://x/bf4/user/FixtureUser/"))
    assert ident["user"] == {"user_id": "900000000000000001", "username": "FixtureUser"}
    assert [(s["persona_id"], s["platform"]) for s in ident["soldiers"]] == [("111111111", "pc"), ("222222222", "ps4")]


def test_stats_match_source_values_exactly():
    ov = fixture_json("overview.json")["data"]["overviewStats"]
    det = fixture_json("detailed.json")["data"]["generalStats"]
    p = ProfileParser().parse_profile([raw("overview", "overview.json"), raw("detailed", "detailed.json")])
    s = p["stats"]
    assert s["kills"]["value"] == ov["kills"] and s["kills"]["origin"] == "source"
    assert s["kills"]["source"] == "overview:data.overviewStats.kills"
    assert s["kd_ratio"]["value"] == ov["kdRatio"] and s["kd_ratio"]["origin"] == "source"
    assert s["headshots"]["value"] == det["headshots"]
    assert s["time_played"]["value"] == ov["timePlayed"]
    assert s["rounds"]["value"] == det["numRounds"]
    # derived only where no source value exists
    assert s["wl_ratio"]["origin"] == "derived" and s["wl_ratio"]["value"] == 1.5
    assert s["headshot_rate"]["value"] == 25.0
    assert s["kills_per_minute"]["value"] == round(1000 / 600, 4)


def test_missing_is_none_not_zero():
    p = ProfileParser().parse_profile([raw("overview", "overview.json")])
    assert p["stats"]["headshots"] == {"value": None, "unit": "count", "origin": "missing", "source": None}
    assert p["stats"]["headshot_rate"]["value"] is None


def test_extra_numeric_fields_are_kept_and_strings_ignored():
    p = ProfileParser().parse_profile([raw("overview", "overview.json")])
    assert "overviewStats.dogtagsTaken" in p["extra_stats"]
    assert "overviewStats.flagCaptures" not in p["extra_stats"]


def test_dogtag_status_only_from_explicit_fields():
    tags = {t["id"]: t for t in ProfileParser().parse_dogtags(raw("dogtags", "dogtags.json"))}
    assert tags["fx-basic-1"]["status"] == "UNLOCKED"
    assert tags["fx-basic-2"]["status"] == "LOCKED" and tags["fx-basic-2"]["requirements"] == "Rank 99"
    assert tags["fx-adv-1"]["status"] == "UNKNOWN"         # no ownership field -> never "unlocked"
    assert tags["fx-evt-1"]["status"] == "UNLOCKED"        # unlock date present
    assert tags["fx-adv-1"]["source_category"] == "advanced"


def test_no_dogtag_response_means_none():
    p = ProfileParser().parse_profile([raw("overview", "overview.json")])
    assert p["dogtags"] is None
    assert p["identity"]["persona_id"] == "111111111" and p["identity"]["platform"] == "pc"
