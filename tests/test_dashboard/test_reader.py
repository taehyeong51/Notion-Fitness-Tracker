import copy
import time
import unittest
import uuid
from urllib.parse import parse_qs, urlencode
from unittest.mock import patch

from dashboard.reader import ReadDeadlineError, ReadOnlyClient, Reader, ReaderError, SourceChangedError, _RepeatedQuery


IDS = {role: str(uuid.uuid5(uuid.NAMESPACE_URL, role)) for role in ["sessions", "sets", "exercises", "optional"]}
SESSION = str(uuid.uuid5(uuid.NAMESPACE_URL, "session-row"))
EXERCISE = str(uuid.uuid5(uuid.NAMESPACE_URL, "exercise-row"))
SET = str(uuid.uuid5(uuid.NAMESPACE_URL, "set-row"))
EDITED = "2026-10-04T01:00:00.000Z"


class NoWait:
    def wait(self, deadline=None):
        pass


def property_value(identifier, kind, value):
    if kind in {"rich_text", "title"}:
        value = [{"plain_text": value}] if value is not None else []
    if kind in {"select", "status"}:
        value = {"name": value} if value is not None else None
    if kind == "date":
        value = {"start": value, "end": None} if value is not None else None
    if kind == "relation":
        value = [{"id": part} for part in value]
    return {"id": identifier, "type": kind, kind: value}


def fixture():
    config = {
        "data_sources": {role: IDS[role] for role in ["sessions", "sets", "exercises"]},
        "properties": {"sessions": {}, "sets": {}, "exercises": {}},
        "completed_values": {"sessions": [], "sets": []},
        "set_type_values": {"Working": "Working"},
        "pullup_exercise_ids": [], "historical_condition_verified": True,
        "load_unit": "kg", "load_unit_verified": True, "external_load_verified": False,
    }
    definitions = {
        "sessions": {"date": ("date", "2026-10-04"), "split": ("select", "Pull"), "done": ("checkbox", True)},
        "sets": {"session": ("relation", [SESSION]), "exercise": ("relation", [EXERCISE]),
                 "load": ("number", 140), "reps": ("number", 4), "set_type": ("select", "Unknown type"),
                 "done": ("checkbox", True), "historical_condition": ("rich_text", "barbell/verified"),
                 "condition_confirmed": ("checkbox", False)},
        "exercises": {"label": ("title", "데드 리프트"), "muscle": ("select", "Back")},
    }
    schemas, rows = {}, {}
    for role, fields in definitions.items():
        props, raw = {}, {}
        for field, (kind, value) in fields.items():
            identifier = role[:1] + field
            name = field.capitalize()
            schema = {"id": identifier, "type": kind, "name": name}
            if kind in {"select", "status"}:
                schema[kind] = {"options": [{"name": value}]}
            if kind == "relation":
                schema["relation"] = {"data_source_id": IDS["sessions" if field == "session" else "exercises"]}
            props[name] = schema
            raw[name] = property_value(identifier, kind, value)
            config["properties"][role][field] = {"id": identifier, "name": name, "type": kind}
        schemas[IDS[role]] = {"object": "data_source", "properties": props}
        identifier = {"sessions": SESSION, "sets": SET, "exercises": EXERCISE}[role]
        rows[IDS[role]] = [{"object": "page", "id": identifier, "last_edited_time": EDITED,
                            "url": "https://www.notion.so/" + identifier, "properties": raw}]
    return config, schemas, rows


