"""Configuration setup verifies live metadata and preserves private inputs."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dashboard.config import (ConfigError, OPTIONAL_PROPERTIES, discover_config,
                              import_legacy, load_config, optional_sources_from_inventory)
from notion.api import NotionError


IDS = {"sessions": "10000000-0000-4000-8000-000000000001",
       "sets": "10000000-0000-4000-8000-000000000002",
       "exercises": "10000000-0000-4000-8000-000000000003"}
HEALTH_ID = "10000000-0000-4000-8000-000000000004"
PULLUP_ID = "20000000-0000-4000-8000-000000000001"


def prop(identifier, kind, **extra):
    return {"id": identifier, "type": kind, kind: {}, **extra}


def schemas():
    return {
        IDS["sessions"]: {"properties": {
            "Date": prop("date", "date"), "Split": prop("split", "select"),
            "Completed": prop("session-done", "checkbox"),
        }},
        IDS["sets"]: {"properties": {
            "Session": prop("session-link", "relation", relation={"data_source_id": IDS["sessions"]}),
            "Exercise": prop("exercise-link", "relation", relation={"data_source_id": IDS["exercises"]}),
            "Load (kg)": prop("load", "number"), "Reps": prop("reps", "number"),
            "Set Type": prop("set-type", "select"), "Completed": prop("set-done", "checkbox"),
            "NFT Measurement Condition": prop("condition", "rich_text"),
            "NFT Condition Confirmed": prop("confirmed", "checkbox"),
        }},
        IDS["exercises"]: {"properties": {
            "Exercise": prop("title", "title"), "Primary Muscle": prop("muscle", "select"),
        }},
        HEALTH_ID: {"properties": {
            name: prop("health-" + field, "title" if field == "label" else "date" if field == "date"
                       else "checkbox" if field == "verified" else "number")
            for field, name in OPTIONAL_PROPERTIES["daily_health"].items()
        }},
    }


def legacy():
    return {
        "parent_page_id": "preserve-the-existing-management-setting",
        "NOTION_TOKEN": "secret-do-not-copy", "data_sources": dict(IDS),
        "properties": {
            "sessions": {"date": "Date", "split": "Split", "done": "Completed"},
            "sets": {"session": "Session", "exercise": "Exercise", "load": "Load (kg)",
                     "reps": "Reps", "set_type": "Set Type", "done": "Completed",
                     "historical_condition": "NFT Measurement Condition", "condition_confirmed": "NFT Condition Confirmed"},
            "exercises": {"label": "Exercise", "muscle": "Primary Muscle"},
        },
        "completed_values": {"sessions": ["Completed"], "sets": ["Completed"]},
        "set_type_values": {"Working": "Working"}, "pullup_exercise_ids": [PULLUP_ID],
        "historical_condition_verified": True,
    }


class FakeClient:
    def __init__(self):
        self.schemas = schemas()
        self.calls = []
        self.search_results = {
            name: [{"object": "data_source", "id": IDS[role], "title": [{"plain_text": name}]}]
            for role, name in {"sessions": "Workout Sessions", "sets": "Exercise Sets", "exercises": "Exercise Library"}.items()
        }
        self.search_results["Daily Health Log"] = [{"object": "data_source", "id": HEALTH_ID,
                                                   "title": [{"plain_text": "Daily Health Log"}]}]
        self.exercise_records = [{"id": PULLUP_ID, "properties": {
            "Exercise": {"id": "title", "type": "title", "title": [{"plain_text": "Pull Up (parallel grip)"}]},
        }}]

    def request(self, method, path, payload=None, query=None):
        self.calls.append((method, path))
        if method != "GET" or not path.startswith("/data_sources/"):
            raise AssertionError("Configuration migration may only GET schemas")
        return copy.deepcopy(self.schemas[path.rsplit("/", 1)[1]])

    def search(self, name):
        self.calls.append(("POST", "/search"))
        return copy.deepcopy(self.search_results.get(name, []))

    def pages(self, path, payload=None, query=None, method="POST"):
        self.calls.append((method, path))
        if path != "/data_sources/" + IDS["exercises"] + "/query" or query != {"filter_properties": "title"}:
            raise AssertionError("Discovery should only query the library title")
        yield from copy.deepcopy(self.exercise_records)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.source = Path(self.directory.name) / "notion-config.json"
        self.target = Path(self.directory.name) / "private" / "dashboard-config.json"
        self.data = legacy()
        self.client = FakeClient()
        self.write()

    def write(self):
        self.source.write_text(json.dumps(self.data), encoding="utf-8")

    def test_migration_verifies_ids_types_and_never_changes_source_or_serializes_secret(self):
        before = self.source.read_bytes()
        result = import_legacy(self.source, self.target, self.client)
        config = load_config(self.target)
        self.assertEqual(before, self.source.read_bytes())
        self.assertEqual({"id": "load", "name": "Load (kg)", "type": "number"}, config["properties"]["sets"]["load"])
        self.assertTrue(config["load_unit_verified"])
        self.assertFalse(config["external_load_verified"])
        self.assertTrue(config["historical_condition_verified"])
        self.assertNotIn("secret-do-not-copy", self.target.read_text())
        self.assertNotIn("parent_page_id", config)
        self.assertEqual(0o600, self.target.stat().st_mode & 0o777)
        self.assertTrue(all(method == "GET" for method, _ in self.client.calls))
        self.assertEqual(str(self.target), result["path"])

    def test_existing_new_file_is_not_overwritten_without_explicit_argument(self):
        self.target.parent.mkdir()
        self.target.write_text("keep this", encoding="utf-8")
        with self.assertRaises(ConfigError):
            import_legacy(self.source, self.target, self.client)
        self.assertEqual("keep this", self.target.read_text())
        self.assertEqual([], self.client.calls)
        import_legacy(self.source, self.target, self.client, overwrite=True)
        self.assertIn("data_sources", load_config(self.target))

    def test_legacy_cannot_be_overwritten_even_explicitly(self):
        with self.assertRaises(ConfigError):
            import_legacy(self.source, self.source, self.client, overwrite=True)
        self.assertEqual([], self.client.calls)

    def test_other_load_name_does_not_prove_kg_or_external_load(self):
        self.data["properties"]["sets"]["load"] = "Resistance"
        self.client.schemas[IDS["sets"]]["properties"]["Resistance"] = self.client.schemas[IDS["sets"]]["properties"].pop("Load (kg)")
        self.write()
        result = import_legacy(self.source, self.target, self.client)
        config = load_config(self.target)
        self.assertFalse(config["load_unit_verified"])
        self.assertFalse(config["external_load_verified"])
        self.assertEqual(2, len(result["warnings"]))

    def test_explicit_verified_unit_metadata_can_be_preserved(self):
        self.data.update({"load_unit": "kg", "load_unit_verified": True, "external_load_verified": True})
        self.data["properties"]["sets"]["load"] = "Resistance"
        self.client.schemas[IDS["sets"]]["properties"]["Resistance"] = self.client.schemas[IDS["sets"]]["properties"].pop("Load (kg)")
        self.write()
        import_legacy(self.source, self.target, self.client)
        self.assertTrue(load_config(self.target)["load_unit_verified"])
        self.assertTrue(load_config(self.target)["external_load_verified"])

    def test_property_id_survives_rename_and_type_change_fails(self):
        self.data["properties"]["sets"]["load"] = {"id": "load", "name": "Load (kg)", "type": "number"}
        props = self.client.schemas[IDS["sets"]]["properties"]
        props["Renamed Load"] = props.pop("Load (kg)")
        self.write()
        import_legacy(self.source, self.target, self.client)
        self.assertEqual("Renamed Load", load_config(self.target)["properties"]["sets"]["load"]["name"])
        props["Renamed Load"]["type"] = "formula"
        with self.assertRaises(ConfigError):
            import_legacy(self.source, self.target, self.client, overwrite=True)

    def test_wrong_relation_prevents_new_config(self):
        self.client.schemas[IDS["sets"]]["properties"]["Session"]["relation"]["data_source_id"] = IDS["exercises"]
        with self.assertRaises(ConfigError):
            import_legacy(self.source, self.target, self.client)
        self.assertFalse(self.target.exists())

    def test_completed_select_values_are_checked_against_live_options(self):
        self.client.schemas[IDS["sessions"]]["properties"]["Completed"] = prop("session-done", "status", status={"options": [{"name": "Done"}]})
        with self.assertRaises(ConfigError):
            import_legacy(self.source, self.target, self.client)
        self.data["completed_values"]["sessions"] = ["Done", "Obsolete"]
        self.write()
        import_legacy(self.source, self.target, self.client)
        self.assertEqual(["Done"], load_config(self.target)["completed_values"]["sessions"])

    def test_optional_health_is_explicit_scalar_metadata_not_record_values(self):
        result = import_legacy(self.source, self.target, self.client, optional_sources={
            "daily_health": {"data_source_id": HEALTH_ID, "properties": OPTIONAL_PROPERTIES["daily_health"]},
        })
        self.assertEqual(["daily_health"], result["optional_sources"])
        source = load_config(self.target)["optional_sources"]["daily_health"]
        self.assertEqual("health-weight_kg", source["properties"]["weight_kg"]["id"])
        self.assertNotIn("records", self.target.read_text())

    def test_optional_failure_is_safe_and_does_not_stop_required_setup(self):
        result = import_legacy(self.source, self.target, self.client, optional_sources={
            "daily_health": {"data_source_id": HEALTH_ID, "properties": {"weight_kg": "Missing"}},
        })
        self.assertEqual([], result["optional_sources"])
        self.assertEqual({}, load_config(self.target)["optional_sources"])
        self.assertTrue(any("daily_health" in warning for warning in result["warnings"]))

    def test_inventory_candidates_only_copy_connection_metadata(self):
        inventory = Path(self.directory.name) / "inventory.json"
        inventory.write_text(json.dumps({"protected_sources": {
            HEALTH_ID: {"name": "Daily Health Log", "rows": [{"private_value": "never copy"}]},
        }}))
        sources = optional_sources_from_inventory(inventory)
        self.assertNotIn("never copy", json.dumps(sources))
        self.assertEqual(HEALTH_ID, sources["daily_health"]["data_source_id"])

    def test_required_uuid_and_boolean_semantics_validated(self):
        self.data["load_unit_verified"] = "true"
        self.write()
        with self.assertRaises(ConfigError):
            load_config(self.source)
        self.data.pop("load_unit_verified")
        self.data["data_sources"]["sets"] = "an unverified md URL"
        self.write()
        with self.assertRaises(ConfigError):
            load_config(self.source)

    def test_first_pc_discovery_verifies_source_names_and_pullup_family_only(self):
        result = discover_config(self.target, self.client)
        config = load_config(self.target)
        self.assertEqual(IDS, config["data_sources"])
        self.assertEqual([PULLUP_ID], config["pullup_exercise_ids"])
        self.assertFalse(config["historical_condition_verified"])
        self.assertFalse(config["pullup_mode_verified"])
        self.assertFalse(config["external_load_verified"])
        self.assertEqual(["daily_health"], result["optional_sources"])
        self.assertTrue(all(method == "GET" or path == "/search" or path.endswith("/query")
                            for method, path in self.client.calls))

    def test_chinup_variants_are_family_only_and_lat_pulldown_is_excluded(self):
        labels = ["Chin-up", "Parallel-grip Chin Up", "친 업 (중립 그립)", "Lat pulldown"]
        identifiers = [f"20000000-0000-4000-8000-{index:012d}" for index in range(2, 6)]
        self.client.exercise_records.extend([
            {"id": identifier, "properties": {"Exercise": {
                "id": "title", "type": "title", "title": [{"plain_text": label}],
            }}} for identifier, label in zip(identifiers, labels)
        ])
        discover_config(self.target, self.client)
        config = load_config(self.target)
        self.assertEqual(sorted([PULLUP_ID, *identifiers[:3]]), config["pullup_exercise_ids"])
        self.assertNotIn(identifiers[3], config["pullup_exercise_ids"])
        self.assertFalse(config["pullup_mode_verified"])
        self.assertFalse(config["historical_condition_verified"])
        self.assertEqual({}, config["pullup_mode_values"])

    def test_discovery_fails_for_ambiguous_required_sources(self):
        self.client.search_results["Workout Sessions"].append({"object": "data_source", "id": HEALTH_ID,
                                                                "title": [{"plain_text": "Workout Sessions"}]})
        with self.assertRaises(ConfigError):
            discover_config(self.target, self.client)
        self.assertFalse(self.target.exists())

    def test_discovery_ignores_pages_partial_titles_and_archived_sources(self):
        self.client.search_results["Workout Sessions"].extend([
            {"object": "page", "id": HEALTH_ID, "title": [{"plain_text": "Workout Sessions"}]},
            {"object": "data_source", "id": HEALTH_ID, "title": [{"plain_text": "Old Workout Sessions"}]},
            {"object": "data_source", "id": HEALTH_ID, "archived": True, "title": [{"plain_text": "Workout Sessions"}]},
        ])
        discover_config(self.target, self.client)
        self.assertEqual(IDS, load_config(self.target)["data_sources"])

    def test_missing_server_token_produces_clean_setup_error(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ConfigError, "NOTION_TOKEN"):
                discover_config(self.target)
            with self.assertRaisesRegex(ConfigError, "NOTION_TOKEN"):
                import_legacy(self.source, self.target)
        self.assertFalse(self.target.exists())

    def test_api_errors_are_actionable_and_do_not_expose_transport_diagnosis(self):
        with patch.object(self.client, "request", side_effect=NotionError("secret-token-in-transport")):
            with self.assertRaises(ConfigError) as error:
                import_legacy(self.source, self.target, self.client)
        self.assertNotIn("secret-token", str(error.exception))
        self.assertFalse(self.target.exists())

    def test_live_date_formula_mapping_is_supported_without_type_coercion(self):
        self.client.schemas[IDS["sessions"]]["properties"]["Date"] = prop("date", "formula")
        import_legacy(self.source, self.target, self.client)
        self.assertEqual("formula", load_config(self.target)["properties"]["sessions"]["date"]["type"])

    def test_url_encoded_property_id_matches_live_stable_id(self):
        self.data["properties"]["sets"]["load"] = {"id": "load%3Aid", "name": "old name", "type": "number"}
        self.client.schemas[IDS["sets"]]["properties"]["Load (kg)"]["id"] = "load:id"
        self.write()
        import_legacy(self.source, self.target, self.client)
        self.assertEqual("load:id", load_config(self.target)["properties"]["sets"]["load"]["id"])


if __name__ == "__main__":
    unittest.main()
