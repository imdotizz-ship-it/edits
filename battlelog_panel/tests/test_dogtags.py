from blpanel.dogtags import DEV_CATEGORY, DogtagManager

TAGS = [
    {"id": "a", "name": "Plain", "description": None, "source_category": "basic", "status": "UNLOCKED"},
    {"id": "b", "name": "DICE Tag", "description": "for developers", "source_category": "advanced", "status": "LOCKED"},
    {"id": "c", "name": "Mystery", "description": None, "source_category": None, "status": "UNKNOWN"},
    {"id": "d", "name": "Premium tag", "description": None, "source_category": "advanced", "status": "UNKNOWN"},
]


def test_dev_heuristic_does_not_change_status():
    m = DogtagManager(annotations={})
    b = m.classify(TAGS[1])
    assert b["category"] == DEV_CATEGORY and b["category_basis"].startswith("heuristic")
    assert b["status"] == "LOCKED"


def test_annotation_takes_precedence():
    m = DogtagManager(annotations={"c": {"id": "c", "category": DEV_CATEGORY, "unlock_source": "granted by EA"}})
    c = m.classify(TAGS[2])
    assert c["category"] == DEV_CATEGORY and c["category_basis"] == "annotation"
    assert c["unlock_source"] == "granted by EA" and c["status"] == "UNKNOWN"


def test_catalogue_filters_and_counts():
    m = DogtagManager(annotations={})
    cat = m.catalogue(TAGS, status="unknown")
    assert {t["id"] for t in cat["items"]} == {"c", "d"}
    assert cat["counts"] == {"UNLOCKED": 1, "LOCKED": 1, "UNKNOWN": 2}
    assert DEV_CATEGORY in cat["categories"]
    assert [t["id"] for t in m.catalogue(TAGS, query="dice")["items"]] == ["b"]
    assert [t["id"] for t in m.catalogue(TAGS, category=DEV_CATEGORY)["items"]] == ["b"]


def test_no_data():
    cat = DogtagManager(annotations={}).catalogue(None)
    assert cat["available"] is False and cat["items"] == []
