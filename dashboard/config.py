"""Local dashboard configuration and read-only migration from Notion tooling.

Only connection metadata is copied. A token always comes from the server's
environment, and neither a migration nor validation writes to Notion.
"""
from __future__ import annotations

import copy
from functools import wraps
import json
import os
from pathlib import Path
import re
import tempfile
import uuid
from urllib.parse import unquote

from notion.api import Client, NotionError
from notion.source import value as source_value


class ConfigError(ValueError):
    """An actionable configuration error without credentials or raw records."""


REQUIRED_PROPERTIES = {
    "sessions": {"date", "split", "done"},
    "sets": {"session", "exercise", "load", "reps", "set_type", "done"},
    "exercises": {"label", "muscle"},
}
PROPERTY_TYPES = {
    "sessions": {
        "date": {"date", "formula"}, "split": {"select", "status", "rich_text", "formula"},
        "done": {"checkbox", "status", "select"},
    },
    "sets": {
        "session": {"relation"}, "exercise": {"relation"},
        "load": {"number", "formula"}, "reps": {"number", "formula"},
        "set_type": {"select", "status", "rich_text", "formula"},
        "done": {"checkbox", "status", "select"},
        "historical_condition": {"rich_text", "select", "formula"},
        "condition_confirmed": {"checkbox"},
        "pullup_mode": {"rich_text", "select", "formula"},
    },
    "exercises": {
        "label": {"title", "rich_text", "formula"},
        "muscle": {"select", "rich_text", "formula"},
    },
}

# These are explicit field choices for inspected optional schemas. Source IDs
# remain local, and import_legacy checks each choice against the live schema.
OPTIONAL_PROPERTIES = {
    "daily_health": {
        "label": "Day", "date": "Date", "weight_kg": "Morning Weight (kg)",
        "calories_kcal": "Calories (kcal)", "protein_g": "Protein (g)",
        "carbohydrate_g": "Carbohydrate (g)", "fat_g": "Fat (g)",
        "steps": "Steps", "sleep_hr": "Sleep (hr)", "waist_cm": "Waist (cm)",
        "verified": "Verified",
    },
    "body_composition": {
        "label": "Measurement", "date": "Measured At", "weight_kg": "Weight (kg)",
        "body_fat_percent": "Body Fat Percentage (%)",
        "body_fat_mass_kg": "Body Fat Mass (kg)",
        "skeletal_muscle_mass_kg": "Skeletal Muscle Mass (kg)",
        "waist_cm": "Waist (cm)", "condition": "Measurement Condition",
        "device": "Device / Location", "verified": "Verified",
    },
    "weekly_reviews": {
        "label": "Week", "date": "Week Start", "week_end": "Week End",
        "review_as_of": "Review As Of", "reviewed": "Reviewed",
        "average_weight_kg": "Average Weight (kg)",
        "average_calories_kcal": "Average Calories", "average_protein_g": "Average Protein (g)",
        "average_steps": "Average Steps", "average_sleep_hr": "Average Sleep (hr)",
        "strength_sessions": "Strength Sessions", "coverage_percent": "Coverage (%)",
        "wins": "Wins", "risks": "Risks", "next_week_adjustment": "Next Week Adjustment",
    },
    "plans_targets": {
        "label": "Plan", "status": "Status", "date": "Start Date", "end_date": "End Date",
        "protein_target_g": "Protein Target", "steps_target": "Steps Target",
        "training_calories_kcal": "Training Calories", "rest_calories_kcal": "Rest Calories",
        "weekly_strength_target": "Weekly Strength Target", "weekly_cardio_target": "Weekly Cardio Target",
    },
}
OPTIONAL_SOURCE_NAMES = {
    "daily_health": "Daily Health Log", "body_composition": "Body Composition",
    "weekly_reviews": "Weekly Reviews", "plans_targets": "Plans & Targets",
}
_OPTIONAL_TYPES = {"title", "rich_text", "number", "date", "select", "status", "checkbox"}
_CONFIG_KEYS = {
    "schema_version", "data_sources", "properties", "completed_values", "set_type_values",
    "pullup_exercise_ids", "historical_condition_verified", "load_unit", "load_unit_verified",
    "external_load_verified", "pullup_mode_verified", "pullup_mode_values", "optional_sources",
}


