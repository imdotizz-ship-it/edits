"""DogtagManager: catalogue, categories, search and filters.

Ownership status is copied verbatim from the parser (UNLOCKED / LOCKED /
UNKNOWN). Nothing here can change it: categorisation only adds labels.

Category "Developer / Exclusive" is assigned either from your curated
annotation file (basis: "annotation") or by a keyword heuristic over the
source name/description/category (basis: "heuristic: ..."), and the basis is
always shown so a guess is never presented as a fact.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

DEV_CATEGORY = "Developer / Exclusive"
DEV_KEYWORDS = re.compile(
    r"\b(dev|devs|developer|developers|dice|staff|moderator|admin|exclusive|tester|"
    r"community manager|employee|ea staff)\b", re.IGNORECASE)
CATEGORY_RULES = [
    (re.compile(r"\b(premium|dlc|expansion)\b", re.I), "Premium / DLC"),
    (re.compile(r"\b(pre-?order|bonus|promo|promotion|retail)\b", re.I), "Promoção / Pré-venda"),
    (re.compile(r"\b(event|community mission|mission|challenge)\b", re.I), "Eventos / Missões"),
    (re.compile(r"\b(assignment|award|ribbon|medal|service star)\b", re.I), "Conquistas"),
    (re.compile(r"\b(weapon|kills? with|vehicle)\b", re.I), "Armas / Veículos"),
]
DEFAULT_ANNOTATIONS = Path(__file__).resolve().parent.parent / "data" / "dogtag_annotations.json"


def load_annotations(path: Path | None = None) -> dict[str, dict]:
    p = Path(path) if path else DEFAULT_ANNOTATIONS
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {str(e["id"]): e for e in data.get("entries", []) if "id" in e}


class DogtagManager:
    def __init__(self, annotations: dict[str, dict] | None = None):
        self.annotations = load_annotations() if annotations is None else annotations

    def classify(self, tag: dict) -> dict:
        out = dict(tag)
        ann = self.annotations.get(str(tag.get("id")))
        if ann:
            out["category"] = ann.get("category") or out.get("source_category") or "Sem categoria"
            out["category_basis"] = "annotation"
            for k in ("unlock_source", "requirements", "notes"):
                if ann.get(k) and not out.get(k):
                    out[k] = ann[k]
            return out
        text = " ".join(str(tag.get(k) or "") for k in ("name", "description", "source_category"))
        m = DEV_KEYWORDS.search(text)
        if m:
            out["category"], out["category_basis"] = DEV_CATEGORY, f"heuristic: keyword {m.group(0)!r}"
            return out
        for pat, label in CATEGORY_RULES:
            m = pat.search(text)
            if m:
                out["category"], out["category_basis"] = label, f"heuristic: keyword {m.group(0)!r}"
                return out
        out["category"] = tag.get("source_category") or "Sem categoria"
        out["category_basis"] = "source" if tag.get("source_category") else "none"
        return out

    def catalogue(self, tags: list[dict] | None, query: str = "", status: str = "", category: str = "") -> dict:
        if tags is None:
            return {"available": False, "reason": "No dogtag data was obtained from any source for this snapshot.",
                    "items": [], "counts": {}, "categories": []}
        items = [self.classify(t) for t in tags]
        q = query.strip().lower()
        if q:
            items = [t for t in items if q in (t["name"] or "").lower() or q in (t.get("description") or "").lower()
                     or q in t["id"].lower()]
        if status:
            items = [t for t in items if t["status"] == status.upper()]
        if category:
            items = [t for t in items if t["category"] == category]
        all_classified = [self.classify(t) for t in tags]
        counts = {s: sum(1 for t in all_classified if t["status"] == s) for s in ("UNLOCKED", "LOCKED", "UNKNOWN")}
        categories = sorted({t["category"] for t in all_classified} | {DEV_CATEGORY})
        return {"available": True, "items": items, "counts": counts, "categories": categories,
                "total": len(all_classified)}
