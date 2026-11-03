"""ProfileParser: turns raw Battlelog responses into one normalized profile.

The parser is deliberately tolerant about *where* a structure sits (it walks
the JSON looking for the named containers) and strict about *what* it claims:
an absent field is reported as missing, never as zero, and dogtag ownership is
UNKNOWN unless the source carries an explicit ownership flag.
"""
from __future__ import annotations

import re
from typing import Any, Iterator

from .connector import RawResponse, persona_from_url
from .platforms import platform_name
from .statistics import MAPPED_FIELDS, STATS, StatisticsManager

SCHEMA = "blpanel.profile/1"
OWNERSHIP_TRUE_KEYS = ("unlocked", "isUnlocked", "owned", "isOwned", "hasUnlocked", "isAwarded", "awarded")
OWNERSHIP_DATE_KEYS = ("unlockedAt", "unlockDate", "dateUnlocked", "awardedAt")
DOGTAG_ID_KEYS = ("dogtagId", "id", "guid", "key", "unlockId", "code")
DOGTAG_NAME_KEYS = ("name", "title", "label", "nameSID", "stringID", "stringId")
DOGTAG_DESC_KEYS = ("description", "desc", "descriptionSID", "unlockDescription")
DOGTAG_REQ_KEYS = ("requirement", "requirements", "criteria", "unlockCriteria", "progress")


def walk(obj: Any, path: str = "") -> Iterator[tuple[str, Any]]:
    yield path, obj
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, f"{path}[{i}]")


def find_container(obj: Any, name: str) -> tuple[str, dict] | None:
    for path, value in walk(obj):
        if isinstance(value, dict) and name in value and isinstance(value[name], dict):
            return (f"{path}.{name}" if path else name), value[name]
    return None


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _first(d: dict, keys: tuple[str, ...]) -> tuple[str | None, Any]:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return k, d[k]
    return None, None