def _client_or_error(client):
    if client is not None:
        return client
    if not os.environ.get("NOTION_TOKEN"):
        raise ConfigError("NOTION_TOKEN이 없습니다. 로컬 서버 환경 변수에 Notion 토큰을 설정하세요.")
    return Client()


def _translate_notion_errors(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except NotionError:
            # Do not print API body text or a transport's credential-containing
            # diagnosis, even when an alternate client is supplied by a caller.
            raise ConfigError("Notion 원본을 확인하지 못했습니다. 토큰·공유 권한·연결 상태를 확인하세요.") from None
    return guarded


def _read_json(path, *, limit=2_000_000):
    path = Path(path)
    try:
        if path.stat().st_size > limit:
            raise ConfigError("설정 파일이 너무 큽니다. 연결 정보만 저장하세요.")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        if isinstance(error, ConfigError):
            raise
        raise ConfigError("설정 JSON을 읽을 수 없습니다. 파일 경로와 형식을 확인하세요.") from None
    if not isinstance(value, dict):
        raise ConfigError("설정은 JSON 객체여야 합니다.")
    return value


def _identifier(value, field):
    try:
        if not isinstance(value, str):
            raise ValueError
        uuid.UUID(value)
    except (ValueError, AttributeError):
        raise ConfigError(f"{field}에 확인한 데이터 소스 ID를 지정하세요.") from None
    return value


def _mapping(value, field, *, optional=False):
    if value is None and optional:
        return None
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, dict):
        identifier, name = value.get("id"), value.get("name")
        if not ((isinstance(identifier, str) and identifier.strip()) or
                (isinstance(name, str) and name.strip())):
            raise ConfigError(f"{field}의 속성 ID 또는 이름을 지정하세요.")
        mapped = {key: value[key] for key in ("id", "name", "type") if key in value}
        if any(not isinstance(item, str) or not item.strip() for item in mapped.values()):
            raise ConfigError(f"{field}의 속성 매핑 형식을 확인하세요.")
        return mapped
    raise ConfigError(f"{field}의 속성 ID 또는 이름을 지정하세요.")


