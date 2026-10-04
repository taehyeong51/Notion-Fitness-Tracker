"""Official 2026-03-11 view payloads, with filters local to each view."""
from datetime import datetime, timedelta

from .metrics import SEOUL

# id, page, projection, title, chart type, category, aggregation, value, subgroup
SPECS = [
    ("C01", "운동 성과", "sets", "근력 추세 · e1RM (kg)", "line", "Date", "max", "e1RM Display", None),
    ("C02", "운동 성과", "sets", "같은 중량 · 최고 반복 (회)", "line", "Date", "max", "Reps", None),
    ("C03", "운동 성과", "sets", "월별 반복 구간 · 최고 중량 (kg)", "column", "Date", "max", "Load", "Rep Band"),
    ("C04A", "훈련 구성", "sessions", "주간 세션 수", "column", "Week", "count", None, None),
    ("C04B", "훈련 구성", "sets", "주간 기록 세트 구성", "column", "Week", "count", None, "Set Type"),
    ("C05", "훈련 구성", "sets", "주 근육별 기록 세트", "bar", "Primary Muscle", "count", None, None),
    ("C06", "훈련 구성", "sets", "종목별 기록 세트 · 전체", "bar", "Exercise", "count", None, None),
    ("C06T", "훈련 구성", "sets", "종목별 기록 세트 · 상위 10", "bar", "Exercise", "count", None, None),
    ("C07", "훈련 구성", "membership", "종목별 수행 세션 수", "bar", "Exercise", "count", None, None),
    ("C08", "운동 성과", "sets", "풀업 최고 반복 (회)", "line", "Date", "max", "Reps", None),
    ("C09A", "훈련 구성", "sessions", "분할별 세션 수", "bar", "Split", "count", None, None),
    ("C09B", "훈련 구성", "sets", "분할별 기록 세트", "donut", "Split", "count", None, None),
    ("C10", "비교 분석", "progress", "중량 종목 발전 지수", "line", "Date", "max", "Index Display", "Exercise"),
    ("C10P", "비교 분석", "progress", "풀업 반복 지수 · 별도", "line", "Date", "max", "Index Display", "Exercise"),
]


def group(schema, name, granularity="day"):
    prop = schema[name]
    result = {"type": prop["type"], "property_id": prop["id"],
              "sort": {"type": "ascending"}}
    if prop["type"] == "date":
        result["group_by"] = granularity
        result["start_day_of_week"] = 1
    elif prop["type"] in {"title", "rich_text", "text"}:
        result["type"] = "text"
        result["group_by"] = "exact"
    elif prop["type"] == "formula":
        result.pop("sort")  # Formula sorting belongs to its scalar group_by.
        nested = {"type": prop["result_type"], "sort": {"type": "ascending"}}
        if nested["type"] == "date":
            nested.update(group_by=granularity, start_day_of_week=1)
        elif nested["type"] == "text":
            nested["group_by"] = "exact"
        result["group_by"] = nested
    return result


def payload(spec, schema, config, now=None, native=False):
    ident, page, role, title, kind, category, agg, value, subgroup = spec
    ident = ident.split("_")[0]
    now = now or datetime.now(SEOUL).date()
    filters = [
        {"property": "Done", "checkbox": {"equals": True}},
        {"property": "Date", "date": {"on_or_after": (now - timedelta(days=83)).isoformat()}},
        {"property": "Date", "date": {"on_or_before": now.isoformat()}},
    ]
    if native:
        filters = [
            {"property": "Done", "checkbox": {"equals": True}},
            {"property": "Recent 12 Weeks", "checkbox": {"equals": True}},
        ]
        if ident == "C07":
            agg, value = "unique", "Session Key"
            filters.append({"property": "Valid Exercise", "checkbox": {"equals": True}})
    strength = config.get("strength_scope", {})
    pullup = config.get("pullup_scope", {})
    scope = strength if ident in {"C01", "C02", "C03"} else pullup
    if ident in {"C01", "C02", "C03", "C08"}:
        filters += [
            {"property": "Original Exercise", "relation": {"contains": scope["exercise_id"]}
                if scope.get("exercise_id") else {"is_empty": True}},
            {"property": "Condition", "rich_text": {"equals": scope.get("condition") or "미선정"}},
            {"property": "Pullup", "checkbox": {"equals": ident == "C08"}},
        ]
        if not scope.get("observational"):
            filters.append({"property": "Comparable", "checkbox": {"equals": True}})
        else:
            title += " · 조건 미확인"
        filters.append({"property": "Reps", "number": {"greater_than": 0}})
    if ident in {"C01", "C03"}:
        filters += [
            {"property": "e1RM", "number": {"greater_than": 0}},
            {"property": "Load", "number": {"greater_than": 0}},
            {"property": "Reps", "number": {"greater_than_or_equal_to": 1}},
            {"property": "Reps", "number": {"less_than_or_equal_to": 12}},
        ]
    if ident == "C02":
        fixed = strength.get("fixed_load")
        # An unselected fixed load leaves an intentionally empty view.
        filters.append({"property": "Load", "number": {"equals": fixed if fixed is not None else -1}})
        title += f" · {fixed}kg" if fixed is not None else " · 고정 중량 미선정"
    if ident in {"C10", "C10P"}:
        filters.append({"property": "Metric", "select": {"equals": "e1rm" if ident == "C10" else "reps"}})
    if ident == "C06T":
        if native:
            filters.append({"or": [{"property": "Original Exercise", "relation": {"contains": identifier}}
                                    for identifier in config["top10_exercise_ids"]]})
        else:
            filters.append({"property": "Top 10", "checkbox": {"equals": True}})
    aggregation = {"aggregator": agg}
    if value:
        aggregation["property_id"] = schema[value]["id"]
    chart = {
        "type": "chart", "chart_type": kind,
        "x_axis": group(schema, category, "month" if ident == "C03" else "day"),
        "y_axis": aggregation, "sort": "y_descending" if kind == "bar" else "x_ascending",
        "color_theme": "teal", "height": "large", "show_data_labels": True,
        "axis_labels": "both", "grid_lines": "horizontal",
        "hide_empty_groups": True, "legend_position": "bottom" if subgroup else "off",
        "caption": "최근 12주",
    }
    if subgroup:
        chart["stack_by"] = group(schema, subgroup)
        chart["color_theme"] = "colorful"
    if kind == "line":
        chart.update(smooth_line=False, hide_line_fill_area=True, cumulative=False)
        if ident.startswith("C10"):
            chart["reference_lines"] = [{"value": 100, "label": "기준 수행 = 100", "color": "gray", "dash_style": "dash"}]
    if kind in {"bar", "column"}:
        chart["y_axis_min"] = 0
        if subgroup:
            chart["group_style"] = "side_by_side" if ident == "C03" else "normal"
    if kind == "donut":
        chart.update(donut_labels="name_and_value", legend_position="side", color_theme="colorful")
    if scope.get("observational") and ident in {"C01", "C02", "C03", "C08"}:
        chart["caption"] += " · 조건 미확인: 발전 비교 제외"
    if ident == "C06T" and native:
        chart["caption"] += " · 순위 갱신 " + config["top10_updated_at"][:10]
    return {
        "name": title, "type": "chart",
        "filter": {"and": filters}, "configuration": chart,
        "quick_filters": {},
    }