class FakeClient:
    def __init__(self, schemas, rows):
        self.schemas, self.rows = schemas, rows
        self.calls = []
        self.query_counts = {}
        self.transform = None
        self.relations = {}

    def request(self, method, path, payload=None, query=None):
        self.calls.append((method, path, copy.deepcopy(payload), dict(query or {})))
        if path.startswith("/data_sources/"):
            identifier = path.split("/")[2]
            if method == "GET":
                return copy.deepcopy(self.schemas[identifier])
            self.query_counts[identifier] = self.query_counts.get(identifier, 0) + 1
            rows = copy.deepcopy(self.rows[identifier])
            if self.transform:
                rows = self.transform(identifier, self.query_counts[identifier], rows)
            cursor = int((payload or {}).get("start_cursor", "0"))
            next_cursor = cursor + 100 if len(rows) > cursor + 100 else None
            return {"results": rows[cursor:cursor + 100], "has_more": next_cursor is not None,
                    "next_cursor": str(next_cursor) if next_cursor is not None else None}
        if path.startswith("/pages/"):
            cursor = int((query or {}).get("start_cursor", "0"))
            rows = copy.deepcopy(self.relations[path])
            next_cursor = cursor + 100 if len(rows) > cursor + 100 else None
            return {"results": rows[cursor:cursor + 100], "has_more": next_cursor is not None,
                    "next_cursor": str(next_cursor) if next_cursor is not None else None}
        raise AssertionError(path)


