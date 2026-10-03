import copy
from datetime import date
import unittest

from notion.metrics import build, day, numeric, rep_band, week


def snapshot():
    return {
        "sessions": [
            {"id": "s1", "date": "2026-08-31", "split": "Pull", "done": True},
            {"id": "s2", "date": "2026-09-30", "split": "Pull", "done": True},
        ],
        "exercises": [{"id": "deadlift", "label": "데드리프트", "muscle": "등"}],
        "sets": [
            {"id": "a", "session_ids": ["s1"], "exercise_ids": ["deadlift"], "load": 140,
             "reps": 2, "set_type": "Working", "done": True, "condition": "바벨/양손/총중량"},
            {"id": "b", "session_ids": ["s2"], "exercise_ids": ["deadlift"], "load": 140,
             "reps": 4, "set_type": None, "done": True, "condition": "바벨/양손/총중량"},
            {"id": "c", "session_ids": ["s2"], "exercise_ids": ["deadlift"], "load": 140,
             "reps": 3, "set_type": "Warm-up", "done": True, "condition": "바벨/양손/총중량"},
        ],
    }


BASELINE = {"source_set_id": "a", "version": "v1", "value": 140 * (1 + 2 / 30)}
TODAY = date(2026, 10, 3)


class MetricsTests(unittest.TestCase):
    def test_deadlift_reference_is_158_7_without_changing_precision(self):
        result = build(snapshot(), now=TODAY)
        highest = max(row["e1rm"] for row in result["sets"])
        self.assertAlmostEqual(highest, 158.66666666666666)
        self.assertEqual(round(highest, 1), 158.7)

    def test_five_sets_or_two_conditions_still_count_as_one_session(self):
        data = snapshot()
        for ident, condition in [("d", "다른 그립"), ("e", "다른 그립")]:
            row = {**data["sets"][-1], "id": ident, "condition": condition}
            data["sets"].append(row)
        result = build(data, now=TODAY)
        self.assertEqual(len(result["sets"]), 5)
        self.assertEqual(len(result["membership"]), 2)
        self.assertEqual(len(result["membership"][1]["original_set"]), 4)

    def test_unclassified_preserved_not_counted_as_working(self):
        result = build(snapshot(), now=TODAY)
        self.assertEqual(result["diagnostics"]["set_types"], {"Working": 1, "미분류": 1, "Warm-up": 1})

    def test_weekly_summary_does_not_treat_unknown_sets_as_working(self):
        result = build(snapshot(), now=TODAY)
        self.assertEqual(sum(row["sessions_count"] for row in result["weekly"]), 2)
        self.assertEqual(sum(row["sets_count"] for row in result["weekly"]), 3)
        latest = result["weekly"][-1]
        self.assertEqual(latest["week"], "2026-09-28")
        self.assertEqual(latest["working_count"], 0)
        self.assertEqual(latest["unknown_count"], 1)
        self.assertEqual(latest["warmup_count"], 1)

    def test_missing_exercise_is_unknown_and_preserves_the_total(self):
        data = snapshot()
        data["sets"][1]["exercise_ids"] = []
        result = build(data, now=TODAY)
        self.assertEqual(len(result["sets"]), 3)
        self.assertEqual(result["sets"][1]["exercise"], "미상")
        self.assertIsNone(result["sets"][1]["e1rm"])
        self.assertEqual(result["diagnostics"]["unlinked_exercise"], 1)

    def test_multiple_sessions_are_not_silently_assigned(self):
        data = snapshot()
        data["sets"][1]["session_ids"] = ["s1", "s2"]
        result = build(data, now=TODAY)
        self.assertIsNone(result["sets"][1]["date"])
        self.assertFalse(result["sets"][1]["done"])

    def test_unknown_condition_excluded_from_strength_comparison(self):
        data = snapshot()
        data["sets"][1]["condition"] = None
        result = build(data, now=TODAY)
        self.assertIsNone(result["sets"][1]["e1rm"])
        self.assertIn("비교 조건 미확인", result["sets"][1]["issue"])
        self.assertEqual(len(result["membership"]), 2)

    def test_pullup_missing_load_is_valid_repetition_not_zero_strength(self):
        data = snapshot()
        data["sets"][1].update(pullup=True, load=None, reps=10, condition="맨몸/중립그립")
        result = build(data, now=TODAY)
        self.assertTrue(result["sets"][1]["comparable"])
        self.assertIsNone(result["sets"][1]["load"])
        self.assertIsNone(result["sets"][1]["e1rm"])

    def test_baseline_is_100_and_latest_is_106_25(self):
        result = build(snapshot(), [BASELINE], now=TODAY)
        self.assertEqual(result["progress"][0]["index"], 100)
        self.assertAlmostEqual(result["progress"][-1]["index"], 106.25)
        self.assertEqual(result["progress"][-1]["original_set"], ["b"])

    def test_deleting_winner_recalculates_next_observation(self):
        data = snapshot()
        before = build(data, [BASELINE], now=TODAY)
        data["sets"] = [row for row in data["sets"] if row["id"] != "b"]
        after = build(data, [BASELINE], now=TODAY)
        self.assertLess(after["progress"][-1]["index"], before["progress"][-1]["index"])
        self.assertEqual(after["progress"][-1]["original_set"], ["c"])
        self.assertEqual(after["progress"][-1]["key"], before["progress"][-1]["key"])

    def test_mutated_baseline_requires_explicit_new_version(self):
        data = snapshot()
        data["sets"][0]["reps"] = 3
        with self.assertRaisesRegex(ValueError, "Baseline source changed"):
            build(data, [BASELINE], now=TODAY)

    def test_baseline_must_be_the_daily_maximum(self):
        data = snapshot()
        data["sets"].append({**data["sets"][0], "id": "better", "reps": 3})
        with self.assertRaisesRegex(ValueError, "daily maximum"):
            build(data, [BASELINE], now=TODAY)

    def test_duplicate_source_ids_stop_before_sync(self):
        data = snapshot()
        data["sets"].append(copy.deepcopy(data["sets"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            build(data, now=TODAY)

    def test_duplicate_labels_do_not_merge_distinct_exercises(self):
        data = snapshot()
        data["exercises"].append({"id": "another", "label": "데드리프트", "muscle": "등"})
        data["sets"][1]["exercise_ids"] = ["another"]
        result = build(data, now=TODAY)
        self.assertNotEqual(result["sets"][0]["exercise"], result["sets"][1]["exercise"])

    def test_rep_boundaries_and_ineligible_values(self):
        values = [0, 1, 5, 6, 8, 9, 12, 13, None, 2.5]
        expected = [None, "1–5회", "1–5회", "6–8회", "6–8회", "9–12회", "9–12회", None, None, None]
        self.assertEqual([rep_band(item) for item in values], expected)

    def test_seoul_day_and_monday_boundary(self):
        observed = day("2026-10-04T16:00:00Z")
        self.assertEqual(observed, date(2026, 10, 5))
        self.assertEqual(week(observed), "2026-10-05")
        self.assertEqual(week(date(2026, 10, 4)), "2026-09-28")

    def test_non_finite_boolean_and_unit_strings_are_rejected(self):
        for raw in [float("nan"), float("inf"), True, "140kg"]:
            with self.assertRaises(ValueError):
                numeric(raw)

    def test_top_ten_uses_current_completed_records_and_excludes_unknown(self):
        data = snapshot()
        for i in range(11):
            ident = f"exercise{i:02}"
            data["exercises"].append({"id": ident, "label": ident, "muscle": "등"})
            data["sets"].append({**data["sets"][1], "id": ident, "exercise_ids": [ident]})
        result = build(data, now=TODAY)
        selected = {row["exercise"] for row in result["sets"] if row["top10"]}
        self.assertEqual(len(selected), 10)
        self.assertIn("데드리프트", selected)

    def test_uncompleted_sets_not_in_frequency_or_progress(self):
        data = snapshot()
        for row in data["sets"][1:]:
            row["done"] = False
        result = build(data, [BASELINE], now=TODAY)
        self.assertEqual(len(result["membership"]), 1)
        self.assertEqual(len(result["progress"]), 1)


if __name__ == "__main__":
    unittest.main()
