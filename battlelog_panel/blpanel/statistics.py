"""StatisticsManager: stat catalogue, derived metrics and snapshot comparison.

Each stat lists candidate source paths, tried in order. Paths are
PRIOR_KNOWLEDGE_UNVERIFIED field names of the Battlelog BF4 JSON payloads; any
path that is not present simply yields `origin: "missing"` (never 0).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class StatDef:
    key: str
    label: str
    unit: str                      # count | ratio | percent | seconds | score | rank | meters
    sources: tuple[tuple[str, str], ...] = ()   # (container, field) e.g. ("overviewStats", "kills")
    derived: str | None = None     # name of derivation, used when no source value exists
    editable: bool = True          # can be changed in the lab
    group: str = "core"
    note: str = ""


STATS: tuple[StatDef, ...] = (
    StatDef("kills", "Kills", "count", (("overviewStats", "kills"), ("generalStats", "kills"))),
    StatDef("deaths", "Deaths", "count", (("overviewStats", "deaths"), ("generalStats", "deaths"))),
    StatDef("kd_ratio", "K/D", "ratio", (("overviewStats", "kdRatio"), ("generalStats", "kdRatio")),
            derived="kd_ratio"),
    StatDef("score", "Score", "score", (("overviewStats", "score"), ("generalStats", "score"))),
    StatDef("skill", "Skill", "score", (("overviewStats", "skill"), ("generalStats", "skill"))),
    StatDef("score_per_minute", "Score/min", "ratio",
            (("overviewStats", "scorePerMinute"), ("generalStats", "scorePerMinute"))),
    StatDef("wins", "Vitórias", "count", (("overviewStats", "numWins"), ("generalStats", "numWins"))),
    StatDef("losses", "Derrotas", "count", (("overviewStats", "numLosses"), ("generalStats", "numLosses"))),
    StatDef("wl_ratio", "W/L", "ratio", (("overviewStats", "wlRatio"), ("generalStats", "wlRatio")),
            derived="wl_ratio"),
    StatDef("headshots", "Headshots", "count", (("generalStats", "headshots"), ("overviewStats", "headshots"))),
    StatDef("headshot_rate", "Headshots/kills", "percent", (), derived="headshot_rate", editable=False),
    StatDef("shots_fired", "Disparos", "count", (("generalStats", "shotsFired"), ("overviewStats", "shotsFired"))),
    StatDef("shots_hit", "Acertos", "count", (("generalStats", "shotsHit"), ("overviewStats", "shotsHit"))),
    StatDef("accuracy", "Precisão", "percent", (("overviewStats", "accuracy"), ("generalStats", "accuracy")),
            derived="accuracy", note="Scale as reported by the source; verify with a real capture."),
    StatDef("time_played", "Tempo de jogo", "seconds",
            (("overviewStats", "timePlayed"), ("generalStats", "timePlayed"))),
    StatDef("rounds", "Partidas", "count", (("generalStats", "numRounds"), ("overviewStats", "numRounds")),
            derived="rounds"),
    StatDef("kills_per_minute", "Kills/min", "ratio", (), derived="kills_per_minute", editable=False),
    StatDef("rank", "Rank", "rank", (("overviewStats", "rank"), ("generalStats", "rank")), group="extra"),
    StatDef("kill_assists", "Assistências", "count",
            (("overviewStats", "killAssists"), ("generalStats", "killAssists")), group="extra"),
    StatDef("longest_headshot", "Headshot mais longo", "meters",
            (("generalStats", "longestHeadshot"), ("overviewStats", "longestHeadshot")), group="extra"),
    StatDef("revives", "Revives", "count", (("generalStats", "revives"), ("overviewStats", "revives")), group="extra"),
    StatDef("heals", "Heals", "count", (("generalStats", "heals"), ("overviewStats", "heals")), group="extra"),
    StatDef("resupplies", "Resupplies", "count",
            (("generalStats", "resupplies"), ("overviewStats", "resupplies")), group="extra"),
    StatDef("repairs", "Repairs", "count", (("generalStats", "repairs"), ("overviewStats", "repairs")), group="extra"),
)
STAT_INDEX = {s.key: s for s in STATS}
MAPPED_FIELDS = {f for s in STATS for _, f in s.sources}


def _num(stats: dict, key: str) -> float | None:
    item = stats.get(key) or {}
    v = item.get("value")
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _ratio(a: float | None, b: float | None, scale: float = 1.0) -> float | None:
    if a is None or b is None:
        return None
    if b == 0:
        return None
    return round(a / b * scale, 4)


DERIVATIONS = {
    "kd_ratio": (("kills", "deaths"), lambda s: _ratio(_num(s, "kills"), _num(s, "deaths"))),
    "wl_ratio": (("wins", "losses"), lambda s: _ratio(_num(s, "wins"), _num(s, "losses"))),
    "headshot_rate": (("headshots", "kills"), lambda s: _ratio(_num(s, "headshots"), _num(s, "kills"), 100)),
    "accuracy": (("shots_hit", "shots_fired"), lambda s: _ratio(_num(s, "shots_hit"), _num(s, "shots_fired"), 100)),
    "kills_per_minute": (("kills", "time_played"),
                         lambda s: _ratio(_num(s, "kills"), (_num(s, "time_played") or 0) / 60 or None)),
    "rounds": (("wins", "losses"),
               lambda s: None if _num(s, "wins") is None or _num(s, "losses") is None
               else _num(s, "wins") + _num(s, "losses")),
}


@dataclass
class StatisticsManager:
    defs: tuple[StatDef, ...] = field(default=STATS)

    def catalogue(self) -> list[dict]:
        return [{"key": d.key, "label": d.label, "unit": d.unit, "group": d.group, "editable": d.editable,
                 "derived_from": list(DERIVATIONS[d.derived][0]) if d.derived else [],
                 "sources": [f"{c}.{f}" for c, f in d.sources], "note": d.note} for d in self.defs]

    def apply_derivations(self, stats: dict[str, dict], keep: set[str] | None = None,
                          origin_label: str = "derived") -> None:
        """Fill derived stats that have no source value (or recompute ones not in `keep`).

        `keep` lists keys whose current value must not be recomputed (source values or
        explicit lab overrides)."""
        keep = keep or set()
        for d in self.defs:
            if not d.derived or d.key in keep:
                continue
            current = stats.get(d.key, {})
            if current.get("origin") == "source":
                continue
            inputs, fn = DERIVATIONS[d.derived]
            value = fn(stats)
            if value is None:
                stats[d.key] = {"value": None, "unit": d.unit, "origin": "missing", "source": None,
                                "derived_from": list(inputs)}
            else:
                stats[d.key] = {"value": value, "unit": d.unit, "origin": origin_label, "source": None,
                                "derived_from": list(inputs)}

    @staticmethod
    def diff(before: dict[str, dict], after: dict[str, dict]) -> list[dict[str, Any]]:
        changes = []
        for key in sorted(set(before) | set(after)):
            a = (before.get(key) or {}).get("value")
            b = (after.get(key) or {}).get("value")
            if a == b:
                continue
            delta = None
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                delta = round(b - a, 4)
            label = STAT_INDEX[key].label if key in STAT_INDEX else key
            changes.append({"key": key, "label": label, "before": a, "after": b, "delta": delta,
                            "after_origin": (after.get(key) or {}).get("origin")})
        return changes