class ProfileParser:
    def __init__(self, stats: StatisticsManager | None = None):
        self.stats = stats or StatisticsManager()

    # ---- identity ----------------------------------------------------------------
    @staticmethod
    def parse_identity(raw: RawResponse) -> dict:
        """Extract userId/username and every soldier (persona) on the user page."""
        body = raw.body
        user = {}
        for _, value in walk(body):
            if isinstance(value, dict) and "userId" in value and "username" in value:
                user = {"user_id": str(value["userId"]), "username": value.get("username")}
                break
        soldiers, seen = [], set()
        for _, value in walk(body):
            if not isinstance(value, dict):
                continue
            persona = value.get("persona") if isinstance(value.get("persona"), dict) else None
            if persona and "personaId" in persona:
                pid = str(persona["personaId"])
                plat = platform_name(value.get("platform", persona.get("platform")))
                key = (pid, plat)
                if key in seen:
                    continue
                seen.add(key)
                soldiers.append({
                    "persona_id": pid,
                    "persona_name": persona.get("personaName"),
                    "platform": plat,
                    "platform_raw": value.get("platform"),
                    "namespace": persona.get("namespace"),
                    "game_raw": value.get("game"),
                })
        return {"user": user, "soldiers": soldiers, "source": raw.meta()}

    # ---- stats -------------------------------------------------------------------
    def _containers(self, responses: list[RawResponse]) -> dict[str, list[tuple[str, str, dict, RawResponse]]]:
        found: dict[str, list] = {}
        for r in responses:
            for cname in ("overviewStats", "generalStats"):
                hit = find_container(r.body, cname)
                if hit:
                    found.setdefault(cname, []).append((r.resource, hit[0], hit[1], r))
        return found

    def parse_stats(self, responses: list[RawResponse]) -> tuple[dict, dict]:
        containers = self._containers(responses)
        stats: dict[str, dict] = {}
        for d in STATS:
            entry = None
            for cname, fieldname in d.sources:
                for resource, path, container, r in containers.get(cname, []):
                    if fieldname in container and _is_number(container[fieldname]):
                        entry = {"value": container[fieldname], "unit": d.unit, "origin": "source",
                                 "source": f"{resource}:{path}.{fieldname}", "fetched_at": r.fetched_at,
                                 "source_kind": r.source_kind}
                        break
                if entry:
                    break
            stats[d.key] = entry or {"value": None, "unit": d.unit, "origin": "missing", "source": None}
        self.stats.apply_derivations(stats)
        # "Other available stats": every numeric scalar we did not map.
        extra: dict[str, dict] = {}
        for cname, hits in containers.items():
            for resource, path, container, r in hits:
                for k, v in container.items():
                    if _is_number(v) and k not in MAPPED_FIELDS:
                        extra.setdefault(f"{cname}.{k}", {"value": v, "source": f"{resource}:{path}.{k}",
                                                          "fetched_at": r.fetched_at})
        return stats, extra

    # ---- dogtags -----------------------------------------------------------------
    @staticmethod
    def _looks_like_dogtag(d: dict) -> bool:
        return _first(d, DOGTAG_ID_KEYS)[0] is not None and _first(d, DOGTAG_NAME_KEYS)[0] is not None

    def parse_dogtags(self, raw: RawResponse) -> list[dict]:
        tags, seen = [], set()
        for path, value in walk(raw.body):
            if not (isinstance(value, list) and value and all(isinstance(x, dict) for x in value)):
                continue
            if not re.search(r"dogtag|tag", path, re.I):
                continue
            if not all(self._looks_like_dogtag(x) for x in value):
                continue
            group = re.sub(r"\[\d+\]", "", path).split(".")[-1]
            for i, item in enumerate(value):
                _, tid = _first(item, DOGTAG_ID_KEYS)
                tid = str(tid)
                if tid in seen:
                    continue
                seen.add(tid)
                _, name = _first(item, DOGTAG_NAME_KEYS)
                _, desc = _first(item, DOGTAG_DESC_KEYS)
                _, req = _first(item, DOGTAG_REQ_KEYS)
                status, basis = "UNKNOWN", "no ownership field in source"
                own_key, own_val = _first(item, OWNERSHIP_TRUE_KEYS)
                date_key, date_val = _first(item, OWNERSHIP_DATE_KEYS)
                if own_key is not None and isinstance(own_val, bool):
                    status, basis = ("UNLOCKED" if own_val else "LOCKED"), f"source field {own_key}={own_val}"
                elif date_key is not None:
                    status, basis = "UNLOCKED", f"source field {date_key} present"
                tags.append({
                    "id": tid,
                    "name": str(name),
                    "description": desc if isinstance(desc, str) else None,
                    "requirements": req if isinstance(req, (str, int, float, dict, list)) else None,
                    "source_category": item.get("category") or item.get("type") or group,
                    "status": status,
                    "status_basis": basis,
                    "image": item.get("image") or item.get("imageUrl") or item.get("imageConfig"),
                    "source": f"dogtags:{path}[{i}]",
                    "fetched_at": raw.fetched_at,
                })
        return tags

    # ---- whole profile ---------------------------------------------------------------
    def parse_profile(self, responses: list[RawResponse], identity: dict | None = None) -> dict:
        if not any(r.resource in ("overview", "detailed") for r in responses):
            raise ValueError("no overview/detailed stats response to parse")
        stats, extra = self.parse_stats(responses)
        dogtag_responses = [r for r in responses if r.resource == "dogtags"]
        dogtags = None
        if dogtag_responses:
            dogtags = []
            for r in dogtag_responses:
                dogtags.extend(self.parse_dogtags(r))
        ident = dict(identity or {})
        if not ident.get("persona_id"):
            for r in responses:
                pid, plat = persona_from_url(r.url)
                if pid:
                    ident.setdefault("persona_id", pid)
                    ident.setdefault("platform", plat)
                    break
        ident.setdefault("persona_id", None)
        ident.setdefault("persona_name", None)
        ident.setdefault("user_id", None)
        ident.setdefault("platform", None)
        return {
            "schema": SCHEMA,
            "identity": ident,
            "stats": stats,
            "extra_stats": extra,
            "dogtags": dogtags,                       # None = no dogtag data was obtained
            "sources": [r.meta() for r in responses],
        }