def load_config(path):
    """Read local metadata; live schemas are verified again by the reader.

    Unknown management settings and credential fields are deliberately omitted.
    Optional health sources and unverified measurement semantics are valid.
    """
    raw = _read_json(path)
    config = {key: copy.deepcopy(value) for key, value in raw.items() if key in _CONFIG_KEYS}
    config.setdefault("schema_version", 1)
    if config["schema_version"] != 1:
        raise ConfigError("지원하지 않는 설정 버전입니다. 연결 설정을 다시 가져오세요.")
    if not isinstance(config.get("data_sources"), dict) or not isinstance(config.get("properties"), dict):
        raise ConfigError("data_sources와 properties 매핑이 필요합니다.")
    # Limit returned mappings to inspected fields, rather than passing arbitrary
    # JSON or old page-management information to the dashboard.
    sources, properties = {}, {}
    for role, required in REQUIRED_PROPERTIES.items():
        sources[role] = _identifier(config["data_sources"].get(role), "data_sources." + role)
        mapped = config["properties"].get(role)
        if not isinstance(mapped, dict):
            raise ConfigError(f"properties.{role} 매핑이 필요합니다.")
        properties[role] = {
            field: _mapping(mapped.get(field), f"properties.{role}.{field}", optional=field not in required)
            for field in PROPERTY_TYPES[role] if field in required or field in mapped
        }
    config["data_sources"], config["properties"] = sources, properties
    completed = config.get("completed_values", {})
    if not isinstance(completed, dict):
        raise ConfigError("completed_values는 세션·세트별 완료 값 목록이어야 합니다.")
    config["completed_values"] = {}
    for role in ("sessions", "sets"):
        values = completed.get(role, [])
        if not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values):
            raise ConfigError(f"completed_values.{role}는 문자열 목록이어야 합니다.")
        config["completed_values"][role] = values
    values = config.get("set_type_values", {})
    if not isinstance(values, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                         for k, v in values.items()):
        raise ConfigError("set_type_values는 원본 유형과 표시 유형의 문자열 매핑이어야 합니다.")
    config["set_type_values"] = values
    ids = config.setdefault("pullup_exercise_ids", [])
    if not isinstance(ids, list):
        raise ConfigError("pullup_exercise_ids는 종목 ID 목록이어야 합니다.")
    for identifier in ids:
        _identifier(identifier, "pullup_exercise_ids")
    for key in ("historical_condition_verified", "load_unit_verified", "external_load_verified", "pullup_mode_verified"):
        config.setdefault(key, False)
        if not isinstance(config[key], bool):
            raise ConfigError(f"{key}는 true 또는 false여야 합니다.")
    config.setdefault("load_unit", "kg")
    if config["load_unit"] != "kg":
        raise ConfigError("현재 분석은 kg 단위만 지원합니다. 단위를 확인하세요.")
    mode_values = config.setdefault("pullup_mode_values", {})
    if not isinstance(mode_values, dict) or any(not isinstance(k, str) or v not in {"bodyweight", "added", "assisted", "unknown"}
                                              for k, v in mode_values.items()):
        raise ConfigError("pullup_mode_values의 확인한 방식 매핑을 확인하세요.")
    optional = config.setdefault("optional_sources", {})
    if not isinstance(optional, dict):
        raise ConfigError("optional_sources는 선택 원본별 매핑이어야 합니다.")
    safe_optional = {}
    for role, source in optional.items():
        if role not in OPTIONAL_SOURCE_NAMES or not isinstance(source, dict):
            raise ConfigError("지원하지 않는 선택 원본 매핑입니다.")
        identifier = _identifier(source.get("data_source_id"), f"optional_sources.{role}.data_source_id")
        mapped = source.get("properties")
        if not isinstance(mapped, dict) or not mapped:
            raise ConfigError(f"optional_sources.{role}에 조회할 속성을 지정하세요.")
        if any(field not in OPTIONAL_PROPERTIES[role] for field in mapped):
            raise ConfigError(f"optional_sources.{role}의 지원 속성을 확인하세요.")
        safe_optional[role] = {"data_source_id": identifier, "properties": {
            field: _mapping(value, f"optional_sources.{role}.{field}") for field, value in mapped.items()
        }}
    config["optional_sources"] = safe_optional
    return config


def optional_sources_from_inventory(path):
    """Select connection metadata from the previously inspected local inventory.

    This never extracts or copies record values. Candidates still require live
    read-only schema validation in import_legacy before the new file is saved.
    """
    inventory = _read_json(path, limit=64_000_000)
    protected = inventory.get("protected_sources", {})
    if not isinstance(protected, dict):
        raise ConfigError("검증된 원본 목록이 없는 점검 파일입니다.")
    selected = {}
    for role, name in OPTIONAL_SOURCE_NAMES.items():
        matches = [identifier for identifier, source in protected.items()
                   if isinstance(source, dict) and source.get("name") == name]
        if len(matches) > 1:
            raise ConfigError(f"선택 원본 {role}이 중복됩니다. 실제 ID를 직접 선택하세요.")
        if matches:
            selected[role] = {"data_source_id": _identifier(matches[0], role),
                              "properties": copy.deepcopy(OPTIONAL_PROPERTIES[role])}
    return selected


def _resolve(schema, mapping, field, allowed):
    props = schema.get("properties", {})
    mapped_id = mapping.get("id") if isinstance(mapping, dict) else None
    mapped_name = mapping.get("name") if isinstance(mapping, dict) else mapping
    matches = [(name, prop) for name, prop in props.items()
               if (mapped_id and unquote(prop.get("id", "")) == unquote(mapped_id)) or
               (not mapped_id and name == mapped_name)]
    if len(matches) != 1:
        raise ConfigError(f"{field} 속성이 없거나 중복되었습니다. 원본 매핑을 확인하세요.")
    name, prop = matches[0]
    if prop.get("type") not in allowed or not prop.get("id"):
        raise ConfigError(f"{field} 속성 타입이 변경되었습니다. 원본 매핑을 확인하세요.")
    if isinstance(mapping, dict) and mapping.get("type") and mapping["type"] != prop["type"]:
        raise ConfigError(f"{field} 속성 타입이 변경되었습니다. 원본 매핑을 확인하세요.")
    return {"id": prop["id"], "name": name, "type": prop["type"]}, prop


