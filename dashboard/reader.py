"""Read-only Notion snapshots; no managed Notion projection is consulted."""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote
from urllib.request import urlopen

from notion.api import Client, NotionError


class ReaderError(RuntimeError):
    """A complete source snapshot could not be read safely."""


class ReadDeadlineError(ReaderError):
    pass


class SourceChangedError(ReaderError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _check_deadline(deadline):
    if deadline is not None and time.monotonic() >= deadline:
        raise ReadDeadlineError("Notion 조회 제한 시간을 초과했습니다. 다시 시도하세요.")


class RequestLimiter:
    """One process-wide gate, shared by all readers and retries."""

    def __init__(self, interval=0.5):
        self.interval = interval
        self._lock = threading.Lock()
        self._last = None

    def wait(self, deadline=None):
        with self._lock:
            _check_deadline(deadline)
            delay = 0 if self._last is None else max(0, self._last + self.interval - time.monotonic())
            if deadline is not None and time.monotonic() + delay >= deadline:
                raise ReadDeadlineError("Notion 조회 제한 시간을 초과했습니다. 다시 시도하세요.")
            if delay:
                time.sleep(delay)
            _check_deadline(deadline)
            self._last = time.monotonic()


_LIMITER = RequestLimiter()


class _RepeatedQuery(dict):
    """Legacy urlencode has no doseq; expand the documented array query here."""

    def items(self):
        for key, value in super().items():
            if isinstance(value, (list, tuple)):
                for item in value:
                    yield key, item
            else:
                yield key, value


def _uuid(value):
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        raise ReaderError("원본 데이터 소스 ID를 확인하세요.") from None


class ReadOnlyClient:
    """Narrow allowlist around the legacy API client, including its retry transport."""

    def __init__(self, client=None, limiter=None):
        self.deadline = None
        self.limiter = limiter or _LIMITER
        self._owns_client = client is None
        try:
            self.client = client or Client(transport=self._transport, sleeper=self._sleep)
        except NotionError:
            raise ReaderError("서버 환경에 NOTION_TOKEN을 설정하고 Notion 원본 접근 권한을 확인하세요.") from None

    def _sleep(self, seconds):
        _check_deadline(self.deadline)
        if self.deadline is not None and time.monotonic() + seconds >= self.deadline:
            raise ReadDeadlineError("Notion 조회 제한 시간을 초과했습니다. 다시 시도하세요.")
        time.sleep(seconds)
        _check_deadline(self.deadline)

    def _transport(self, request, timeout=30):
        self.limiter.wait(self.deadline)
        remaining = 30 if self.deadline is None else self.deadline - time.monotonic()
        _check_deadline(self.deadline)
        return urlopen(request, timeout=min(timeout, remaining))

    @staticmethod
    def _allowed(method, path, payload):
        # IDs are parsed, not merely matched by a loose /query suffix.
        parts = path.split("/")
        try:
            if len(parts) == 3 and parts[1] == "data_sources" and method == "GET":
                _uuid(parts[2])
                return payload is None
            if len(parts) == 4 and parts[1] == "data_sources" and parts[3] == "query" and method == "POST":
                _uuid(parts[2])
                return True
            if len(parts) == 5 and parts[1] == "pages" and parts[3] == "properties" and method == "GET":
                _uuid(parts[2])
                return bool(parts[4]) and payload is None and "/" not in unquote(parts[4])
        except ReaderError:
            pass
        return False

    def request(self, method, path, payload=None, query=None):
        if not self._allowed(method, path, payload):
            raise ReaderError("웹에서는 Notion 원본 조회만 허용합니다.")
        _check_deadline(self.deadline)
        if not self._owns_client:
            self.limiter.wait(self.deadline)
        try:
            result = self.client.request(method, path, payload, _RepeatedQuery(query or {}))
        except (ReadDeadlineError, ReaderError):
            raise
        except NotionError as error:
            # Legacy client already redacts credentials; never forward arbitrary transport bodies.
            message = str(error)
            if "401" in message or "403" in message or "404" in message:
                raise ReaderError("Notion 접근 권한과 원본 연결을 확인하세요.") from None
            raise ReaderError("Notion 통신에 실패했습니다. 잠시 후 다시 시도하세요.") from None
        _check_deadline(self.deadline)
        if not isinstance(result, dict):
            raise ReaderError("Notion 응답 형식을 확인할 수 없습니다.")
        return result

    def pages(self, path, payload=None, query=None, method="POST"):
        cursor = None
        seen_cursors = set()
        seen_ids = set()
        while True:
            body, params = dict(payload or {}), dict(query or {})
            target = params if method == "GET" else body
            target["page_size"] = 100
            if cursor:
                target["start_cursor"] = cursor
            result = self.request(method, path, None if method == "GET" else body, params)
            if result.get("request_status", {}).get("type") == "incomplete":
                raise ReaderError("원본 조회가 완료되지 않았습니다. 이전 데이터를 유지합니다.")
            rows = result.get("results")
            if not isinstance(rows, list):
                raise ReaderError("Notion 목록 응답이 올바르지 않습니다.")
            for row in rows:
                if not isinstance(row, dict):
                    raise ReaderError("Notion 레코드 형식을 확인할 수 없습니다.")
                # Property-item IDs repeat legitimately; page IDs must never repeat.
                if row.get("object") == "page":
                    identifier = row.get("id")
                    if not identifier or identifier in seen_ids:
                        raise ReaderError("원본 조회에 중복 또는 누락된 ID가 있습니다.")
                    seen_ids.add(identifier)
                    if row.get("in_trash") or row.get("archived") or row.get("is_archived"):
                        raise ReaderError("조회 중 원본이 휴지통으로 이동했습니다. 다시 조회하세요.")
                yield row
            if not result.get("has_more"):
                return
            cursor = result.get("next_cursor")
            if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
                raise ReaderError("원본 페이지 조회 커서가 올바르지 않습니다.")
            seen_cursors.add(cursor)


_EXPECTED = {
    "sessions": {"date": {"date", "formula"}, "split": {"select", "status", "rich_text", "formula"},
                 "done": {"checkbox", "select", "status"}},
    "sets": {"session": {"relation"}, "exercise": {"relation"}, "load": {"number", "formula"},
             "reps": {"number", "formula"}, "set_type": {"select", "status", "rich_text", "formula"},
             "done": {"checkbox", "select", "status"}},
    "exercises": {"label": {"title", "rich_text", "formula"}, "muscle": {"select", "rich_text", "formula"}},
}
_OPTIONAL_SET_TYPES = {"historical_condition": {"rich_text", "select", "formula"},
                       "condition_confirmed": {"checkbox"},
                       "pullup_mode": {"select", "rich_text", "formula"}}


def _prop_value(prop):
    kind = prop.get("type")
    data = prop.get(kind)
    if kind in {"title", "rich_text"}:
        return "".join(part.get("plain_text", part.get("text", {}).get("content", "")) for part in data or [])
    if kind in {"select", "status"}:
        return data.get("name") if isinstance(data, dict) else None
    if kind == "multi_select":
        return [item["name"] for item in data or []]
    if kind == "date":
        if data and data.get("end"):
            return {"start": data.get("start"), "end": data.get("end"), "time_zone": data.get("time_zone")}
        return data.get("start") if data else None
    if kind == "relation":
        if prop.get("has_more"):
            raise ReaderError("분석에 필요한 관계 조회가 완료되지 않았습니다.")
        return [item["id"] for item in data or []]
    if kind in {"number", "checkbox", "url", "string", "boolean", "email", "phone_number"}:
        return data
    if kind == "formula":
        return _prop_value(data or {})
    raise ReaderError("원본 속성 타입이 지원되지 않습니다. 매핑을 확인하세요.")


def _same_property_id(left, right):
    return unquote(str(left)) == unquote(str(right))


class Reader:
    def __init__(self, config_path_or_dict, client=None, limiter=None):
        if isinstance(config_path_or_dict, (str, Path)):
            try:
                config_path_or_dict = json.loads(Path(config_path_or_dict).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise ReaderError("로컬 연결 설정을 읽을 수 없습니다. 설정 파일을 확인하세요.") from None
        self.config = dict(config_path_or_dict)
        self.client = client if isinstance(client, ReadOnlyClient) else ReadOnlyClient(client, limiter)
        self._mapping = {}
        self._progress = None

    def _report(self, stage, **fields):
        if self._progress:
            self._progress({"stage": stage, **fields})

    def _resolve(self, schema, role, fields, expected):
        props = schema.get("properties", {})
        if not isinstance(props, dict):
            raise ReaderError("원본 속성 목록을 확인할 수 없습니다.")
        mapping = {}
        for field, acceptable in expected.items():
            configured = fields.get(field)
            if not configured:
                raise ReaderError(f"원본 속성 매핑을 확인하세요: {role}.{field}")
            if isinstance(configured, str):
                candidates = [item for name, item in props.items() if name == configured]
            elif isinstance(configured, dict) and configured.get("id"):
                candidates = [item for item in props.values() if _same_property_id(item.get("id"), configured["id"])]
            elif isinstance(configured, dict) and configured.get("name"):
                candidates = [item for name, item in props.items() if name == configured["name"]]
            else:
                raise ReaderError(f"원본 속성 ID를 확인하세요: {role}.{field}")
            if len(candidates) != 1 or candidates[0].get("type") not in acceptable:
                raise ReaderError(f"원본 속성이 변경되었습니다: {role}.{field}")
            actual = candidates[0]
            if isinstance(configured, dict) and configured.get("type") and configured["type"] != actual["type"]:
                raise ReaderError(f"원본 속성 타입이 변경되었습니다: {role}.{field}")
            mapping[field] = actual
        return mapping

    def _schemas(self):
        schemas = {}
        for role, required in _EXPECTED.items():
            identifier = _uuid(self.config.get("data_sources", {}).get(role))
            schema = self.client.request("GET", f"/data_sources/{identifier}")
            if schema.get("in_trash") or schema.get("archived") or schema.get("is_archived"):
                raise ReaderError("필수 원본이 휴지통에 있습니다. 연결을 확인하세요.")
            fields = self.config.get("properties", {}).get(role, {})
            expected = dict(required)
            if role == "sets":
                expected.update({key: kinds for key, kinds in _OPTIONAL_SET_TYPES.items() if fields.get(key)})
            mapping = self._resolve(schema, role, fields, expected)
            if role in {"sets", "sessions"}:
                done = mapping["done"]
                if done["type"] in {"status", "select"}:
                    values = self.config.get("completed_values", {}).get(role, [])
                    options = {item["name"] for item in done.get(done["type"], {}).get("options", [])}
                    if not set(values).intersection(options):
                        raise ReaderError(f"완료 상태 값을 확인하세요: {role}")
            if role == "sets":
                for field, target in [("session", "sessions"), ("exercise", "exercises")]:
                    linked = mapping[field].get("relation", {}).get("data_source_id")
                    if _uuid(linked) != _uuid(self.config["data_sources"][target]):
                        raise ReaderError(f"원본 관계 대상이 변경되었습니다: sets.{field}")
            self._mapping[role] = mapping
            schemas[role] = schema
        return schemas

    def _scan(self, role, identifier, mapping, fingerprint_only=False):
        query = {"filter_properties": [prop["id"] for prop in mapping.values()]}
        # Revalidation needs only IDs and edited times; filter to one scalar property.
        if fingerprint_only:
            query = {"filter_properties": [next(iter(mapping.values()))["id"]]}
        rows = list(self.client.pages(f"/data_sources/{_uuid(identifier)}/query", query=query))
        ids = set()
        for row in rows:
            if row.get("object") != "page" or not row.get("id") or not row.get("last_edited_time") or row["id"] in ids:
                raise ReaderError("원본 레코드 ID 또는 수정 시각이 올바르지 않습니다.")
            ids.add(row["id"])
            if row.get("in_trash") or row.get("archived") or row.get("is_archived"):
                raise ReaderError("조회 중 원본이 휴지통으로 이동했습니다. 다시 조회하세요.")
            if not fingerprint_only:
                for field, schema in mapping.items():
                    prop = self._property(row, schema)
                    if prop.get("type") != schema.get("type"):
                        raise ReaderError(f"조회 중 원본 속성 타입이 변경되었습니다: {role}.{field}")
                    if prop.get("type") == "relation" and prop.get("has_more"):
                        property_id = quote(unquote(str(schema["id"])), safe="")
                        items = list(self.client.pages(f"/pages/{_uuid(row['id'])}/properties/{property_id}", method="GET"))
                        relations = []
                        for item in items:
                            relation = item.get("relation")
                            if not isinstance(relation, dict) or not relation.get("id"):
                                raise ReaderError("관계 속성 조회가 올바르지 않습니다.")
                            relations.append(relation)
                        if len({item["id"] for item in relations}) != len(relations):
                            raise ReaderError("원본 관계 조회에 중복 ID가 있습니다.")
                        prop["relation"], prop["has_more"] = relations, False
        return rows

    @staticmethod
    def _property(row, schema):
        candidates = [prop for prop in row.get("properties", {}).values() if _same_property_id(prop.get("id"), schema["id"])]
        if len(candidates) != 1:
            raise ReaderError("원본 속성이 삭제되거나 조회되지 않았습니다. 매핑을 확인하세요.")
        return candidates[0]

    def _normalize(self, records):
        def get(role, row, field):
            schema = self._mapping[role].get(field)
            return _prop_value(self._property(row, schema)) if schema else None

        def common(row):
            return {"id": row["id"], "url": _notion_url(row.get("url")), "last_edited_time": row["last_edited_time"]}

        def done(role, row):
            raw = get(role, row, "done")
            return raw if isinstance(raw, bool) else raw in self.config.get("completed_values", {}).get(role, [])

        result = {"sessions": [], "sets": [], "exercises": [], "diagnostics": []}
        for row in records["sessions"]:
            date = get("sessions", row, "date")
            item = {**common(row), "date": date if not isinstance(date, dict) else None,
                    "split": get("sessions", row, "split"), "done": done("sessions", row)}
            if isinstance(date, dict):
                item.update(raw_date=date, date_issue="date_range")
                result["diagnostics"].append({"kind": "date_range", "source_id": row["id"]})
            result["sessions"].append(item)
        for row in records["exercises"]:
            result["exercises"].append({**common(row), "label": get("exercises", row, "label"),
                                        "muscle": get("exercises", row, "muscle")})
        pullup_ids = {_uuid(identifier) for identifier in self.config.get("pullup_exercise_ids", [])}
        verified_unit = self.config.get("load_unit") == "kg" and self.config.get("load_unit_verified") is True
        for row in records["sets"]:
            exercises = get("sets", row, "exercise")
            raw_type = get("sets", row, "set_type")
            raw_condition = get("sets", row, "historical_condition")
            condition_confirmed = (self.config.get("historical_condition_verified") is True
                                   and (not self._mapping["sets"].get("condition_confirmed")
                                        or get("sets", row, "condition_confirmed") is True))
            pullup = len(exercises) == 1 and _uuid(exercises[0]) in pullup_ids
            mode = self.config.get("pullup_mode_values", {}).get(get("sets", row, "pullup_mode"))
            if not pullup or self.config.get("pullup_mode_verified") is not True or mode not in {"bodyweight", "added", "assisted"}:
                mode = "unknown"
            result["sets"].append({**common(row), "session_ids": get("sets", row, "session"),
                                   "exercise_ids": exercises, "load": get("sets", row, "load"),
                                   "reps": get("sets", row, "reps"),
                                   "set_type": self.config.get("set_type_values", {}).get(raw_type, raw_type),
                                   "raw_set_type": raw_type, "done": done("sets", row),
                                   "condition": raw_condition if condition_confirmed and raw_condition else None,
                                   "raw_condition": raw_condition, "pullup": pullup, "pullup_mode": mode,
                                   "load_unit": "kg" if verified_unit else None,
                                   "external_load_verified": verified_unit and self.config.get("external_load_verified") is True})
        return result

    def _optional(self):
        result = {}
        for name, configured in self.config.get("optional_sources", {}).items():
            if not configured or not configured.get("data_source_id"):
                result[name] = {"state": "not_configured", "records": []}
                continue
            self._report("optional_source", source=name)
            try:
                identifier = _uuid(configured["data_source_id"])
                schema = self.client.request("GET", f"/data_sources/{identifier}")
                if schema.get("in_trash") or schema.get("archived") or schema.get("is_archived"):
                    raise ReaderError("선택 원본이 휴지통에 있습니다.")
                fields = configured.get("properties", {})
                if not fields:
                    raise ReaderError("선택 원본의 실제 측정 속성을 설정하세요.")
                scalar = {"title", "rich_text", "date", "number", "checkbox", "select", "status", "multi_select", "formula", "url"}
                mapping = self._resolve(schema, name, fields, {field: scalar for field in fields})
                rows = self._scan(name, identifier, mapping)
                second = self._scan(name, identifier, mapping, fingerprint_only=True)
                if _fingerprint(rows) != _fingerprint(second):
                    raise SourceChangedError("선택 원본이 조회 중 변경되었습니다. 다시 조회하세요.")
                normalized = [{"id": row["id"], "url": _notion_url(row.get("url")), "last_edited_time": row["last_edited_time"],
                               "values": {field: _prop_value(self._property(row, prop)) for field, prop in mapping.items()}} for row in rows]
                result[name] = {"state": "succeeded", "fetched_at": utc_now(), "records": normalized,
                                "source_max_last_edited_at": max((row["last_edited_time"] for row in rows), default=None)}
            except ReaderError as error:
                result[name] = {"state": "failed", "error": str(error), "records": []}
        return result

    def read(self, progress=None, deadline=None):
        self._progress = progress
        self.client.deadline = min(deadline, time.monotonic() + 120) if deadline is not None else time.monotonic() + 120
        started_at = utc_now()
        for attempt in range(1, 3):
            self._report("schema", attempt=attempt)
            self._schemas()
            records = {}
            for role in _EXPECTED:
                self._report("source", source=role, attempt=attempt)
                records[role] = self._scan(role, self.config["data_sources"][role], self._mapping[role])
                self._report("source_complete", source=role, count=len(records[role]), attempt=attempt)
            changed = False
            for role in _EXPECTED:
                self._report("recheck", source=role, attempt=attempt)
                second = self._scan(role, self.config["data_sources"][role], self._mapping[role], fingerprint_only=True)
                if _fingerprint(records[role]) != _fingerprint(second):
                    changed = True
            if changed:
                self._report("source_changed", attempt=attempt)
                if attempt == 2:
                    raise SourceChangedError("조회 중 기록이 변경되었습니다. 잠시 후 다시 읽으세요.")
                continue
            self._report("normalize")
            snapshot = self._normalize(records)
            snapshot["optional_sources"] = self._optional()
            _check_deadline(self.client.deadline)
            snapshot.update(started_at=started_at, fetched_at=utc_now(), source_counts={role: len(rows) for role, rows in records.items()},
                            source_fingerprint={role: _fingerprint(rows) for role, rows in records.items()},
                            source_max_last_edited_at=max((row["last_edited_time"] for rows in records.values() for row in rows), default=None))
            return snapshot
        raise SourceChangedError("조회 중 기록이 변경되었습니다. 다시 읽으세요.")


def _fingerprint(rows):
    canonical = sorted((row["id"], row["last_edited_time"]) for row in rows)
    return hashlib.sha256(json.dumps(canonical, separators=(",", ":")).encode()).hexdigest()


def _notion_url(url):
    from urllib.parse import urlparse

    parsed = urlparse(url or "")
    host = (parsed.hostname or "").lower()
    try:
        allowed_port = parsed.port in {None, 443}
    except ValueError:
        allowed_port = False
    notion_hosts = {"notion.so", "www.notion.so", "notion.site", "www.notion.site", "notion.com", "www.notion.com", "app.notion.com"}
    if (parsed.scheme == "https" and (host in notion_hosts or host.endswith(".notion.site")) and allowed_port
            and not parsed.username and not parsed.password):
        return url
    return None
