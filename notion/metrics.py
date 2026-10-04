"""Deterministic projections derived from source records, never mockup numbers."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import math
from zoneinfo import ZoneInfo

SEOUL = ZoneInfo("Asia/Seoul")
UNKNOWN = "미상"
SET_TYPES = {"Working", "Top Set", "Warm-up", "Back-off", "Drop Set", "Failure", "Technique"}


def day(value: str | None) -> date | None:
    if not value:
        return None
    if not isinstance(value, str):
        raise ValueError("Date must be an ISO date or timestamp")
    if len(value) == 10:
        return date.fromisoformat(value)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include its timezone")
    return parsed.astimezone(SEOUL).date()


def numeric(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Expected a numeric source value, without unit conversion")
    if not math.isfinite(value):
        raise ValueError("Non-finite numeric value")
    return float(value)


def week(value: date | None) -> str | None:
    return (value - timedelta(days=value.weekday())).isoformat() if value else None


def rep_band(reps: float | None) -> str | None:
    if reps is None or reps != int(reps):
        return None
    if 1 <= reps <= 5:
        return "1–5회"
    if 6 <= reps <= 8:
        return "6–8회"
    if 9 <= reps <= 12:
        return "9–12회"
    return None


def stable_key(*parts: str) -> str:
    raw = "\x00".join(parts).encode()
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class Session:
    id: str
    observed: date | None
    split: str
    done: bool


@dataclass(frozen=True)
class Exercise:
    id: str
    label: str
    muscle: str


@dataclass(frozen=True)
class Observation:
    id: str
    session_id: str | None
    exercise_id: str | None
    observed: date | None
    exercise: str
    muscle: str
    split: str
    done: bool
    load: float | None
    reps: float | None
    set_type: str
    condition: str | None
    pullup: bool
    issue: str

    @property
    def comparable(self) -> bool:
        return bool(self.done and self.observed and self.session_id
                    and self.exercise_id and self.condition
                    and self.reps and self.reps.is_integer()
                    and self.reps > 0)

    @property
    def e1rm(self) -> float | None:
        if (self.comparable and not self.pullup and self.load is not None
                and self.load > 0 and rep_band(self.reps)):
            return self.load * (1 + self.reps / 30)
        return None


def _unique(records: list[dict], kind: str) -> None:
    ids = [row["id"] for row in records]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate {kind} source IDs; stop before syncing")


def build(snapshot: dict, baselines: list[dict] | None = None, now: date | None = None) -> dict:
    """Input dates and conditions must come from inspected, explicitly mapped fields.

    In particular condition is an observation's historical condition, never the
    exercise library's current attributes. Unknown conditions remain excluded.
    """
    for collection in ("sessions", "exercises", "sets"):
        _unique(snapshot[collection], collection)
    if any(not isinstance(row["label"], str) or not row["label"].strip()
           for row in snapshot["exercises"]):
        raise ValueError("Exercise labels must be nonempty source strings")
    sessions = {row["id"]: Session(row["id"], day(row.get("date")),
                row.get("split") or UNKNOWN, row["done"] is True)
                for row in snapshot["sessions"]}
    duplicate_labels = Counter(row["label"] for row in snapshot["exercises"])
    exercises = {row["id"]: Exercise(row["id"], row["label"] +
                 (" · " + row["id"] if duplicate_labels[row["label"]] > 1 else ""),
                 row.get("muscle") or UNKNOWN)
                 for row in snapshot["exercises"]}
    observations = []
    for raw in snapshot["sets"]:
        session_ids = raw.get("session_ids", [])
        exercise_ids = raw.get("exercise_ids", [])
        session = sessions.get(session_ids[0]) if len(session_ids) == 1 else None
        exercise = exercises.get(exercise_ids[0]) if len(exercise_ids) == 1 else None
        load, reps = numeric(raw.get("load")), numeric(raw.get("reps"))
        condition = raw.get("condition") or None
        issues = []
        if not session:
            issues.append("세션 관계 누락/다중/접근 불가")
        elif not session.observed:
            issues.append("세션 날짜 누락")
        if not exercise:
            issues.append("종목 미연결/다중/접근 불가")
        if not condition:
            issues.append("비교 조건 미확인")
        if reps is None or reps <= 0 or reps != int(reps):
            issues.append("유효 반복 수 없음")
        set_type = raw.get("set_type")
        set_type = set_type if set_type in SET_TYPES else "미분류"
        observations.append(Observation(
            raw["id"], session.id if session else None,
            exercise.id if exercise else None,
            session.observed if session else None,
            exercise.label if exercise else UNKNOWN,
            exercise.muscle if exercise else UNKNOWN,
            session.split if session else UNKNOWN,
            raw["done"] is True and bool(session and session.done),
            load, reps, set_type, condition, raw.get("pullup") is True,
            " · ".join(issues),
        ))
    set_rows = []
    members = defaultdict(list)
    now = now or datetime.now(SEOUL).date()
    recent_counts = Counter(obs.exercise_id for obs in observations
                            if obs.done and obs.observed and obs.exercise_id
                            and now - timedelta(days=83) <= obs.observed <= now)
    top_ten = {identifier for identifier, count in sorted(
        recent_counts.items(), key=lambda item: (-item[1], item[0]))[:10]}
    for obs in observations:
        set_rows.append({
            "key": obs.id, "date": obs.observed.isoformat() if obs.observed else None,
            "week": week(obs.observed), "exercise": obs.exercise,
            "muscle": obs.muscle, "split": obs.split,
            "done": obs.done, "load": obs.load, "reps": obs.reps,
            "set_type": obs.set_type, "condition": obs.condition or "미확인",
            "e1rm": obs.e1rm, "rep_band": rep_band(obs.reps),
            "e1rm_display": round(obs.e1rm, 1) if obs.e1rm is not None else None,
            "comparable": obs.comparable, "pullup": obs.pullup,
            "top10": obs.exercise_id in top_ten,
            "issue": obs.issue, "original_set": [obs.id],
            "original_session": [obs.session_id] if obs.session_id else [],
            "original_exercise": [obs.exercise_id] if obs.exercise_id else [],
        })
        if obs.done and obs.observed and obs.session_id and obs.exercise_id:
            members[(obs.session_id, obs.exercise_id)].append(obs)
    member_rows = []
    for (session_id, exercise_id), group in sorted(members.items()):
        first = group[0]
        if len(group) > 100:
            raise ValueError("Membership has over 100 source relations; do not truncate")
        member_rows.append({
            "key": stable_key(session_id, exercise_id),
            "date": first.observed.isoformat(), "exercise": first.exercise,
            "done": True, "original_session": [session_id],
            "original_exercise": [exercise_id],
            "original_set": sorted(item.id for item in group),
        })
    session_rows = [{
        "key": row.id, "date": row.observed.isoformat() if row.observed else None,
        "week": week(row.observed), "split": row.split, "done": row.done,
        "original_session": [row.id],
    } for row in sessions.values()]
    weeks = defaultdict(lambda: {"session_ids": set(), "sets": 0, "types": Counter()})
    for session in sessions.values():
        if session.done and session.observed:
            weeks[week(session.observed)]["session_ids"].add(session.id)
    for obs in observations:
        if obs.done and obs.observed:
            item = weeks[week(obs.observed)]
            item["sets"] += 1
            item["types"][obs.set_type] += 1
    weekly_rows = []
    for start, item in sorted(weeks.items()):
        if len(item["session_ids"]) > 100:
            raise ValueError("Week has over 100 session relations; do not truncate")
        weekly_rows.append({"key": start, "date": start, "week": start, "done": True,
            "sessions_count": len(item["session_ids"]), "sets_count": item["sets"],
            "working_count": item["types"]["Working"] + item["types"]["Top Set"],
            "warmup_count": item["types"]["Warm-up"], "unknown_count": item["types"]["미분류"],
            "original_session": sorted(item["session_ids"]),
        })
    lookup = {item.id: item for item in observations}
    progress, exclusions, used_baselines = [], [], set()
    for baseline in baselines or []:
        source_id = baseline["source_set_id"]
        version = baseline["version"]
        if not isinstance(version, str) or not version:
            raise ValueError("A baseline needs an explicit version")
        reference = lookup.get(source_id)
        if not reference or not reference.comparable:
            exclusions.append({"source_set_id": source_id, "reason": "유효 기준 수행 없음"})
            continue
        metric = "reps" if reference.pullup else "e1rm"
        actual = reference.reps if reference.pullup else reference.e1rm
        expected = numeric(baseline["value"])
        if expected is None or expected <= 0 or actual is None:
            raise ValueError("A baseline requires a positive eligible value")
        if not math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError("Baseline source changed; create an explicit new baseline version")
        identity = (reference.exercise_id, metric)
        if identity in used_baselines:
            raise ValueError("Use one active baseline condition per exercise and metric")
        used_baselines.add(identity)
        groups = defaultdict(list)
        for obs in observations:
            if (obs.comparable and obs.exercise_id == reference.exercise_id
                    and obs.condition == reference.condition
                    and obs.pullup == reference.pullup
                    and obs.observed >= reference.observed):
                value = obs.reps if reference.pullup else obs.e1rm
                if value is not None:
                    groups[obs.observed].append((value, obs))
        for observed, points in sorted(groups.items()):
            value, winner = max(points, key=lambda item: (item[0], item[1].id))
            if observed == reference.observed and not math.isclose(value, expected, rel_tol=1e-9):
                raise ValueError("Baseline must be the eligible daily maximum")
            progress.append({
                "key": stable_key(reference.exercise_id, reference.condition,
                                  observed.isoformat(), metric, version),
                "date": observed.isoformat(), "exercise": winner.exercise,
                "condition": winner.condition, "done": True,
                "metric": metric, "index": value / expected * 100,
                "index_display": round(value / expected * 100, 2),
                "baseline_value": expected, "baseline_version": version,
                "observations": len(points), "original_set": [winner.id],
                "baseline_set": [reference.id],
                "original_exercise": [winner.exercise_id],
            })
    return {"sessions": session_rows, "sets": set_rows,
            "membership": member_rows, "progress": progress, "weekly": weekly_rows,
            "diagnostics": {
                "source_sessions": len(sessions), "source_sets": len(observations),
                "done_sets": sum(obs.done for obs in observations),
                "unlinked_exercise": sum(not obs.exercise_id for obs in observations),
                "unclassified_sets": sum(obs.set_type == "미분류" for obs in observations),
                "comparable_sets": sum(obs.comparable for obs in observations),
                "set_types": dict(Counter(obs.set_type for obs in observations)),
                "baseline_exclusions": exclusions,
            }}
