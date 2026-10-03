import copy
from datetime import date
import tempfile
from pathlib import Path
import unittest

from notion.charts import SPECS, payload
from notion.deploy import Deploy, HOME, ROOT_TITLE, FIELD_NAMES, FIELD_TYPES, ROLE_FIELDS, contains, properties, save
from notion.metrics import build


class StateFileTests(unittest.TestCase):
    def test_save_preserves_existing_parent_permissions_and_protects_file(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            parent.chmod(0o755)
            path = parent / 'state.json'
            save(path, {'checkpoint': 'safe'})
            self.assertEqual(parent.stat().st_mode & 0o777, 0o755)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


def returned_properties(encoded):
    return {name: {"type": next(iter(prop)), **copy.deepcopy(prop)} for name, prop in encoded.items()}


class FakeClient:
    def __init__(self):
        self.records = {}
        self.calls = []
    def data(self, source):
        return [copy.deepcopy(row) for row in self.records.values()
                if row["parent"]["data_source_id"] == source and not row.get("in_trash")]
    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "GET":
            return copy.deepcopy(self.records[path.rsplit("/", 1)[-1]])
        if method == "POST":
            identifier = "derived-" + str(len(self.records) + 1)
            row = {"id": identifier, "parent": copy.deepcopy(body["parent"]),
                   "properties": returned_properties(body["properties"]), "in_trash": False}
            self.records[identifier] = row
            return copy.deepcopy(row)
        if method == "PATCH":
            row = self.records[path.rsplit("/", 1)[-1]]
            if "properties" in body:
                row["properties"].update(returned_properties(body["properties"]))
            if "in_trash" in body:
                row["in_trash"] = body["in_trash"]
            return copy.deepcopy(row)
        raise AssertionError("Unexpected method")


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.client = FakeClient()
        self.deployment = Deploy(self.client, {"parent_page_id": "parent"},
                                 Path(self.directory.name) / "state.json")
        self.deployment.state["databases"]["sessions"] = {"data_source_id": "derived-source"}
        self.rows = [{"key": "original-session", "date": "2026-09-30", "week": "2026-09-28",
                      "split": "Pull", "done": True, "original_session": ["original-session"]}]

    def test_removed_view_is_not_recreated_on_the_next_apply(self):
        self.deployment.config['disabled_views'] = ['C10']
        self.deployment.ensure_view('C10', '비교 분석', 'progress', {})
        self.assertEqual(self.client.calls, [])
        self.assertNotIn('C10', self.deployment.state['views'])

    def test_home_view_targets_the_entry_page(self):
        self.deployment.state['root'] = 'settings'
        self.assertEqual(self.deployment.page_target(HOME), 'parent')
        self.assertEqual(self.deployment.page_target(ROOT_TITLE), 'settings')

    def test_compact_status_updates_only_the_settings_status(self):
        self.deployment.config['compact_layout'] = True
        self.deployment.state['status_blocks'] = {'운동 성과': 'performance-status', '관리·집계': 'settings-status'}
        self.client.records['settings-status'] = {'id': 'settings-status'}
        self.deployment.status('갱신 완료', success=True)
        self.assertEqual([path for _, path, _ in self.client.calls], ['/blocks/settings-status'])

    def test_second_sync_does_not_create_or_update_unchanged_rows(self):
        first = self.deployment.sync_rows("sessions", self.rows)
        second = self.deployment.sync_rows("sessions", self.rows)
        self.assertEqual(first, {"created": 1, "updated": 0, "archived": 0})
        self.assertEqual(second, {"created": 0, "updated": 0, "archived": 0})

    def test_source_deletion_archives_only_the_projection_and_restores_same_row(self):
        self.deployment.sync_rows("sessions", self.rows)
        identifier = next(iter(self.client.records))
        result = self.deployment.sync_rows("sessions", [])
        self.assertEqual(result["archived"], 1)
        self.assertTrue(self.client.records[identifier]["in_trash"])
        restored = self.deployment.sync_rows("sessions", self.rows)
        self.assertEqual(restored["created"], 0)
        self.assertEqual(len(self.client.records), 1)
        self.assertFalse(self.client.records[identifier]["in_trash"])
        self.assertFalse(any(path.endswith("original-session") for _, path, _ in self.client.calls))

    def test_manual_unowned_projection_rows_are_preserved(self):
        props = properties(self.rows[0])
        props["Key"] = {"rich_text": []}
        self.client.request("POST", "/pages", {"parent": {"data_source_id": "derived-source"}, "properties": props})
        self.deployment.sync_rows("sessions", [])
        self.assertFalse(next(iter(self.client.records.values()))["in_trash"])

    def test_duplicate_desired_keys_fail_before_a_write(self):
        with self.assertRaisesRegex(ValueError, "Duplicate desired"):
            self.deployment.sync_rows("sessions", self.rows * 2)
        self.assertEqual(self.client.records, {})

    def test_manual_row_with_its_own_key_is_preserved(self):
        props = properties(self.rows[0])
        props["Key"] = {"rich_text": [{"type": "text", "text": {"content": "manual-key"}}]}
        props["Managed"] = {"checkbox": False}
        self.client.request("POST", "/pages", {"parent": {"data_source_id": "derived-source"}, "properties": props})
        self.deployment.sync_rows("sessions", [])
        self.assertFalse(next(iter(self.client.records.values()))["in_trash"])

    def test_date_move_updates_the_existing_derived_row(self):
        self.deployment.sync_rows("sessions", self.rows)
        moved = [{**self.rows[0], "date": "2026-10-05", "week": "2026-10-05"}]
        result = self.deployment.sync_rows("sessions", moved)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["created"], 0)

    def test_api_generated_reference_line_id_is_allowed_in_read_back(self):
        expected = {"reference_lines": [{"value": 100, "label": "기준", "color": "gray", "dash_style": "dash"}]}
        actual = {"reference_lines": [{"id": "server-id", **expected["reference_lines"][0]}], "other": None}
        self.assertTrue(contains(actual, expected))
        actual["reference_lines"][0]["value"] = 90
        self.assertFalse(contains(actual, expected))

    def test_existing_linked_view_recovered_without_block_type_assumptions(self):
        body = {"name": "NFT C01 · chart", "type": "chart", "configuration": {"type": "chart", "chart_type": "line"}}
        view = {"id": "existing-view", "parent": {"database_id": "linked-container"},
                "data_source_id": "derived-source", **body}
        calls = []
        class ViewClient:
            def pages(self, path, query=None, method=None):
                self.assert_source = query
                if path != "/views":
                    raise AssertionError("Recovery must use the documented Views API")
                yield {"id": "existing-view"}
            def request(self, method, path, payload=None):
                calls.append((method, path))
                if method != "GET":
                    raise AssertionError("Unchanged existing view must not be recreated or patched")
                if path.startswith("/views/"):
                    return copy.deepcopy(view)
                return {"parent": {"page_id": "target-page"}}
        self.deployment.client = ViewClient()
        self.deployment.state["pages"]["운동 성과"] = "target-page"
        self.assertEqual(self.deployment.ensure_view("C01", "운동 성과", "sessions", body), "existing-view")
        self.assertFalse(any(method == "POST" for method, path in calls))


