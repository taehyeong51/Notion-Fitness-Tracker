from datetime import date
import unittest

from notion.charts import payload, SPECS
from notion.native import adapt_filters


class NativeTests(unittest.TestCase):
    def test_native_period_is_live_formula_not_date_frozen_at_deploy(self):
        schema = {
            "Date": {"id": "date-id", "type": "formula", "result_type": "date"},
            "Week": {"id": "week-id", "type": "formula", "result_type": "date"},
            "Done": {"id": "done-id", "type": "formula", "result_type": "checkbox"},
            "Recent 12 Weeks": {"id": "recent-id", "type": "formula", "result_type": "checkbox"},
        }
        spec = next(item for item in SPECS if item[0] == "C04A")
        body = adapt_filters(payload(spec, schema, {}, now=date(2026, 10, 3), native=True), schema)
        self.assertEqual(body["filter"], {"and": [
            {"property": "done-id", "formula": {"checkbox": {"equals": True}}},
            {"property": "recent-id", "formula": {"checkbox": {"equals": True}}},
        ]})
        self.assertNotIn("2026-", str(body["filter"]))
        self.assertEqual(body["configuration"]["x_axis"]["group_by"]["type"], "date")
        self.assertNotIn("sort", body["configuration"]["x_axis"])

    def test_frequency_uses_native_unique_sessions_not_count_of_sets(self):
        schema = {name: {"id": name, "type": kind} for name, kind in [
            ("Date", "date"), ("Done", "checkbox"), ("Recent 12 Weeks", "checkbox"),
            ("Exercise", "select"), ("Session Key", "rich_text"), ("Valid Exercise", "checkbox")]}
        spec = next(item for item in SPECS if item[0] == "C07")
        body = payload(spec, schema, {}, native=True)
        self.assertEqual(body["configuration"]["y_axis"], {"aggregator": "unique", "property_id": "Session Key"})
        self.assertIn({"property": "Valid Exercise", "checkbox": {"equals": True}}, body["filter"]["and"])

    def test_formula_condition_is_filtered_as_string_not_select(self):
        schema = {"Condition": {"id": "condition-id", "type": "formula", "result_type": "text"}}
        body = {"filter": {"property": "Condition", "rich_text": {"equals": "historic-condition"}}}
        adapt_filters(body, schema)
        self.assertEqual(body["filter"], {"property": "condition-id", "formula": {"string": {"equals": "historic-condition"}}})

    def test_extra_lift_tab_keeps_same_exercise_and_eligibility_filters(self):
        names = ['Date', 'Done', 'Recent 12 Weeks', 'Original Exercise', 'Condition', 'Comparable', 'Pullup', 'Reps', 'e1RM', 'e1RM Display', 'Load']
        schema = {name: {'id': name, 'type': 'number' if name in {'Reps', 'e1RM', 'e1RM Display', 'Load'} else 'date' if name == 'Date' else 'rich_text'} for name in names}
        config = {'strength_scope': {'exercise_id': 'bench-id', 'condition': 'confirmed'}}
        original = SPECS[0]
        extra = ('C01_Bench', *original[1:])
        body = payload(extra, schema, config, native=True)
        filters = body['filter']['and']
        self.assertIn({'property': 'Original Exercise', 'relation': {'contains': 'bench-id'}}, filters)
        self.assertIn({'property': 'e1RM', 'number': {'greater_than': 0}}, filters)
        self.assertIn({'property': 'Comparable', 'checkbox': {'equals': True}}, filters)
        self.assertTrue(body['name'].startswith('NFT C01_Bench'))

    def test_observation_is_explicit_and_does_not_enable_unverified_progress(self):
        names = ['Date', 'Done', 'Recent 12 Weeks', 'Original Exercise', 'Condition', 'Comparable', 'Pullup', 'Reps', 'e1RM', 'e1RM Display', 'Load', 'Index Display', 'Exercise', 'Metric']
        schema = {name: {'id': name, 'type': 'date' if name == 'Date' else 'select'} for name in names}
        config = {'strength_scope': {'exercise_id': 'deadlift-id', 'condition': 'unknown', 'observational': True}}
        observed = payload(SPECS[0], schema, config, native=True)
        self.assertIn('조건 미확인 관측', observed['name'])
        self.assertIn('동일 조건 발전으로 해석 금지', observed['configuration']['caption'])
        comparison = payload(next(s for s in SPECS if s[0] == 'C10'), schema, config, native=True)
        self.assertIn({'property': 'Metric', 'select': {'equals': 'e1rm'}}, comparison['filter']['and'])

    def test_top_ten_uses_original_relations_with_selection_timestamp(self):
        names = ['Date', 'Done', 'Recent 12 Weeks', 'Original Exercise', 'Exercise']
        schema = {name: {'id': name, 'type': 'date' if name == 'Date' else 'select'} for name in names}
        config = {'top10_exercise_ids': ['exercise-a', 'exercise-b'], 'top10_updated_at': '2026-10-03'}
        body = payload(next(s for s in SPECS if s[0] == 'C06T'), schema, config, native=True)
        self.assertEqual(len(body['filter']['and'][-1]['or']), 2)
        self.assertIn('상위 종목 선정: 2026-10-03', body['configuration']['caption'])


if __name__ == "__main__":
    unittest.main()