class ReaderTests(unittest.TestCase):
    def reader(self):
        config, schemas, rows = fixture()
        fake = FakeClient(schemas, rows)
        return Reader(config, client=fake, limiter=NoWait()), fake

    def test_raw_fields_and_unconfirmed_condition_are_preserved(self):
        reader, fake = self.reader()
        snapshot = reader.read()
        row = snapshot["sets"][0]
        self.assertEqual(row["set_type"], "Unknown type")
        self.assertEqual(row["raw_set_type"], "Unknown type")
        self.assertIsNone(row["condition"])
        self.assertEqual(row["raw_condition"], "barbell/verified")
        self.assertEqual(row["load_unit"], "kg")
        self.assertFalse(row["external_load_verified"])
        self.assertEqual(snapshot["source_counts"], {"sessions": 1, "sets": 1, "exercises": 1})
        self.assertTrue(all(method in {"GET", "POST"} for method, *_ in fake.calls))

    def test_property_name_changes_keep_stable_id_mapping(self):
        reader, fake = self.reader()
        schema = fake.schemas[IDS["sets"]]["properties"]
        schema["Renamed Load"] = schema.pop("Load")
        values = fake.rows[IDS["sets"]][0]["properties"]
        values["Renamed Load"] = values.pop("Load")
        self.assertEqual(reader.read()["sets"][0]["load"], 140)

    def test_property_type_or_relation_target_change_is_rejected(self):
        reader, fake = self.reader()
        fake.schemas[IDS["sets"]]["properties"]["Load"]["type"] = "rich_text"
        with self.assertRaises(ReaderError):
            reader.read()
        reader, fake = self.reader()
        fake.schemas[IDS["sets"]]["properties"]["Session"]["relation"]["data_source_id"] = IDS["exercises"]
        with self.assertRaises(ReaderError):
            reader.read()

    def test_full_pagination_and_required_relations_only(self):
        reader, fake = self.reader()
        base = fake.rows[IDS["sets"]][0]
        fake.rows[IDS["sets"]] = [{**copy.deepcopy(base), "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"row-{i}"))} for i in range(101)]
        first = fake.rows[IDS["sets"]][0]
        first["properties"]["Session"]["has_more"] = True
        # An inverse relation is intentionally not requested or hydrated.
        first["properties"]["Unused inverse"] = {"id": "unused", "type": "relation", "relation": [], "has_more": True}
        relation_path = f"/pages/{first['id']}/properties/ssession"
        fake.relations[relation_path] = [{"object": "property_item", "relation": {"id": SESSION}}]
        snapshot = reader.read()
        self.assertEqual(len(snapshot["sets"]), 101)
        relation_calls = [path for _, path, *_ in fake.calls if path.startswith("/pages/")]
        self.assertEqual(relation_calls, [relation_path])

    def test_duplicate_ids_and_incomplete_queries_fail(self):
        reader, fake = self.reader()
        fake.rows[IDS["sets"]] *= 2
        with self.assertRaises(ReaderError):
            reader.read()
        reader, fake = self.reader()
        original = fake.request

        def incomplete(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[0] == "POST":
                result["request_status"] = {"type": "incomplete"}
            return result

        fake.request = incomplete
        with self.assertRaises(ReaderError):
            reader.read()

    def test_fingerprint_change_retries_once_then_fails(self):
        reader, fake = self.reader()
        def changing(identifier, count, rows):
            if identifier == IDS["sets"]:
                rows[0]["last_edited_time"] = f"2026-10-04T01:00:{count:02d}.000Z"
            return rows
        fake.transform = changing
        with self.assertRaises(SourceChangedError):
            reader.read()
        self.assertEqual(fake.query_counts[IDS["sets"]], 4)

    def test_changed_first_attempt_can_recover_with_fresh_scan(self):
        reader, fake = self.reader()
        def one_change(identifier, count, rows):
            if identifier == IDS["sets"] and count >= 2:
                rows[0]["last_edited_time"] = "2026-10-04T01:00:01.000Z"
            return rows
        fake.transform = one_change
        snapshot = reader.read()
        self.assertEqual(snapshot["sets"][0]["last_edited_time"], "2026-10-04T01:00:01.000Z")

    def test_pullup_zero_load_never_implies_bodyweight(self):
        reader, fake = self.reader()
        reader.config["pullup_exercise_ids"] = [EXERCISE]
        fake.rows[IDS["sets"]][0]["properties"]["Load"]["number"] = 0
        row = reader.read()["sets"][0]
        self.assertTrue(row["pullup"])
        self.assertEqual(row["pullup_mode"], "unknown")

    def test_date_ranges_are_preserved_as_invalid_date_diagnostic(self):
        reader, fake = self.reader()
        fake.rows[IDS["sessions"]][0]["properties"]["Date"]["date"]["end"] = "2026-10-05"
        row = reader.read()["sessions"][0]
        self.assertIsNone(row["date"])
        self.assertEqual(row["raw_date"]["end"], "2026-10-05")

    def test_optional_source_failure_does_not_hide_workout_snapshot(self):
        reader, fake = self.reader()
        reader.config["optional_sources"] = {"body_composition": {"data_source_id": IDS["optional"], "properties": {"weight": {"id": "weight"}}}}
        fake.schemas[IDS["optional"]] = {"properties": {}}
        snapshot = reader.read()
        self.assertEqual(snapshot["optional_sources"]["body_composition"]["state"], "failed")
        self.assertEqual(len(snapshot["sets"]), 1)

    def test_unsupported_notion_write_requests_never_reach_transport(self):
        _, schemas, rows = fixture()
        fake = FakeClient(schemas, rows)
        readonly = ReadOnlyClient(fake, NoWait())
        for method, path in [("PATCH", f"/pages/{SESSION}"), ("POST", "/pages"), ("POST", "/search"),
                             ("DELETE", f"/pages/{SESSION}"), ("POST", "/anything/query"), ("GET", "/users/me")]:
            with self.assertRaises(ReaderError):
                readonly.request(method, path)
        self.assertEqual(fake.calls, [])

    def test_deadline_prevents_even_the_first_request(self):
        reader, fake = self.reader()
        with self.assertRaises(ReadDeadlineError):
            reader.read(deadline=time.monotonic() - 1)
        self.assertEqual(fake.calls, [])

    def test_query_array_is_repeated_in_legacy_urlencode(self):
        query = _RepeatedQuery({"filter_properties": ["load", "reps"], "page_size": 100})
        self.assertEqual(parse_qs(urlencode(query))["filter_properties"], ["load", "reps"])

    def test_missing_token_has_actionable_server_configuration_error(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ReaderError, "NOTION_TOKEN"):
                Reader(fixture()[0])

    def test_current_api_notion_links_and_archive_flag(self):
        reader, fake = self.reader()
        fake.rows[IDS["sessions"]][0]["url"] = "https://app.notion.com/" + SESSION
        self.assertEqual(reader.read()["sessions"][0]["url"], "https://app.notion.com/" + SESSION)
        fake.rows[IDS["sets"]][0]["is_archived"] = True
        with self.assertRaises(ReaderError):
            reader.read()


if __name__ == "__main__":
    unittest.main()
