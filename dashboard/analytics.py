"""Read-only dashboard projections from a complete, canonical snapshot.

Observed e1RM deliberately differs from the strict Notion comparison metric:
unknown historical conditions can be plotted, but never create verified gains.
All calculations retain unrounded source values and evidence IDs.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import csv
from datetime import date, datetime, timedelta
import hashlib
import io
import json
import math
from zoneinfo import ZoneInfo

from notion.metrics import day, rep_band, week

SEOUL = ZoneInfo("Asia/Seoul")
SCHEMA_VERSION = "1"
UNKNOWN = "미상"
UNCLASSIFIED = "미분류"
# Explicit semantic aliases observed in the source library. Do not use prefix
# matching: an incline/Smith/Romanian variant is a separate Exercise ID.
DEFAULT_NAMES = (("벤치프레스", "바벨벤치프레스", "benchpress", "barbellbenchpress"),
                 ("스쿼트", "바벨백스쿼트", "squat", "barbellbacksquat"),
                 ("데드리프트", "컨벤셔널데드리프트", "deadlift", "conventionaldeadlift"),
                 ("풀업", "pullup", "pull-up"))


def _today(value=None):
    if value is None:
        return datetime.now(SEOUL).date()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("today timestamp must include a timezone")
        return value.astimezone(SEOUL).date()
    return value if isinstance(value, date) else date.fromisoformat(value)


def _number(value):
    return (float(value) if isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) else None)


def _reps(value):
    number = _number(value)
    return number if number is not None and number > 0 and number.is_integer() else None


def _name(value):
    return "".join(str(value).split()).casefold()


def _date(value):
    try:
        return day(value)
    except (ValueError, TypeError, OverflowError):
        return None


def _instant(value):
    if not isinstance(value, str) or len(value) <= 10:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def _fingerprint_value(value):
    """Stable JSON for even invalid source values, without NaN serialization."""
    if isinstance(value, float) and not math.isfinite(value):
        return {"invalid_numeric": repr(value)}
    if isinstance(value, dict):
        return {str(key): _fingerprint_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_fingerprint_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return {"invalid_type": type(value).__name__, "value": str(value)}


def _baseline_fingerprint(raw, session, normalized):
    # The local version label is user controlled. Bind a baseline separately
    # to the source record and its historical context, including changes that
    # leave the numeric metric unchanged (date, type, edit version, etc.).
    payload = {"schema": "baseline-source-v1", "set_id": raw["id"],
        "session_ids": raw.get("session_ids"), "exercise_ids": raw.get("exercise_ids"),
        "set_last_edited_time": raw.get("last_edited_time"),
        "session_id": session["id"] if session else None,
        "session_date": session.get("source_date") if session else None,
        "session_last_edited_time": session.get("last_edited_time") if session else None,
        "session_done": session.get("done") if session else None,
        "session_split": session.get("split") if session else None,
        "session_split_id": session.get("split_id") if session else None,
        "load": raw.get("load"), "reps": raw.get("reps"),
        "source_set_type": raw.get("set_type"), "raw_set_type": raw.get("raw_set_type"),
        "set_type": normalized["set_type"],
        "set_done": raw.get("done", raw.get("completed")),
        "condition": normalized["condition"], "condition_confirmed": normalized["condition_confirmed"],
        "load_unit": normalized["load_unit"], "pullup": normalized["pullup"],
        "pullup_mode": normalized["pullup_mode"],
        "external_load_verified": raw.get("external_load_verified")}
    encoded = json.dumps(_fingerprint_value(payload), sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _context(snapshot, today=None):
    now = _today(today)
    for name in ("sessions", "sets", "exercises"):
        records = snapshot.get(name, [])
        ids = [item.get("id") for item in records]
        if any(not isinstance(identifier, str) or not identifier for identifier in ids):
            raise ValueError(f"Missing {name} source ID")
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate {name} source IDs")
    exercises = {row["id"]: {**row, "label": row.get("label") or UNKNOWN,
                 "muscle": row.get("muscle") or row.get("primary_muscle") or UNKNOWN}
                 for row in snapshot.get("exercises", [])}
    sessions = {}
    for raw in snapshot.get("sessions", []):
        observed = _date(raw.get("date"))
        issues = []
        if observed is None:
            issues.append("invalid_session_date")
        elif observed > now:
            issues.append("future_session_date")
        sessions[raw["id"]] = {**raw, "date": observed.isoformat() if observed else None,
            "source_date": raw.get("date"), "_day": observed,
            "_instant": _instant(raw.get("date")), "split": raw.get("split") or UNKNOWN,
            "done": raw.get("done", raw.get("completed")) is True,
            "eligible": bool(observed and observed <= now and raw.get("done", raw.get("completed")) is True),
            "issues": issues}
    sets = []
    for raw in snapshot.get("sets", []):
        session_ids = raw.get("session_ids") or []
        exercise_ids = raw.get("exercise_ids") or []
        session = sessions.get(session_ids[0]) if len(session_ids) == 1 else None
        exercise = exercises.get(exercise_ids[0]) if len(exercise_ids) == 1 else None
        issues = []
        if not session:
            issues.append("invalid_session_relation")
        elif not session["_day"]:
            issues.append("invalid_session_date")
        elif session["_day"] > now:
            issues.append("future_session_date")
        if not exercise:
            issues.append("unlinked_exercise")
        condition = raw.get("condition")
        # A canonical condition is a verified historical value. An explicit
        # false confirmation overrides it; the exercise library is never read.
        condition = condition if isinstance(condition, str) and condition.strip() and raw.get("condition_confirmed", True) is True else None
        if condition is None:
            issues.append("unconfirmed_condition")
        load, reps = _number(raw.get("load")), _number(raw.get("reps"))
        valid_reps = _reps(raw.get("reps"))
        if valid_reps is None:
            issues.append("invalid_reps")
        if load is None:
            issues.append("missing_or_invalid_load")
        elif load < 0:
            issues.append("negative_load")
        unit = raw.get("load_unit")
        if unit != "kg":
            issues.append("unconfirmed_load_unit")
        pullup = raw.get("pullup") is True or bool(exercise and exercise.get("pullup") is True)
        mode = raw.get("pullup_mode") if raw.get("pullup_mode") in {"bodyweight", "added", "assisted"} else "unknown"
        if pullup and mode == "unknown":
            issues.append("unconfirmed_pullup_mode")
        set_type = raw.get("set_type") or raw.get("raw_set_type") or UNCLASSIFIED
        if set_type == UNCLASSIFIED:
            issues.append("unclassified_set_type")
        eligible = bool(raw.get("done", raw.get("completed")) is True and session and session["eligible"])
        e1rm = (load * (1 + valid_reps / 30) if not pullup and unit == "kg" and
                load is not None and load > 0 and valid_reps is not None and valid_reps <= 12 else None)
        volume = (load * valid_reps if not pullup and unit == "kg" and
                  raw.get("external_load_verified") is True and load is not None and load >= 0 and valid_reps is not None else None)
        if e1rm is not None and not math.isfinite(e1rm):
            issues.append("e1rm_overflow")
            e1rm = None
        if volume is not None and not math.isfinite(volume):
            issues.append("volume_overflow")
            volume = None
        if volume is None:
            issues.append("volume_ineligible")
        normalized = {**raw, "session_id": session["id"] if session else None,
            "exercise_id": exercise["id"] if exercise else None,
            "exercise": exercise["label"] if exercise else "미연결",
            "muscle": exercise["muscle"] if exercise else UNKNOWN,
            "date": session["date"] if session else None,
            "_day": session["_day"] if session else None,
            "_instant": session["_instant"] if session else None,
            "split": session["split"] if session else UNKNOWN,
            "split_id": session.get("split_id") if session else None,
            "done": eligible, "eligible": eligible,
            "load": load, "reps": reps, "_valid_reps": valid_reps,
            "set_type": set_type, "raw_set_type": raw.get("raw_set_type"),
            "condition": condition, "condition_confirmed": condition is not None,
            "pullup": pullup, "pullup_mode": mode, "load_unit": unit,
            "e1rm_observed": e1rm, "e1rm_display": round(e1rm, 1) if e1rm is not None else None,
            "volume": volume, "issues": sorted(set(issues))}
        normalized["baseline_fingerprint"] = _baseline_fingerprint(raw, session, normalized)
        sets.append(normalized)
    return now, sessions, exercises, sets


def _range(request, now, sessions):
    scope = request.get("range") or {}
    preset = scope.get("preset", "12w")
    if preset == "4w":
        start, end = now - timedelta(days=27), now
    elif preset == "12w":
        start, end = now - timedelta(days=83), now
    elif preset == "all":
        start, end = min((s["_day"] for s in sessions.values() if s["eligible"]), default=now), now
    elif preset == "custom":
        try:
            start, end = date.fromisoformat(scope["start"]), date.fromisoformat(scope["end"])
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError("A custom range needs ISO start and end dates") from exc
        if start > end or end > now:
            raise ValueError("The range must satisfy start <= end <= Seoul today")
    else:
        raise ValueError("Unknown range preset")
    return start, end


def _filters(request):
    raw = request.get("filters") or {}
    return {key: set(raw.get(key) or []) for key in ("split_ids_or_values", "set_types", "exercise_ids")}


def _matches(row, filters, is_set=True):
    splits = filters["split_ids_or_values"]
    if splits and row.get("split") not in splits and row.get("split_id") not in splits:
        return False
    if is_set:
        if filters["set_types"] and row["set_type"] not in filters["set_types"]:
            return False
        if filters["exercise_ids"] and row["exercise_id"] not in filters["exercise_ids"]:
            return False
    return True


def _select(request, now, sessions, sets):
    start, end = _range(request, now, sessions)
    filters = _filters(request)
    historical = [r for r in sets if r["eligible"] and _matches(r, filters)]
    selected = [r for r in historical if start <= r["_day"] <= end]
    member_ids = {r["session_id"] for r in selected}
    selected_sessions = [s for s in sessions.values() if s["eligible"] and start <= s["_day"] <= end
                         and _matches(s, filters, False) and
                         (not (filters["set_types"] or filters["exercise_ids"]) or s["id"] in member_ids)]
    return start, end, filters, historical, selected, selected_sessions


def _public(row):
    return {key: value for key, value in row.items() if not key.startswith("_") and key not in {"eligible", "volume"}}


def _point(value, rows=(), **fields):
    return {**fields, "value": value, "sample_count": len(rows),
            "source_set_ids": sorted({r["id"] for r in rows}),
            "source_session_ids": sorted({r["session_id"] for r in rows if r.get("session_id")})}


def _exclusions(rows, included_ids, reason):
    excluded = [r for r in rows if r["id"] not in included_ids]
    return {"count": len(excluded), "reasons": {reason: len(excluded)} if excluded else {}}


def _chart(title, metric, unit, aggregation, start, end, series=None, exclusions=None, **extra):
    series = series or []
    has_values = any(point.get("value") is not None for item in series for point in item["points"])
    return {"status": "ok" if has_values else "empty", "title": title, "metric": metric,
            "unit": unit, "aggregation": aggregation,
            "range": {"start": start.isoformat(), "end": end.isoformat()},
            "series": series, "exclusions": exclusions or {"count": 0, "reasons": {}}, **extra}


def _condition_key(row):
    return row["condition"] or "__observed__"


def _series_identity(row, pullup=False):
    identity = (row["exercise_id"], _condition_key(row))
    if pullup:
        # Additional/assistance loads and unspecified raw loads never share a
        # repetition series. A missing load is different from an explicit zero.
        return identity + (row["pullup_mode"], None if row["pullup_mode"] == "bodyweight" else row["load"], row["load_unit"])
    return identity


def _series_meta(key, row, pullup=False):
    condition = row["condition"]
    label = row["exercise"] + " · " + (condition or "조건 미확인")
    if pullup:
        modes = {"bodyweight": "맨몸", "added": "추가", "assisted": "보조", "unknown": "방식 미확인"}
        label += " · " + modes[row["pullup_mode"]]
        if row["pullup_mode"] != "bodyweight":
            label += f" · {row['load'] if row['load'] is not None else '중량 없음'} {row['load_unit'] or '단위 미확인'}"
    return {"key": "|".join(str(part) for part in key), "label": label,
            "exercise_id": row["exercise_id"], "condition_key": condition,
            "observation_only": condition is None or (pullup and row["pullup_mode"] == "unknown"),
            **({"pullup_mode": row["pullup_mode"], "load": row["load"], "load_unit": row["load_unit"]} if pullup else {})}


def _winner(rows, field):
    return max(rows, key=lambda r: (r[field], r.get("_instant") or float("-inf"), r["id"]))


def _trend(rows, start, end, pullup=False):
    groups = defaultdict(list)
    eligible = []
    for row in rows:
        value = row["_valid_reps"] if pullup else row["e1rm_observed"]
        if (row["exercise_id"] and row["pullup"] == pullup and value is not None and
                (not pullup or row["pullup_mode"] not in {"added", "assisted"} or
                 (row["load"] is not None and row["load"] >= 0 and row["load_unit"] == "kg"))):
            groups[_series_identity(row, pullup)].append(row)
            eligible.append(row)
    series = []
    field = "_valid_reps" if pullup else "e1rm_observed"
    for key, group in sorted(groups.items(), key=lambda item: str(item[0])):
        daily = defaultdict(list)
        for row in group:
            daily[row["date"]].append(row)
        points = []
        for observed, records in sorted(daily.items()):
            winner = _winner(records, field)
            points.append(_point(winner[field], records, date=observed,
                                 detail={"representative_set_id": winner["id"], "load": winner["load"], "reps": winner["reps"]}))
        series.append({**_series_meta(key, group[0], pullup), "points": points})
    return _chart("풀업 추세" if pullup else "근력 추세", "reps" if pullup else "e1rm_observed",
                  "회" if pullup else "kg", "동일 시리즈의 날짜별 최고", start, end, series,
                  _exclusions(rows, {r["id"] for r in eligible}, "ineligible_pullup_reps" if pullup else "ineligible_e1rm"))


def _scope_rows(rows, request, default_ids, pullup=None):
    scope = request.get("detail_scope") or {}
    ids = {scope["exercise_id"]} if scope.get("exercise_id") else set(default_ids)
    result = [r for r in rows if r["exercise_id"] in ids]
    if scope.get("condition_key"):
        condition = scope["condition_key"]
        result = [r for r in result if (r["condition"] or "__observed__") == condition]
    if scope.get("pullup_mode"):
        result = [r for r in result if r["pullup_mode"] == scope["pullup_mode"]]
    if scope.get("fixed_load") is not None:
        # C02 applies a weight selector to ordinary lifts. For pullups it is
        # additionally required to isolate an added/assistance-load series.
        result = [r for r in result if not r["pullup"] or r["load"] == scope["fixed_load"]]
    return [r for r in result if r["pullup"] == pullup] if pullup is not None else result


def _fixed_load(rows, request, start, end):
    valid = [r for r in rows if r["load_unit"] == "kg" and
             r["load"] is not None and r["load"] >= 0 and r["_valid_reps"] is not None]
    loads = sorted({r["load"] for r in valid})
    selected = (request.get("detail_scope") or {}).get("fixed_load")
    if selected is None and valid:
        latest = max(valid, key=lambda r: (r["date"], r["_instant"] or float("-inf"), r["id"]))
        selected = latest["load"]
    if selected is not None and (_number(selected) is None or selected < 0):
        raise ValueError("fixed_load must be a finite nonnegative number")
    matched = [r for r in valid if r["load"] == selected]
    groups = defaultdict(list)
    for row in matched:
        groups[_series_identity(row, row["pullup"])].append(row)
    series = []
    for key, group in sorted(groups.items(), key=lambda item: str(item[0])):
        daily = defaultdict(list)
        for row in group:
            daily[row["date"]].append(row)
        series.append({**_series_meta(key, group[0], group[0]["pullup"]), "points": [
            _point(_winner(records, "_valid_reps")["_valid_reps"], records, date=observed,
                   detail={"fixed_load": selected, "representative_set_id": _winner(records, "_valid_reps")["id"]})
            for observed, records in sorted(daily.items())]})
    return _chart("같은 중량의 반복", "reps", "회", "같은 중량·조건의 날짜별 최고", start, end, series,
                  _exclusions(rows, {r["id"] for r in valid}, "ineligible_fixed_load_reps"),
                  available_loads=loads, selected_load=selected)


def _rep_bands(rows, start, end):
    valid = [r for r in rows if not r["pullup"] and r["load_unit"] == "kg" and
             r["load"] is not None and r["load"] > 0 and rep_band(r["_valid_reps"])]
    groups = defaultdict(list)
    for row in valid:
        groups[_series_identity(row)].append(row)
    months = []
    month = start.replace(day=1)
    while month <= end:
        months.append(month.isoformat()[:7])
        month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    series = []
    for key, group in sorted(groups.items(), key=lambda item: str(item[0])):
        for band in ("1–5회", "6–8회", "9–12회"):
            points = []
            for month in months:
                records = [r for r in group if r["date"][:7] == month and rep_band(r["_valid_reps"]) == band]
                value = max((r["load"] for r in records), default=None)
                points.append(_point(value, records, date=month + "-01", label=month,
                                     detail={"rep_band": band, "missing": not records}))
            meta = _series_meta(key, group[0])
            series.append({**meta, "key": meta["key"] + "|" + band, "label": meta["label"] + " · " + band,
                           "rep_band": band, "points": points})
    return _chart("반복 구간별 최고 중량", "load", "kg", "월별·반복 구간별 최고", start, end, series,
                  _exclusions(rows, {r["id"] for r in valid}, "ineligible_rep_band_load"))


def _weekly(rows, sessions, start, end):
    starts = []
    cursor = date.fromisoformat(week(start))
    while cursor <= end:
        starts.append(cursor)
        cursor += timedelta(days=7)
    types = sorted({r["set_type"] for r in rows}) or [UNCLASSIFIED]
    session_points, volume_points = [], []
    type_points = {name: [] for name in types}
    eligible_volume = [r for r in rows if r["volume"] is not None]
    for bucket in starts:
        bucket_end = bucket + timedelta(days=6)
        records = [r for r in rows if bucket <= r["_day"] <= bucket_end]
        session_records = [s for s in sessions if bucket <= s["_day"] <= bucket_end]
        partial_start, partial_end = max(bucket, start), min(bucket_end, end)
        detail = {"range_start": partial_start.isoformat(), "range_end": partial_end.isoformat(),
                  "partial_week": partial_start != bucket or partial_end != bucket_end}
        session_points.append({"date": bucket.isoformat(), "value": len(session_records),
            "sample_count": len(session_records), "source_set_ids": sorted(r["id"] for r in records),
            "source_session_ids": sorted(s["id"] for s in session_records), "detail": detail})
        for name in types:
            group = [r for r in records if r["set_type"] == name]
            type_points[name].append(_point(len(group), group, date=bucket.isoformat(), detail=detail))
        volumes = [r for r in records if r["volume"] is not None]
        volume_points.append(_point(sum(r["volume"] for r in volumes) if eligible_volume else None,
            volumes, date=bucket.isoformat(), detail={**detail, "included": len(volumes), "excluded": len(records) - len(volumes)}))
    simple = lambda key, label, points: [{"key": key, "label": label, "observation_only": False, "condition_key": None, "points": points}]
    counts = _chart("주간 훈련량", "sessions", "회", "월요일 주간 고유 세션", start, end,
                    simple("sessions", "운동 횟수", session_points), denominator=len(sessions))
    sets = _chart("주간 훈련량", "sets", "세트", "월요일 주간 유형별 완료 세트", start, end,
                  [{"key": name, "label": name, "observation_only": False, "condition_key": None, "points": points}
                   for name, points in type_points.items()], denominator=len(rows))
    volume = _chart("주간 훈련량", "external_load_volume", "kg·회", "확인된 외부중량 × 반복 합", start, end,
                    simple("volume", "외부중량 볼륨", volume_points),
                    _exclusions(rows, {r["id"] for r in eligible_volume}, "ineligible_external_load_volume"),
                    denominator=len(eligible_volume))
    if not eligible_volume:
        volume["status"] = "unavailable"
        volume["reason"] = "외부중량 의미·kg 단위·반복이 확인된 기록 없음"
    if not sessions:
        counts["status"] = "empty"
    if not rows:
        sets["status"] = "empty"
    return {"modes": {"sessions": counts, "sets": sets, "volume": volume}}


def _distribution(rows, start, end, muscle=False, frequency=False, top10=False):
    groups = defaultdict(list)
    unlinked = []
    for row in rows:
        if muscle:
            groups[row["muscle"]].append(row)
        elif row["exercise_id"]:
            groups[row["exercise_id"]].append(row)
        else:
            unlinked.append(row)
    values = {key: len({r["session_id"] for r in group}) if frequency else len(group) for key, group in groups.items()}
    ranking = sorted(groups, key=lambda key: (-values[key], key))
    other = ranking[10:] if top10 else []
    ranking = ranking[:10] if top10 else ranking
    points = []
    for key in ranking:
        group = groups[key]
        points.append(_point(values[key], group, category_id=key,
                             label=key if muscle else group[0]["exercise"]))
    if other:
        records = [r for key in other for r in groups[key]]
        points.append(_point(sum(values[key] for key in other), records, category_id="__other__", label="기타",
                             detail={"exercise_ids": other}))
    if unlinked and not frequency:
        points.append(_point(len(unlinked), unlinked, category_id="__unlinked__", label="미연결"))
    denominator = sum(values.values()) if frequency else len(rows)
    title = "종목별 빈도" if frequency else "근육별 세트 분포" if muscle else "종목별 세트 분포"
    return _chart(title, "session_exercise_frequency" if frequency else "sets", "회" if frequency else "세트",
                  "고유 Session × Exercise" if frequency else "주 근육 1개" if muscle else "종목 ID별 완료 세트",
                  start, end, [{"key": "distribution", "label": title, "observation_only": False,
                                "condition_key": None, "points": points}] if points else [],
                  {"count": len(unlinked), "reasons": {"unlinked_exercise": len(unlinked)}} if frequency and unlinked else None,
                  denominator=denominator)


def _split(rows, sessions, start, end):
    modes = {}
    for kind, records in (("sessions", sessions), ("sets", rows)):
        grouped = defaultdict(list)
        for record in records:
            grouped[record["split"]].append(record)
        points = []
        for label, group in sorted(grouped.items(), key=lambda pair: (-len(pair[1]), pair[0])):
            if kind == "sessions":
                point = {"value": len(group), "sample_count": len(group), "source_set_ids": [],
                         "source_session_ids": sorted(r["id"] for r in group)}
            else:
                point = _point(len(group), group)
            points.append({**point, "label": label, "category_id": label,
                           "detail": {"ratio": len(group) / len(records) if records else None}})
        modes[kind] = _chart("분할별 구성", kind, "회" if kind == "sessions" else "세트",
                             "완료 세션" if kind == "sessions" else "완료 세트", start, end,
                             [{"key": "split", "label": "분할", "observation_only": False,
                               "condition_key": None, "points": points}] if points else [], denominator=len(records))
    return {"modes": modes}


def _baselines(rows, historical, request, start, end):
    series, invalid, used = [], [], set()
    references = {r["id"]: r for r in historical}
    for baseline in request.get("baselines") or []:
        reason = None
        exercise_id, condition = baseline.get("exercise_id"), baseline.get("condition_key")
        metric, version = baseline.get("metric"), baseline.get("version")
        reference = references.get(baseline.get("source_set_id"))
        expected = _number(baseline.get("value"))
        identity = (exercise_id, condition, metric)
        if not version or not isinstance(version, str):
            reason = "missing_version"
        elif identity in used:
            reason = "duplicate_baseline"
        elif not reference or reference["exercise_id"] != exercise_id or not condition or reference["condition"] != condition:
            reason = "missing_or_changed_reference"
        elif baseline.get("source_fingerprint") != reference["baseline_fingerprint"]:
            reason = "missing_or_changed_source_fingerprint"
        elif metric not in {"e1rm", "e1rm_observed", "reps"}:
            reason = "invalid_metric"
        elif reference["pullup"] and (metric != "reps" or reference["pullup_mode"] != "bodyweight"):
            reason = "only_confirmed_bodyweight_pullup_supported"
        elif not reference["pullup"] and metric == "reps":
            reason = "invalid_metric"
        field = "_valid_reps" if metric == "reps" else "e1rm_observed"
        actual = reference[field] if reference else None
        if reason is None and (actual is None or expected is None or expected <= 0 or not math.isclose(actual, expected, rel_tol=1e-9, abs_tol=0)):
            reason = "changed_reference_value"
        candidates = [r for r in historical if r["exercise_id"] == exercise_id and r["condition"] == condition
                      and r[field] is not None and (not r["pullup"] or r["pullup_mode"] == "bodyweight")]
        if reason is None:
            daily_max = max((r[field] for r in candidates if r["date"] == reference["date"]), default=None)
            if not math.isclose(daily_max, expected, rel_tol=1e-9, abs_tol=0):
                reason = "reference_not_daily_maximum"
        if reason:
            invalid.append({"source_set_id": baseline.get("source_set_id"), "reason": reason})
            continue
        used.add(identity)
        daily = defaultdict(list)
        selected_ids = {r["id"] for r in rows}
        for row in candidates:
            if row["id"] in selected_ids and row["_day"] >= reference["_day"]:
                daily[row["date"]].append(row)
        points = [_point(_winner(group, field)[field] / expected * 100, group, date=observed,
                         detail={"raw_value": _winner(group, field)[field], "baseline_value": expected,
                                 "baseline_set_id": reference["id"], "baseline_version": version})
                  for observed, group in sorted(daily.items())]
        series.append({"key": "|".join((exercise_id, condition, metric, version)),
                       "label": reference["exercise"] + " · " + condition,
                       "exercise_id": exercise_id, "condition_key": condition,
                       "observation_only": False, "baseline": dict(baseline), "points": points})
    chart = _chart("발전 지수", "progress_index", "기준=100", "확인된 동일 조건 / 고정 기준 × 100",
                   start, end, series, {"count": len(invalid), "reasons": dict(Counter(i["reason"] for i in invalid))},
                   invalid_baselines=invalid)
    if not series:
        chart.update(status="unavailable", reason="동일 조건의 유효한 고정 기준을 설정하세요")
    return chart


def _representative(rows, scope=None):
    if not rows:
        return None
    scope = scope or {}
    pullup = rows[0]["pullup"]
    if pullup:
        mode = scope.get("pullup_mode")
        condition = scope.get("condition_key")
        selected = [r for r in rows if (not mode or r["pullup_mode"] == mode)
                    and (not condition or _condition_key(r) == condition)
                    and (scope.get("fixed_load") is None or r["load"] == scope["fixed_load"])
                    and r["_valid_reps"] is not None
                    and (r["pullup_mode"] not in {"added", "assisted"} or
                         (r["load"] is not None and r["load"] >= 0 and r["load_unit"] == "kg"))]
        identities = {_series_identity(r, True) for r in selected}
        # Multiple distinct pullup modes/loads cannot be called one best set.
        return _winner(selected, "_valid_reps") if selected and len(identities) == 1 else None
    valid = [r for r in rows if r["e1rm_observed"] is not None]
    return _winner(valid, "e1rm_observed") if valid else None


def _core(exercise, selected, historical, sessions, start, end, scope):
    rows = [r for r in selected if r["exercise_id"] == exercise["id"]]
    past = [r for r in historical if r["exercise_id"] == exercise["id"]]
    if scope.get("condition_key"):
        rows = [r for r in rows if _condition_key(r) == scope["condition_key"]]
        past = [r for r in past if _condition_key(r) == scope["condition_key"]]
    if scope.get("pullup_mode"):
        rows = [r for r in rows if not r["pullup"] or r["pullup_mode"] == scope["pullup_mode"]]
        past = [r for r in past if not r["pullup"] or r["pullup_mode"] == scope["pullup_mode"]]
    if scope.get("fixed_load") is not None:
        rows = [r for r in rows if not r["pullup"] or r["load"] == scope["fixed_load"]]
        past = [r for r in past if not r["pullup"] or r["load"] == scope["fixed_load"]]
    pullup = any(r["pullup"] for r in past) or exercise.get("pullup") is True
    current = previous = None
    latest_rows = []
    latest_ties = []
    ambiguity = False
    if rows:
        latest_day = max(r["_day"] for r in rows)
        latest_sessions = {r["session_id"] for r in rows if r["_day"] == latest_day}
        latest_instants = [sessions[i]["_instant"] for i in latest_sessions]
        ambiguity = len(latest_sessions) > 1 and (None in latest_instants or len(set(latest_instants)) < len(latest_instants))
        if ambiguity:
            latest_ties = [{"session_id": identifier, "date": sessions[identifier]["date"],
                            "sets": [_public(r) for r in sorted(rows, key=lambda r: r["id"])
                                     if r["session_id"] == identifier]}
                           for identifier in sorted(latest_sessions)]
        session_id = max(latest_sessions, key=lambda i: (sessions[i]["_instant"] or float("-inf"), i))
        latest_rows = [r for r in rows if r["session_id"] == session_id]
        current = _representative(latest_rows, scope)
        older = [r for r in past if r["_day"] < latest_day or
                 (not ambiguity and r["_day"] == latest_day and r["_instant"] is not None and
                  sessions[session_id]["_instant"] is not None and r["_instant"] < sessions[session_id]["_instant"])]
        if current and pullup:
            older = [r for r in older if _series_identity(r, True) == _series_identity(current, True)]
        if older:
            previous_id = max({r["session_id"] for r in older}, key=lambda i: (
                sessions[i]["_day"], sessions[i]["_instant"] or float("-inf"), i))
            previous_rows = [r for r in older if r["session_id"] == previous_id]
            if current and pullup:
                previous_rows = [r for r in previous_rows if _series_identity(r, True) == _series_identity(current, True)]
            previous = _representative(previous_rows, scope)
    record_best = _representative(rows, scope)
    comparison = {"status": "unavailable", "reason": "직전 기록 없음", "delta": None, "percent": None}
    verified_pr = None
    if ambiguity:
        comparison["reason"] = "같은 날짜의 수행 순서 미확인"
    elif current and previous:
        if not current["condition"] or current["condition"] != previous["condition"]:
            comparison["reason"] = "동일 측정 조건 미확인"
        elif current["load_unit"] != previous["load_unit"]:
            comparison["reason"] = "중량 단위 미확인"
        elif pullup and current["pullup_mode"] == "unknown":
            comparison["reason"] = "풀업 수행 방식 미확인"
        elif pullup and _series_identity(current, True) != _series_identity(previous, True):
            comparison["reason"] = "풀업 방식·중량 조건 불일치"
        else:
            field = "_valid_reps" if pullup else "e1rm_observed"
            if current[field] is not None and previous[field] is not None:
                comparison = {"status": "ok", "reason": None, "metric": "reps" if pullup else "e1rm",
                    "delta": current[field] - previous[field],
                    "percent": (current[field] / previous[field] - 1) * 100 if previous[field] > 0 else None}
    if current and current["condition"] and not ambiguity and (not pullup or current["pullup_mode"] != "unknown"):
        field = "_valid_reps" if pullup else "e1rm_observed"
        candidates = [r for r in past if r[field] is not None and
                      _series_identity(r, pullup) == _series_identity(current, pullup)]
        # PR means all-time up to the current performance, never a selected
        # window maximum, and never includes a later historical performance.
        candidates = [r for r in candidates if r["_day"] <= current["_day"]]
        if candidates and current[field] is not None:
            best = _winner(candidates, field)
            verified_pr = {"value": best[field], "metric": "reps" if pullup else "e1rm",
                           "source_set_id": best["id"], "condition_key": current["condition"],
                           "is_current_best": math.isclose(current[field], best[field], rel_tol=1e-9),
                           "scope": "all_history_through_current"}
    return {"id": exercise["id"], "label": exercise["label"], "muscle": exercise["muscle"],
            "pullup": pullup, "current_set": _public(current) if current else None,
            "previous_set": _public(previous) if previous else None,
            "record_best": _public(record_best) if record_best else None,
            "verified_pr": verified_pr, "comparison": comparison,
            "sequence_ambiguous": ambiguity,
            "latest_ties": latest_ties,
            "latest_sets": [_public(r) for r in sorted(latest_rows, key=lambda r: r["id"])],
            "reason": "서로 다른 풀업 조건: 방식·중량을 선택하세요" if pullup and rows and current is None else
                      "유효 대표 지표 없음" if rows and current is None else "이 기간에 기록 없음" if not rows else None,
            "trend": _trend(rows, start, end, pullup)}


def _diagnostics(sets, selected, sessions):
    period_issues = defaultdict(list)
    for row in selected:
        for issue in row["issues"]:
            period_issues[issue].append(row["id"])
    source_issues = defaultdict(list)
    for row in sets:
        if not row["_day"] or "future_session_date" in row["issues"]:
            for issue in row["issues"]:
                source_issues[issue].append(row["id"])
    render = lambda mapping: {issue: {"count": len(ids), "source_set_ids": sorted(ids)} for issue, ids in sorted(mapping.items())}
    return {"period": render(period_issues), "source": render(source_issues),
            "selected_sets": len(selected), "excluded_undated_sets": sum(r["_day"] is None for r in sets),
            "invalid_sessions": [{"id": s["id"], "issues": s["issues"]} for s in sessions.values() if s["issues"]],
            "unclassified_sets": len(period_issues.get("unclassified_set_type", [])),
            "unlinked_sets": len(period_issues.get("unlinked_exercise", [])),
            "unconfirmed_condition_sets": len(period_issues.get("unconfirmed_condition", []))}


def catalog(snapshot):
    _, sessions, exercises, sets = _context(snapshot)
    candidates, defaults, ambiguous = [], [], []
    for names in DEFAULT_NAMES:
        matches = sorted(identifier for identifier, ex in exercises.items() if _name(ex["label"]) in names)
        candidates.append({"name": names[0], "exercise_ids": matches})
        if len(matches) == 1:
            defaults.append(matches[0])
        elif len(matches) > 1:
            ambiguous.append({"name": names[0], "exercise_ids": matches})
    exercise_rows = []
    for identifier, exercise in sorted(exercises.items(), key=lambda pair: (pair[1]["label"], pair[0])):
        rows = [r for r in sets if r["exercise_id"] == identifier]
        exercise_rows.append({"id": identifier, "label": exercise["label"], "muscle": exercise["muscle"],
            "url": exercise.get("url"), "pullup": any(r["pullup"] for r in rows) or exercise.get("pullup") is True,
            "conditions": sorted({r["condition"] for r in rows if r["condition"]}),
            "has_unconfirmed_condition": any(r["condition"] is None for r in rows),
            "available_loads": sorted({r["load"] for r in rows if r["load_unit"] == "kg" and r["load"] is not None and r["load"] >= 0}),
            "pullup_modes": sorted({r["pullup_mode"] for r in rows if r["pullup"]})})
    return {"snapshot_id": snapshot.get("id"), "schema_version": SCHEMA_VERSION,
            "exercises": exercise_rows, "set_types": sorted({r["set_type"] for r in sets}),
            "splits": sorted({s["split"] for s in sessions.values()}), "default_core_exercise_ids": defaults,
            "default_core_candidates": candidates, "ambiguous_default_exercises": ambiguous}


def session_detail(snapshot, identifier):
    _, sessions, _, sets = _context(snapshot)
    if identifier not in sessions:
        raise KeyError(identifier)
    session = sessions[identifier]
    return {**_public(session), "snapshot_id": snapshot.get("id"),
            "sets": [_public(r) for r in sorted(sets, key=lambda r: r["id"]) if r["session_id"] == identifier]}


def analyze(snapshot, request_dict, today=None):
    now, sessions, exercises, sets = _context(snapshot, today)
    request = request_dict or {}
    start, end, filters, historical, selected, selected_sessions = _select(request, now, sessions, sets)
    core_ids = request.get("core_exercise_ids")
    if core_ids is None:
        core_ids = catalog(snapshot)["default_core_exercise_ids"]
    core_ids = list(dict.fromkeys(core_ids))
    scope = request.get("detail_scope") or {}
    detail_ids = [scope["exercise_id"]] if scope.get("exercise_id") else core_ids[:1]
    detail = _scope_rows(selected, request, detail_ids)
    week_start = now - timedelta(days=now.weekday())
    week_end = week_start + timedelta(days=6)
    current_sessions = [s for s in sessions.values() if s["eligible"] and week_start <= s["_day"] <= now]
    current_sets = [r for r in sets if r["eligible"] and week_start <= r["_day"] <= now]
    latest = max((s["_day"] for s in sessions.values() if s["eligible"]), default=None)
    recent_limit = request.get("recent_limit", 3)
    if isinstance(recent_limit, bool) or not isinstance(recent_limit, int) or not 1 <= recent_limit <= 1000:
        raise ValueError("recent_limit must be an integer between 1 and 1000")
    recent = []
    ordered_sessions = sorted(selected_sessions, key=lambda s: (s["_day"], s["_instant"] or float("-inf"), s["id"]), reverse=True)
    for session in ordered_sessions[:recent_limit]:
        records = [r for r in selected if r["session_id"] == session["id"]]
        same_day = [s for s in selected_sessions if s["_day"] == session["_day"]]
        recent.append({**_public(session), "sets": [_public(r) for r in sorted(records, key=lambda r: r["id"])],
                       "set_count": len(records), "exercise_labels": list(dict.fromkeys(r["exercise"] for r in records)),
                       "sequence_ambiguous": len(same_day) > 1 and any(s["_instant"] is None for s in same_day)})
    cores = []
    for identifier in core_ids:
        if identifier in exercises:
            core_scope = scope if scope.get("exercise_id") == identifier else {}
            cores.append(_core(exercises[identifier], selected, historical, sessions, start, end, core_scope))
        else:
            cores.append({"id": identifier, "label": "접근 불가 종목", "muscle": UNKNOWN, "pullup": False,
                          "current_set": None, "previous_set": None, "record_best": None,
                          "verified_pr": None, "comparison": {"status": "unavailable", "reason": "종목을 다시 선택하세요"},
                          "latest_sets": [], "reason": "종목을 다시 선택하세요", "trend": _trend([], start, end)})
    c01 = _trend(detail, start, end)
    c08 = _trend(detail, start, end, True)
    return {"meta": {"snapshot_id": snapshot.get("id"), "schema_version": SCHEMA_VERSION,
                     "timezone": "Asia/Seoul", "range_start": start.isoformat(), "range_end": end.isoformat(),
                     "range_preset": (request.get("range") or {}).get("preset", "12w"),
                     "fetched_at": snapshot.get("fetched_at"),
                     "source_max_last_edited_at": snapshot.get("source_max_last_edited_at"),
                     "refresh_state": "succeeded", "using_previous_data": False},
            "summary": {"current_week_start": week_start.isoformat(), "current_week_end": week_end.isoformat(),
                        "sessions": len(current_sessions), "sets": len(current_sets),
                        "latest_completed_session_date": latest.isoformat() if latest else None,
                        "latest_outside_range": bool(latest and not start <= latest <= end),
                        "range_sessions": len(selected_sessions), "range_sets": len(selected)},
            "core_exercises": cores,
            "charts": {"C01": c01, "C02": _fixed_load(detail, request, start, end),
                       "C03": _rep_bands(detail, start, end), "C04": _weekly(selected, selected_sessions, start, end),
                       "C05": _distribution(selected, start, end, muscle=True),
                       "C06": {"modes": {"top10": _distribution(selected, start, end, top10=True),
                                         "all": _distribution(selected, start, end)}},
                       "C07": {"modes": {"top10": _distribution(selected, start, end, frequency=True, top10=True),
                                         "all": _distribution(selected, start, end, frequency=True)}},
                       "C08": c08, "C09": _split(selected, selected_sessions, start, end),
                       "C10": _baselines(detail, historical, request, start, end)},
            "recent_sessions": recent, "recent_total": len(selected_sessions), "diagnostics": _diagnostics(sets, selected, sessions),
            "optional_sources": snapshot.get("optional_sources") or {}}


def _safe_text(value):
    if value is None:
        return ""
    text = str(value)
    # Spreadsheet programs may trim whitespace before evaluating formulas.
    return "'" + text if text.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")) else text


def export_csv(snapshot, request_dict, kind="sets", today=None):
    if kind not in {"sets", "sessions"}:
        raise ValueError("CSV kind must be sets or sessions")
    now, sessions, _, sets = _context(snapshot, today)
    start, end, _, _, selected, selected_sessions = _select(request_dict or {}, now, sessions, sets)
    output = io.StringIO(newline="")
    output.write("\ufeff")
    writer = csv.writer(output)
    metadata = ["snapshot_id", "fetched_at", "range_start", "range_end"]
    common = [_safe_text(snapshot.get("id")), _safe_text(snapshot.get("fetched_at")), start.isoformat(), end.isoformat()]
    if kind == "sets":
        writer.writerow(metadata + ["set_id", "session_id", "date", "split", "exercise_id", "exercise", "set_type",
                                    "load", "load_unit", "reps", "e1rm_observed", "condition", "condition_confirmed",
                                    "pullup_mode", "issues", "url"])
        for row in sorted(selected, key=lambda r: (r["date"], r["session_id"], r["id"])):
            values = [_safe_text(row.get(key)) for key in ("id", "session_id", "date", "split", "exercise_id", "exercise", "set_type")]
            values += [row["load"], _safe_text(row["load_unit"]), row["reps"], row["e1rm_observed"],
                       _safe_text(row["condition"]), row["condition_confirmed"], _safe_text(row["pullup_mode"]),
                       _safe_text(";".join(row["issues"])), _safe_text(row.get("url"))]
            writer.writerow(common + values)
    else:
        writer.writerow(metadata + ["session_id", "date", "split", "completed", "set_count", "url"])
        counts = Counter(row["session_id"] for row in selected)
        for row in sorted(selected_sessions, key=lambda r: (r["date"], r["id"])):
            writer.writerow(common + [_safe_text(row["id"]), row["date"], _safe_text(row["split"]), row["done"],
                                       counts[row["id"]], _safe_text(row.get("url"))])
    return output.getvalue()