@_translate_notion_errors
def import_legacy(source_path, target_path, client=None, *, overwrite=False, optional_sources=None):
    """Copy verified connection metadata to a separate private web config.

    Existing files require an explicit overwrite=True, and the source can never
    be overwritten. Only GET schema requests are made. The returned result has
    no source IDs, raw records, or credentials.
    """
    source_path, target_path = Path(source_path), Path(target_path)
    if source_path.resolve() == target_path.resolve():
        raise ConfigError("기존 Notion 설정과 웹 설정은 다른 파일이어야 합니다.")
    if target_path.exists() and not overwrite:
        raise ConfigError("웹 설정이 이미 있습니다. 덮어쓰기를 명시하거나 다른 경로를 선택하세요.")
    config = load_config(source_path)
    if optional_sources is not None:
        # Reuse the structural validator without serializing candidate data.
        if not isinstance(optional_sources, dict):
            raise ConfigError("선택 원본은 명시적인 메타데이터 매핑이어야 합니다.")
        config["optional_sources"] = copy.deepcopy(optional_sources)
    client = _client_or_error(client)
    schemas, resolved = {}, {}
    for role, identifier in config["data_sources"].items():
        schema = client.request("GET", "/data_sources/" + identifier)
        if schema.get("in_trash") or schema.get("archived"):
            raise ConfigError(f"{role} 원본을 사용할 수 없습니다. 공유·휴지통 상태를 확인하세요.")
        schemas[role] = schema
        resolved[role] = {}
        for field, mapping in config["properties"][role].items():
            if mapping is not None:
                resolved[role][field], _ = _resolve(schema, mapping, f"{role}.{field}", PROPERTY_TYPES[role][field])
        if role in {"sessions", "sets"}:
            _, done = _resolve(schema, resolved[role]["done"], f"{role}.done", PROPERTY_TYPES[role]["done"])
            if done["type"] in {"select", "status"}:
                allowed = {option["name"] for option in done[done["type"]].get("options", [])}
                config["completed_values"][role] = [v for v in config["completed_values"][role] if v in allowed]
                if not config["completed_values"][role]:
                    raise ConfigError(f"{role} 완료 값이 원본 상태와 일치하지 않습니다.")
    for field, role in (("session", "sessions"), ("exercise", "exercises")):
        _, prop = _resolve(schemas["sets"], resolved["sets"][field], "sets." + field, {"relation"})
        linked = prop.get("relation", {}).get("data_source_id")
        if not linked or uuid.UUID(_identifier(linked, "sets." + field)) != uuid.UUID(config["data_sources"][role]):
            raise ConfigError(f"sets.{field} 관계가 설정한 원본을 가리키지 않습니다.")
    config["properties"] = resolved
    if resolved["sets"]["load"]["name"] == "Load (kg)":
        config["load_unit"], config["load_unit_verified"] = "kg", True
    # An explicit existing verification may be retained; a label never proves
    # whether bodyweight or assistance was included in a particular old set.
    config["external_load_verified"] = config.get("external_load_verified") is True
    warnings = []
    if not config["load_unit_verified"]:
        warnings.append("중량 단위가 확인되지 않아 중량 기반 계산은 제한됩니다.")
    if not config["external_load_verified"]:
        warnings.append("외부중량 의미가 확인되지 않아 kg·회 볼륨은 제한됩니다.")
    verified_optional = {}
    for role, source in config["optional_sources"].items():
        if role not in OPTIONAL_PROPERTIES or not isinstance(source, dict):
            raise ConfigError("지원하지 않는 선택 원본 매핑입니다.")
        identifier = _identifier(source.get("data_source_id"), "optional_sources." + role)
        try:
            schema = client.request("GET", "/data_sources/" + identifier)
            if schema.get("in_trash") or schema.get("archived"):
                raise ConfigError("선택 원본이 휴지통에 있습니다.")
            selected = {}
            mappings = source.get("properties", {})
            if not isinstance(mappings, dict) or not mappings:
                raise ConfigError("선택 원본 속성이 없습니다.")
            for field, mapping in mappings.items():
                if field not in OPTIONAL_PROPERTIES[role]:
                    raise ConfigError("지원하지 않는 선택 원본 속성입니다.")
                selected[field], _ = _resolve(schema, mapping, role + "." + field, _OPTIONAL_TYPES)
            verified_optional[role] = {"data_source_id": identifier, "properties": selected}
        except Exception:
            # Optional source errors cannot publish credentials/API messages or
            # stop the successfully verified required workout connection.
            warnings.append(f"선택 원본 {role}은 검증되지 않아 연결하지 않았습니다.")
    config["optional_sources"] = verified_optional
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target_path.parent,
                                         prefix=".dashboard-config-", delete=False) as output:
            temporary = Path(output.name)
            os.chmod(temporary, 0o600)
            json.dump(config, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        if overwrite:
            os.replace(temporary, target_path)
        else:
            # A second process must not win a race by silently overwriting.
            os.link(temporary, target_path)
            temporary.unlink()
        temporary = None
    except FileExistsError:
        raise ConfigError("웹 설정이 이미 있습니다. 다른 경로를 선택하세요.") from None
    except OSError:
        raise ConfigError("웹 설정을 저장할 수 없습니다. 경로와 파일 권한을 확인하세요.") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {"path": str(target_path), "optional_sources": list(verified_optional), "warnings": warnings}


@_translate_notion_errors
def discover_config(target_path, client=None, *, overwrite=False):
    """Set up a new PC by inspecting uniquely named, accessible data sources.

    Names are discovery hints, never unchecked IDs. Every selected source and
    property is validated against the live schema before writing a private
    local file. Search/query POSTs are read-only Notion operations.
    """
    target_path = Path(target_path)
    if target_path.exists() and not overwrite:
        raise ConfigError("웹 설정이 이미 있습니다. 덮어쓰기를 명시하거나 다른 경로를 선택하세요.")
    client = _client_or_error(client)
    source_names = {"sessions": "Workout Sessions", "sets": "Exercise Sets", "exercises": "Exercise Library"}
    schemas, source_ids, warnings = {}, {}, []

    def find_source(role, name, required):
        matches = []
        for source in client.search(name):
            if source.get("object") != "data_source" or source.get("in_trash") or source.get("archived"):
                continue
            title = "".join(part.get("plain_text", part.get("text", {}).get("content", ""))
                            for part in source.get("title", []))
            if title == name:
                matches.append(source)
        # A repeated search result does not make an otherwise unique source
        # ambiguous. Different IDs with the same exact name do.
        matches = list({source["id"]: source for source in matches}.values())
        if len(matches) != 1:
            if required:
                raise ConfigError(f"{name} 원본을 하나로 확인할 수 없습니다. 공유 상태·중복 이름을 확인하세요.")
            if matches:
                warnings.append(f"선택 원본 {role}이 중복되어 자동 연결하지 않았습니다.")
            return None
        identifier = _identifier(matches[0].get("id"), role)
        schema = client.request("GET", "/data_sources/" + identifier)
        if schema.get("in_trash") or schema.get("archived"):
            if required:
                raise ConfigError(f"{name} 원본을 사용할 수 없습니다.")
            return None
        return identifier, schema

    for role, name in source_names.items():
        identifier, schema = find_source(role, name, True)
        source_ids[role], schemas[role] = identifier, schema
    properties = {
        "sessions": {"date": "Date", "split": "Split", "done": "Completed"},
        "sets": {"session": "Session", "exercise": "Exercise", "load": "Load (kg)",
                 "reps": "Reps", "set_type": "Set Type", "done": "Completed"},
        "exercises": {"label": "Exercise", "muscle": "Primary Muscle"},
    }
    for field, name in (("historical_condition", "NFT Measurement Condition"),
                        ("condition_confirmed", "NFT Condition Confirmed")):
        if name in schemas["sets"].get("properties", {}):
            properties["sets"][field] = name
    label_mapping, _ = _resolve(schemas["exercises"], properties["exercises"]["label"],
                                "exercises.label", PROPERTY_TYPES["exercises"]["label"])
    pullup_ids = []
    aliases = {"풀업", "패러럴그립풀업", "와이드그립풀업", "내로우그립풀업", "뉴트럴그립풀업",
               "pullup", "pullups", "parallelgrippullup", "widegrippullup", "narrowgrippullup",
               "neutralgrippullup", "weightedpullup", "assistedpullup", "풀업머신", "어시스티드풀업",
               "chinup", "chinups", "parallelgripchinup", "widegripchinup", "narrowgripchinup",
               "neutralgripchinup", "weightedchinup", "assistedchinup", "친업", "친업머신",
               "패러럴그립친업", "와이드그립친업", "내로우그립친업", "뉴트럴그립친업", "어시스티드친업"}
    # The explicit label identifies an exercise family only. It cannot prove
    # an old set's bodyweight/added/assisted mode, grip, or measurement basis.
    for row in client.pages("/data_sources/" + source_ids["exercises"] + "/query",
                            query={"filter_properties": label_mapping["id"]}):
        if row.get("in_trash") or row.get("archived"):
            continue
        matches = [prop for prop in row.get("properties", {}).values()
                   if unquote(prop.get("id", "")) == unquote(label_mapping["id"])]
        if len(matches) != 1:
            raise ConfigError("종목 이름 속성을 확인할 수 없습니다. 원본 매핑을 확인하세요.")
        try:
            label = source_value(matches[0])
        except (ValueError, TypeError, KeyError):
            raise ConfigError("종목 이름 형식을 확인할 수 없습니다. 원본 속성을 확인하세요.") from None
        if not isinstance(label, str):
            raise ConfigError("종목 이름은 문자열이어야 합니다. 원본 속성을 확인하세요.")
        normal = re.sub(r"[\s_-]", "", label).casefold()
        base = re.sub(r"\([^)]*\)", "", normal)
        if normal in aliases or base in aliases:
            pullup_ids.append(_identifier(row.get("id"), "pullup_exercise_ids"))
        elif any(family in normal for family in ("pullup", "풀업", "chinup", "친업")):
            warnings.append("풀업 후보 이름이 모호합니다. pullup_exercise_ids를 직접 확인하세요.")
    optional = {}
    for role, name in OPTIONAL_SOURCE_NAMES.items():
        try:
            found = find_source(role, name, False)
            if found:
                identifier, schema = found
                # Map actual optional fields only; never invent missing values.
                mapped = {field: name for field, name in OPTIONAL_PROPERTIES[role].items()
                          if name in schema.get("properties", {})}
                if "date" in mapped and "label" in mapped:
                    optional[role] = {"data_source_id": identifier, "properties": mapped}
                else:
                    warnings.append(f"선택 원본 {role}의 날짜·이름을 확인할 수 없어 연결하지 않았습니다.")
        except Exception:
            warnings.append(f"선택 원본 {role}은 접근되지 않아 연결하지 않았습니다.")
    seed = {
        "schema_version": 1, "data_sources": source_ids, "properties": properties,
        "completed_values": {"sessions": ["Complete", "Completed", "완료"], "sets": ["Complete", "Completed", "완료"]},
        "set_type_values": {name: name for name in ("Working", "Top Set", "Warm-up", "Back-off", "Drop Set", "Failure", "Technique")},
        "pullup_exercise_ids": sorted(set(pullup_ids)), "historical_condition_verified": False,
        "load_unit": "kg", "load_unit_verified": False, "external_load_verified": False,
        "pullup_mode_verified": False, "pullup_mode_values": {}, "optional_sources": {},
    }
    with tempfile.TemporaryDirectory(prefix="fitness-dashboard-setup-") as directory:
        source_path = Path(directory) / "connection.json"
        source_path.write_text(json.dumps(seed, ensure_ascii=False), encoding="utf-8")
        os.chmod(source_path, 0o600)
        result = import_legacy(source_path, target_path, client, overwrite=overwrite, optional_sources=optional)
    result["warnings"] = list(dict.fromkeys(warnings + result["warnings"]))
    return result