class ChartTests(unittest.TestCase):
    def setUp(self):
        self.schemas = {role: {"Name": {"type": "title", "id": "title"},
                        **{FIELD_NAMES[field]: {"type": FIELD_TYPES[field], "id": FIELD_NAMES[field]}
                           for field in fields}}
                        for role, fields in ROLE_FIELDS.items()}
        self.config = {"strength_scope": {"exercise_id": "exercise-uuid", "condition": "same", "fixed_load": 140},
                       "pullup_scope": {"exercise_id": "pullup-uuid", "condition": "bodyweight-neutral"}}

    def body(self, identifier):
        spec = next(item for item in SPECS if item[0] == identifier)
        return payload(spec, self.schemas[spec[2]], self.config, now=date(2026, 10, 3))

    def test_all_ten_candidates_have_native_chart_definitions(self):
        covered = {spec[0][:3] for spec in SPECS}
        self.assertEqual(covered, {f"C{i:02}" for i in range(1, 11)})
        for spec in SPECS:
            body = payload(spec, self.schemas[spec[2]], self.config, now=date(2026, 10, 3))
            self.assertEqual(body["type"], "chart")
            self.assertIn(body["configuration"]["chart_type"], {"line", "bar", "column", "donut"})
            self.assertNotIn("dashboard", str(body))

    def test_rep_band_maxima_are_side_by_side_not_summed_or_stacked(self):
        chart = self.body("C03")["configuration"]
        self.assertEqual(chart["y_axis"]["aggregator"], "max")
        self.assertEqual(chart["group_style"], "side_by_side")
        self.assertEqual(chart["x_axis"]["group_by"], "month")

    def test_weekly_count_and_type_breakdown_are_separate_views(self):
        self.assertIsNone(next(spec[7] for spec in SPECS if spec[0] == "C04A"))
        chart = self.body("C04B")["configuration"]
        self.assertEqual(chart["y_axis"], {"aggregator": "count"})
        self.assertEqual(chart["stack_by"]["property_id"], "Set Type")

    def test_comparison_modes_have_different_filters_and_baseline_reference(self):
        weighted = self.body("C10")
        pullup = self.body("C10P")
        self.assertNotEqual(weighted["filter"], pullup["filter"])
        self.assertEqual(weighted["configuration"]["reference_lines"][0]["value"], 100)

    def test_fixed_load_filter_and_explicit_condition_are_present(self):
        conditions = self.body("C02")["filter"]["and"]
        self.assertIn({"property": "Load", "number": {"equals": 140}}, conditions)
        self.assertIn({"property": "Condition", "rich_text": {"equals": "same"}}, conditions)

    def test_unspecified_scope_does_not_mix_exercises(self):
        self.config["strength_scope"] = {}
        body = self.body("C01")
        self.assertIn({"property": "Original Exercise", "relation": {"is_empty": True}}, body["filter"]["and"])

    def test_top_ten_is_an_explicit_separate_view(self):
        self.assertIn({"property": "Top 10", "checkbox": {"equals": True}}, self.body("C06T")["filter"]["and"])


if __name__ == "__main__":
    unittest.main()
