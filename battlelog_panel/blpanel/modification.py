"""ModificationEngine: the Profile Modification Lab.

Scenarios are built on a *copy* of a stored real snapshot. The engine reads
snapshots through Storage.snapshot() and writes only to `lab_scenarios`. Every
modified value is tagged origin="simulated", the output carries
mode="SIMULATED", and each modification also reports what would happen on a
live account (always UNSUPPORTED today, via AuthorizedWriteAdapter.assess).
"""
from __future__ import annotations

import copy
import csv
import io
import json
import uuid

from .audit import AuditLogger, utcnow
from .statistics import DERIVATIONS, STAT_INDEX, StatisticsManager
from .storage import Storage
from .write_adapter import AuthorizedWriteAdapter

STAT_OPS = ("set", "add", "multiply")
DOGTAG_OPS = ("unlock", "lock")
RATIO_STRATEGIES = ("adjust_kills", "override")
WATERMARK = "SIMULATED - lab scenario, not real account data"


class LabError(ValueError):
    pass


class ModificationEngine:
    def __init__(self, storage: Storage, audit: AuditLogger, writer: AuthorizedWriteAdapter,
                 stats: StatisticsManager | None = None):
        self.storage = storage
        self.audit = audit
        self.writer = writer
        self.stats = stats or StatisticsManager()

    # ---- persistence (lab table only) ------------------------------------------------
    def _load(self, scenario_id: int) -> dict:
        row = self.storage.one("SELECT * FROM lab_scenarios WHERE id=?", (scenario_id,))
        if not row:
            raise LabError(f"scenario {scenario_id} not found")
        row["modifications"] = json.loads(row.pop("modifications_json"))
        return row

    def _save_mods(self, scenario_id: int, mods: list[dict]) -> None:
        self.storage.execute("UPDATE lab_scenarios SET modifications_json=?, updated_at=? WHERE id=?",
                             (json.dumps(mods), utcnow(), scenario_id))

    def list_scenarios(self) -> list[dict]:
        rows = self.storage.query("SELECT * FROM lab_scenarios ORDER BY id DESC")
        for r in rows:
            r["modifications"] = json.loads(r.pop("modifications_json"))
        return rows

    def create(self, name: str, base_snapshot_id: int, actor: str = "system") -> int:
        if not self.storage.snapshot(base_snapshot_id):
            raise LabError(f"snapshot {base_snapshot_id} not found")
        now = utcnow()
        cur = self.storage.execute(
            "INSERT INTO lab_scenarios (name, base_snapshot_id, created_at, updated_at, modifications_json) "
            "VALUES (?,?,?,?,?)", (name.strip()[:80] or "Cenário", base_snapshot_id, now, now, "[]"))
        self.audit.record("lab_create_scenario", "SIMULATED", "ok", actor, f"scenario:{cur.lastrowid}",
                          {"base_snapshot_id": base_snapshot_id, "name": name})
        return cur.lastrowid

    def delete(self, scenario_id: int, actor: str = "system") -> None:
        self._load(scenario_id)
        self.storage.execute("DELETE FROM lab_scenarios WHERE id=?", (scenario_id,))
        self.audit.record("lab_delete_scenario", "SIMULATED", "ok", actor, f"scenario:{scenario_id}")

    # ---- modifications ---------------------------------------------------------------
    def add_modification(self, scenario_id: int, kind: str, target: str, op: str, value=None,
                         strategy: str = "adjust_kills", actor: str = "system") -> dict:
        sc = self._load(scenario_id)
        base = self.storage.snapshot(sc["base_snapshot_id"])["normalized"]
        if kind == "stat":
            d = STAT_INDEX.get(target)
            if d is None:
                raise LabError(f"unknown stat {target!r}")
            if not d.editable:
                raise LabError(f"{target} is computed from other stats; edit its inputs instead")
            if op not in STAT_OPS:
                raise LabError(f"op must be one of {STAT_OPS}")
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise LabError("value must be a number")
            if strategy not in RATIO_STRATEGIES:
                raise LabError(f"strategy must be one of {RATIO_STRATEGIES}")
            if op != "set" and base["stats"].get(target, {}).get("value") is None:
                raise LabError(f"{target} has no base value; use op 'set'")
            live_kind = "stat"
        elif kind == "dogtag":
            if op not in DOGTAG_OPS:
                raise LabError(f"op must be one of {DOGTAG_OPS}")
            if not any(t["id"] == target for t in (base.get("dogtags") or [])):
                raise LabError(f"dogtag {target!r} not present in the base snapshot")
            live_kind = "dogtag_unlock"
        else:
            raise LabError("kind must be 'stat' or 'dogtag'")
        live = self.writer.assess(live_kind)
        mod = {"id": uuid.uuid4().hex[:10], "kind": kind, "target": target, "op": op, "value": value,
               "strategy": strategy if kind == "stat" else None, "created_at": utcnow(),
               "lab_status": "SIMULATED", "live_status": live["status"], "live_reason": live["reason"]}
        mods = sc["modifications"] + [mod]
        self._save_mods(scenario_id, mods)
        self.audit.record("lab_modify", "SIMULATED", "ok", actor, f"scenario:{scenario_id}",
                          {k: mod[k] for k in ("kind", "target", "op", "value", "strategy")})
        return mod

    def remove_modification(self, scenario_id: int, mod_id: str, actor: str = "system") -> None:
        sc = self._load(scenario_id)
        mods = [m for m in sc["modifications"] if m["id"] != mod_id]
        if len(mods) == len(sc["modifications"]):
            raise LabError(f"modification {mod_id} not found")
        self._save_mods(scenario_id, mods)
        self.audit.record("lab_remove_modification", "SIMULATED", "ok", actor, f"scenario:{scenario_id}",
                          {"modification_id": mod_id})

    def restore(self, scenario_id: int, actor: str = "system") -> None:
        self._load(scenario_id)
        self._save_mods(scenario_id, [])
        self.audit.record("lab_restore", "SIMULATED", "ok", actor, f"scenario:{scenario_id}")

    # ---- evaluation ------------------------------------------------------------------
    def evaluate(self, scenario_id: int) -> dict:
        sc = self._load(scenario_id)
        snap = self.storage.snapshot(sc["base_snapshot_id"])
        original = snap["normalized"]
        modified = copy.deepcopy(original)
        stats = modified["stats"]
        overridden: set[str] = set()
        warnings: list[str] = []
        for m in sc["modifications"]:
            if m["kind"] == "stat":
                key, cur = m["target"], stats[m["target"]].get("value")
                if m["op"] == "set":
                    new = m["value"]
                elif m["op"] == "add":
                    new = (cur or 0) + m["value"]
                else:
                    new = (cur or 0) * m["value"]
                if STAT_INDEX[key].unit == "count":
                    if new < 0:
                        warnings.append(f"{key}: negative result clamped to 0")
                        new = 0
                    new = int(round(new))
                if key in ("kd_ratio", "wl_ratio") and m["strategy"] == "adjust_kills":
                    num, den = ("kills", "deaths") if key == "kd_ratio" else ("wins", "losses")
                    d = stats[den].get("value")
                    if not d:
                        warnings.append(f"{key}: cannot solve for {num} because {den} is missing or 0; overriding")
                        overridden.add(key)
                        stats[key] = {**stats[key], "value": new, "origin": "simulated", "modification": m["id"]}
                        continue
                    stats[num] = {**stats[num], "value": int(round(new * d)), "origin": "simulated",
                                  "modification": m["id"]}
                    overridden.discard(key)
                    continue
                stats[key] = {**stats[key], "value": new, "origin": "simulated", "modification": m["id"]}
                if key in ("kd_ratio", "wl_ratio"):
                    overridden.add(key)
            else:
                for t in modified.get("dogtags") or []:
                    if t["id"] == m["target"]:
                        t["status"] = "UNLOCKED" if m["op"] == "unlock" else "LOCKED"
                        t["status_basis"] = f"SIMULATED by modification {m['id']}"
                        t["simulated"] = True
        # Recompute a derived stat only when one of its inputs was simulated and it was
        # not explicitly overridden; otherwise it keeps its original value and origin.
        touched = {k for k, v in stats.items() if v.get("origin") == "simulated"}
        for k, (inputs, fn) in DERIVATIONS.items():
            if k in overridden or not (set(inputs) & touched):
                continue
            value = fn(stats)
            stats[k] = {**stats[k], "value": value, "source": None, "derived_from": list(inputs),
                        "origin": "simulated-derived" if value is not None else "missing"}
        for key in overridden:
            if key == "kd_ratio" and stats["kills"].get("value") is not None and stats["deaths"].get("value"):
                real = stats["kills"]["value"] / stats["deaths"]["value"]
                if abs(real - stats[key]["value"]) > 0.005:
                    warnings.append(f"K/D override ({stats[key]['value']}) is inconsistent with kills/deaths "
                                    f"({real:.3f}) - a real profile could never show this combination")
        modified["mode"] = "SIMULATED"
        modified["watermark"] = WATERMARK
        dogtag_changes = []
        orig_tags = {t["id"]: t for t in original.get("dogtags") or []}
        for t in modified.get("dogtags") or []:
            if t.get("simulated") and orig_tags[t["id"]]["status"] != t["status"]:
                dogtag_changes.append({"id": t["id"], "name": t["name"], "before": orig_tags[t["id"]]["status"],
                                       "after": t["status"], "after_origin": "simulated"})
        return {
            "scenario": {k: sc[k] for k in ("id", "name", "base_snapshot_id", "created_at", "updated_at")},
            "mode": "SIMULATED",
            "watermark": WATERMARK,
            "base_snapshot": {"id": snap["id"], "captured_at": snap["captured_at"],
                              "source_kind": snap["source_kind"], "raw_sha256": snap["raw_sha256"]},
            "modifications": sc["modifications"],
            "original": original,
            "modified": modified,
            "diff": StatisticsManager.diff(original["stats"], stats),
            "dogtag_diff": dogtag_changes,
            "warnings": warnings,
        }

    def export(self, scenario_id: int, fmt: str = "json", actor: str = "system") -> tuple[str, str]:
        ev = self.evaluate(scenario_id)
        self.audit.record("lab_export", "SIMULATED", "ok", actor, f"scenario:{scenario_id}", {"format": fmt})
        if fmt == "json":
            return json.dumps(ev, indent=2, default=str), "application/json"
        if fmt != "csv":
            raise LabError("format must be json or csv")
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([f"# {WATERMARK}"])
        w.writerow([f"# scenario {ev['scenario']['id']} {ev['scenario']['name']!r}; base snapshot "
                    f"{ev['base_snapshot']['id']} ({ev['base_snapshot']['source_kind']}, "
                    f"{ev['base_snapshot']['captured_at']})"])
        w.writerow(["key", "label", "original", "simulated", "delta", "simulated_origin"])
        for c in ev["diff"]:
            w.writerow([c["key"], c["label"], c["before"], c["after"], c["delta"], c["after_origin"]])
        for c in ev["dogtag_diff"]:
            w.writerow([f"dogtag:{c['id']}", c["name"], c["before"], c["after"], "", "simulated"])
        w.writerow([])
        w.writerow(["modification", "kind", "target", "op", "value", "lab_status", "live_status", "live_reason"])
        for m in ev["modifications"]:
            w.writerow([m["id"], m["kind"], m["target"], m["op"], m["value"], m["lab_status"], m["live_status"],
                        m["live_reason"]])
        return buf.getvalue(), "text/csv"
