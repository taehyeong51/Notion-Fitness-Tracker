"""Independent examples for dashboard counting, evidence and comparability."""
import copy
import csv
from datetime import date
import io
import json
import math
import unittest

from dashboard.analytics import analyze, catalog, export_csv, session_detail


TODAY = date(2026, 10, 4)  # Sunday in Seoul.


def session(identifier, observed="2026-10-02", split="Pull", done=True):
    return {"id": identifier, "date": observed, "split": split, "done": done,
            "url": f"https://www.notion.so/{identifier}"}


def exercise(identifier="dead", label="데드 리프트", muscle="등"):
    return {"id": identifier, "label": label, "muscle": muscle}


def set_row(identifier, sid="s1", eid="dead", load=140, reps=4, **values):
    return {"id": identifier, "session_ids": [sid], "exercise_ids": [eid],
            "load": load, "reps": reps, "done": True, "set_type": "Working",
            "load_unit": "kg", "external_load_verified": True, **values}


def snapshot(sessions=None, sets=None, exercises=None):
    return {"id": "snap-1", "fetched_at": "2026-10-04T04:00:00Z",
            "sessions": sessions if sessions is not None else [session("s1")],
            "exercises": exercises if exercises is not None else [exercise()],
            "sets": sets if sets is not None else [set_row("a")]}


def result(data, **request):
    return analyze(data, {"core_exercise_ids": ["dead"], **request}, today=TODAY)


def points(chart):
    return [point for series in chart["series"] for point in series["points"]]


class AnalysisCountingTests(unittest.TestCase):
    def test_recent_page_size_preserves_complete_analysis_and_csv(self):
        data = snapshot(
            sessions=[session(f"s{i}", f"2026-09-{i+1:02d}") for i in range(5)],
            sets=[set_row(f"set{i}", f"s{i}") for i in range(5)],
        )
        first = result(data)
        more = result(data, recent_limit=13)
        self.assertEqual(first["recent_total"], 5)
        self.assertEqual([s["id"] for s in first["recent_sessions"]], ["s4", "s3", "s2"])
        self.assertEqual(len(more["recent_sessions"]), 5)
        self.assertEqual(first["summary"], more["summary"])
        self.assertEqual(first["charts"], more["charts"])
        exported = export_csv(data, {"recent_limit": 3}, today=TODAY)
        self.assertEqual(len(list(csv.DictReader(io.StringIO(exported.lstrip("\ufeff"))))), 5)

    def test_five_sets_are_one_session_and_one_exercise_frequency(self):
        answer = result(snapshot(sets=[set_row(str(i)) for i in range(5)]))
        self.assertEqual(answer["summary"]["sessions"], 1)
        self.assertEqual(answer["summary"]["sets"], 5)
        self.assertEqual(points(answer["charts"]["C07"]["modes"]["all"])[0]["value"], 1)
        for identifier in ("C05", "C06"):
            chart = answer["charts"][identifier]
            if identifier == "C06":
                chart = chart["modes"]["all"]
            self.assertEqual(sum(point["value"] for point in points(chart)), 5)

    def test_seoul_monday_boundary_and_future_are_independent_of_filter(self):
        data = snapshot(sessions=[session("sun", "2026-09-27T14:59:59Z"),
                                 session("mon", "2026-09-27T15:00:00Z"),
                                 session("future", "2026-10-05")],
                        sets=[set_row("a", "sun"), set_row("b", "mon"), set_row("c", "future")])
        answer = result(data, filters={"set_types": ["Warm-up"]})
        self.assertEqual(answer["summary"]["current_week_start"], "2026-09-28")
        self.assertEqual(answer["summary"]["current_week_end"], "2026-10-04")
        self.assertEqual(answer["summary"]["sessions"], 1)
        self.assertEqual(answer["summary"]["sets"], 1)
        self.assertEqual(answer["summary"]["range_sets"], 0)
        self.assertEqual(answer["diagnostics"]["source"]["future_session_date"]["count"], 1)

    def test_84_days_may_have_13_partial_monday_buckets(self):
        answer = analyze(snapshot(sessions=[session("s1", "2026-10-02")]),
                         {"range": {"preset": "12w"}}, today="2026-10-02")
        chart = answer["charts"]["C04"]["modes"]["sessions"]
        bucket_points = points(chart)
        self.assertEqual(answer["meta"]["range_start"], "2026-07-11")
        self.assertEqual(len(bucket_points), 13)
        self.assertEqual(bucket_points[0]["date"], "2026-07-06")
        self.assertEqual(bucket_points[0]["detail"]["range_start"], "2026-07-11")
        self.assertTrue(bucket_points[0]["detail"]["partial_week"])
        self.assertTrue(bucket_points[-1]["detail"]["partial_week"])
        self.assertEqual(sum(point["value"] for point in bucket_points), 1)

    def test_unlinked_exercise_counts_but_invalid_session_relations_do_not(self):
        data = snapshot(sets=[set_row("linked"), set_row("unlinked", exercise_ids=[]),
                              set_row("multi", session_ids=["s1", "other"]),
                              set_row("missing", session_ids=[])])
        answer = result(data)
        self.assertEqual(answer["summary"]["sets"], 2)
        self.assertEqual(answer["diagnostics"]["unlinked_sets"], 1)
        self.assertEqual(answer["diagnostics"]["excluded_undated_sets"], 2)
        self.assertEqual(sum(p["value"] for p in points(answer["charts"]["C05"])), 2)
        self.assertEqual(sum(p["value"] for p in points(answer["charts"]["C06"]["modes"]["all"])), 2)
        frequency = answer["charts"]["C07"]["modes"]["all"]
        self.assertEqual(frequency["exclusions"]["count"], 1)
        self.assertEqual(frequency["denominator"], 1)

    def test_type_labels_preserved_and_filtered_sessions_require_matching_set(self):
        data = snapshot(sessions=[session("s1"), session("empty"), session("s2", split="Push")],
                        sets=[set_row("a", set_type="Working"), set_row("b", set_type="Burnfit Custom"),
                              set_row("c", set_type=None), set_row("d", "s2", set_type="Warm-up")])
        full = result(data)
        self.assertEqual(full["summary"]["range_sessions"], 3)
        stack = full["charts"]["C04"]["modes"]["sets"]
        self.assertEqual({series["label"] for series in stack["series"]},
                         {"Working", "Burnfit Custom", "미분류", "Warm-up"})
        self.assertEqual(sum(p["value"] for p in points(stack)), 4)
        filtered = result(data, filters={"set_types": ["Burnfit Custom"]})
        self.assertEqual(filtered["summary"]["range_sessions"], 1)
        self.assertEqual(filtered["summary"]["range_sets"], 1)
        self.assertEqual(filtered["summary"]["sets"], 4)

    def test_invalid_dates_timestamp_precision_and_completion(self):
        data = snapshot(sessions=[session("naive", "2026-10-02T12:00:00"),
                                 session("period", {"start": "2026-10-01", "end": "2026-10-02"}),
                                 session("missing", None), session("done", done=False), session("s1")],
                        sets=[set_row("a", "naive"), set_row("b", "period"), set_row("c", "missing"),
                              set_row("d", "done"), set_row("e", done=False), set_row("f")])
        answer = result(data)
        self.assertEqual(answer["summary"]["sets"], 1)
        self.assertEqual(answer["summary"]["sessions"], 1)
        self.assertEqual(len(answer["diagnostics"]["invalid_sessions"]), 3)

    def test_custom_range_boundaries_and_latest_outside_range(self):
        data = snapshot(sessions=[session("before", "2026-09-30"), session("s1", "2026-10-01"),
                                 session("after", "2026-10-02")],
                        sets=[set_row("a", "before"), set_row("b"), set_row("c", "after")])
        answer = result(data, range={"preset": "custom", "start": "2026-10-01", "end": "2026-10-01"})
        self.assertEqual(answer["summary"]["range_sets"], 1)
        self.assertTrue(answer["summary"]["latest_outside_range"])
        for start, end in (("2026-10-03", "2026-10-02"), ("2026-10-01", "2026-10-05")):
            with self.assertRaises(ValueError):
                result(data, range={"preset": "custom", "start": start, "end": end})

    def test_duplicate_id_refuses_projection_and_no_mutation(self):
        data = snapshot()
        original = copy.deepcopy(data)
        answer = result(data)
        self.assertEqual(data, original)
        json.dumps(answer, allow_nan=False)
        data["sets"].append(copy.deepcopy(data["sets"][0]))
        with self.assertRaises(ValueError):
            result(data)


class PerformanceTests(unittest.TestCase):
    def test_pullup_fixed_load_reps_keep_added_assisted_and_unknown_modes_separate(self):
        data = snapshot(sets=[
            set_row("added", load=10, reps=8, pullup=True, pullup_mode="added"),
            set_row("assisted", load=10, reps=12, pullup=True, pullup_mode="assisted"),
            set_row("unknown", load=10, reps=20, pullup=True, pullup_mode="unknown"),
            set_row("other-load", load=15, reps=6, pullup=True, pullup_mode="added"),
        ])
        answer = result(data, detail_scope={"exercise_id": "dead", "fixed_load": 10})
        chart = answer["charts"]["C02"]
        self.assertEqual(chart["selected_load"], 10)
        values = {series["pullup_mode"]: series["points"][0]["value"] for series in chart["series"]}
        self.assertEqual(values, {"added": 8, "assisted": 12, "unknown": 20})
        self.assertEqual(answer["charts"]["C01"]["status"], "empty")
        self.assertIsNone(answer["core_exercises"][0]["verified_pr"])

    def test_e1rm_observed_retains_precision_without_condition_pr(self):
        answer = result(snapshot())
        core = answer["core_exercises"][0]
        self.assertAlmostEqual(core["current_set"]["e1rm_observed"], 158.66666666666666)
        self.assertEqual(core["current_set"]["e1rm_display"], 158.7)
        self.assertIsNone(core["verified_pr"])
        self.assertTrue(core["trend"]["series"][0]["observation_only"])

    def test_invalid_e1rm_inputs_count_as_sets_without_fake_points(self):
        invalid = [set_row("r13", reps=13), set_row("fraction", reps=4.5),
                   set_row("null", reps=None), set_row("zero", load=0),
                   set_row("negative", load=-1), set_row("nan", load=float("nan")),
                   set_row("unknown-unit", load_unit=None), set_row("bool", reps=True),
                   set_row("pullup", pullup=True, pullup_mode="unknown")]
        answer = result(snapshot(sets=invalid))
        self.assertEqual(answer["summary"]["sets"], 9)
        self.assertEqual(answer["charts"]["C01"]["status"], "empty")
        self.assertEqual(answer["charts"]["C01"]["exclusions"]["count"], 9)
        json.dumps(answer, allow_nan=False)

    def test_known_and_unknown_conditions_have_separate_daily_maxima(self):
        data = snapshot(sets=[set_row("a", load=100, condition="barbell"),
                              set_row("b", load=110, condition="barbell"),
                              set_row("c", load=150), set_row("d", load=130, condition="machine")])
        answer = result(data)
        chart = answer["charts"]["C01"]
        self.assertEqual(len(chart["series"]), 3)
        observed = next(s for s in chart["series"] if s["observation_only"])
        self.assertAlmostEqual(observed["points"][0]["value"], 170)
        known = next(s for s in chart["series"] if s["condition_key"] == "barbell")
        self.assertAlmostEqual(known["points"][0]["value"], 124.66666666666666)
        self.assertEqual(known["points"][0]["sample_count"], 2)

    def test_previous_actual_and_verified_gain_only_same_condition(self):
        data = snapshot(sessions=[session("old", "2026-09-01"), session("s1")],
                        sets=[set_row("old-set", "old", load=130, condition="barbell"),
                              set_row("new-set", condition="barbell")])
        answer = result(data)
        core = answer["core_exercises"][0]
        self.assertEqual(core["previous_set"]["load"], 130)
        self.assertEqual(core["comparison"]["status"], "ok")
        self.assertAlmostEqual(core["comparison"]["percent"], (140 / 130 - 1) * 100)
        self.assertTrue(core["verified_pr"]["is_current_best"])
        data["sets"][1]["condition"] = "machine"
        changed = result(data)["core_exercises"][0]
        self.assertEqual(changed["comparison"]["status"], "unavailable")

    def test_same_day_without_times_does_not_claim_preceding_performance(self):
        data = snapshot(sessions=[session("s1"), session("s2")],
                        sets=[set_row("a", "s1", condition="bar"), set_row("b", "s2", condition="bar")])
        core = result(data)["core_exercises"][0]
        self.assertTrue(core["sequence_ambiguous"])
        self.assertEqual([tie["session_id"] for tie in core["latest_ties"]], ["s1", "s2"])
        self.assertEqual([tie["sets"][0]["id"] for tie in core["latest_ties"]], ["a", "b"])
        self.assertIsNone(core["previous_set"])
        self.assertEqual(core["comparison"]["status"], "unavailable")
        self.assertIsNone(core["verified_pr"])
        data["sessions"][0]["date"] = "2026-10-02T09:00:00+09:00"
        data["sessions"][1]["date"] = "2026-10-02T10:00:00+09:00"
        timed = result(data)["core_exercises"][0]
        self.assertFalse(timed["sequence_ambiguous"])
        self.assertEqual(timed["previous_set"]["id"], "a")

    def test_fixed_load_available_values_and_missing_rep_bands(self):
        answer = result(snapshot(sets=[set_row("a", load=100, reps=4), set_row("b", load=100, reps=8),
                                      set_row("c", load=120, reps=5)]), detail_scope={"exercise_id": "dead", "fixed_load": 100})
        chart = answer["charts"]["C02"]
        self.assertEqual(chart["available_loads"], [100, 120])
        self.assertEqual(points(chart)[0]["value"], 8)
        bands = answer["charts"]["C03"]
        absent = next(s for s in bands["series"] if s["rep_band"] == "9–12회")
        self.assertTrue(all(point["value"] is None for point in absent["points"]))
        self.assertFalse(any(point["value"] == 0 for point in points(bands)))

    def test_volume_requires_verified_external_load_without_bodyweight_guess(self):
        data = snapshot(sets=[set_row("valid", load=100, reps=5),
                              set_row("unconfirmed", external_load_verified=False),
                              set_row("pullup", load=20, reps=5, pullup=True, pullup_mode="added")])
        volume = result(data)["charts"]["C04"]["modes"]["volume"]
        self.assertEqual(sum(p["value"] for p in points(volume)), 500)
        self.assertEqual(volume["exclusions"]["count"], 2)
        unavailable = result(snapshot(sets=[set_row("a", external_load_verified=False)]))["charts"]["C04"]["modes"]["volume"]
        self.assertEqual(unavailable["status"], "unavailable")
        self.assertTrue(all(p["value"] is None for p in points(unavailable)))

    def test_pullup_modes_and_loads_do_not_merge_or_receive_e1rm(self):
        data = snapshot(exercises=[exercise("pull", "풀업")], sets=[
            set_row("body", eid="pull", load=0, reps=10, pullup=True, pullup_mode="bodyweight", condition="grip"),
            set_row("add10", eid="pull", load=10, reps=8, pullup=True, pullup_mode="added", condition="grip"),
            set_row("add20", eid="pull", load=20, reps=4, pullup=True, pullup_mode="added", condition="grip"),
            set_row("assist", eid="pull", load=20, reps=15, pullup=True, pullup_mode="assisted", condition="grip"),
            set_row("unknown", eid="pull", load=0, reps=6, pullup=True)])
        answer = result(data, core_exercise_ids=["pull"], detail_scope={"exercise_id": "pull"})
        self.assertEqual(answer["charts"]["C01"]["status"], "empty")
        self.assertEqual(len(answer["charts"]["C08"]["series"]), 5)
        self.assertIsNone(answer["core_exercises"][0]["current_set"])
        scoped = result(data, core_exercise_ids=["pull"], detail_scope={"exercise_id": "pull", "pullup_mode": "bodyweight"})
        self.assertEqual(scoped["core_exercises"][0]["current_set"]["reps"], 10)
        self.assertIsNone(scoped["core_exercises"][0]["current_set"]["e1rm_observed"])

    def test_unknown_pullup_mode_never_verified_even_with_condition(self):
        data = snapshot(sessions=[session("old", "2026-09-01"), session("s1")],
                        exercises=[exercise("pull", "풀업")],
                        sets=[set_row("a", "old", "pull", load=0, pullup=True, reps=5, condition="grip"),
                              set_row("b", "s1", "pull", load=0, pullup=True, reps=7, condition="grip")])
        core = result(data, core_exercise_ids=["pull"])["core_exercises"][0]
        self.assertIsNone(core["verified_pr"])
        self.assertEqual(core["comparison"]["status"], "unavailable")


class BaselineAndRankingTests(unittest.TestCase):
    def test_fixed_baseline_is_100_and_does_not_move_with_range(self):
        data = snapshot(sessions=[session("old", "2026-09-01"), session("s1")],
                        sets=[set_row("base", "old", load=100, condition="bar"),
                              set_row("latest", load=140, condition="bar")])
        baseline = {"exercise_id": "dead", "condition_key": "bar", "metric": "e1rm",
                    "source_set_id": "base", "value": 100 * (1 + 4 / 30), "version": "v1"}
        baseline["source_fingerprint"] = session_detail(data, "old")["sets"][0]["baseline_fingerprint"]
        full = result(data, baselines=[baseline])["charts"]["C10"]
        self.assertEqual(points(full)[0]["value"], 100)
        self.assertAlmostEqual(points(full)[1]["value"], 140)
        narrow = result(data, baselines=[baseline], range={"preset": "4w"})["charts"]["C10"]
        self.assertEqual(len(points(narrow)), 1)
        self.assertAlmostEqual(points(narrow)[0]["value"], 140)
        self.assertEqual(points(narrow)[0]["detail"]["baseline_set_id"], "base")

    def test_changed_deleted_nonmaximum_or_unversioned_baseline_invalidates(self):
        data = snapshot(sets=[set_row("base", load=100, condition="bar"), set_row("higher", load=120, condition="bar")])
        baseline = {"exercise_id": "dead", "condition_key": "bar", "metric": "e1rm",
                    "source_set_id": "base", "value": 100 * (1 + 4 / 30), "version": "v1"}
        baseline["source_fingerprint"] = next(row for row in session_detail(data, "s1")["sets"] if row["id"] == "base")["baseline_fingerprint"]
        chart = result(data, baselines=[baseline])["charts"]["C10"]
        self.assertEqual(chart["status"], "unavailable")
        self.assertEqual(chart["invalid_baselines"][0]["reason"], "reference_not_daily_maximum")
        data["sets"] = data["sets"][:1]
        for change in ({"value": 1}, {"source_set_id": "deleted"}, {"version": None}, {"condition_key": "changed"}):
            chart = result(data, baselines=[{**baseline, **change}])["charts"]["C10"]
            self.assertEqual(chart["status"], "unavailable")
            self.assertFalse(chart["series"])

    def test_baseline_fingerprint_freezes_date_type_and_source_edit_version(self):
        original = snapshot(sessions=[session("old", "2026-09-01"), session("s1")],
                            sets=[set_row("base", "old", load=100, condition="bar", last_edited_time="2026-09-01T01:00:00Z"),
                                  set_row("latest", load=140, condition="bar")])
        original["sessions"][0]["last_edited_time"] = "2026-09-01T01:00:00Z"
        reference = session_detail(original, "old")["sets"][0]
        baseline = {"exercise_id": "dead", "condition_key": "bar", "metric": "e1rm",
                    "source_set_id": "base", "value": reference["e1rm_observed"], "version": "v1",
                    "source_fingerprint": reference["baseline_fingerprint"]}
        self.assertEqual(result(original, baselines=[baseline])["charts"]["C10"]["status"], "ok")
        mutations = [("session", "date", "2026-09-02"),
                     ("session", "date", "2026-09-01T12:00:00+09:00"),
                     ("session", "last_edited_time", "2026-09-01T02:00:00Z"),
                     ("set", "set_type", "Top Set"),
                     ("set", "raw_set_type", "new original label"),
                     ("set", "last_edited_time", "2026-09-01T02:00:00Z")]
        for kind, field, value in mutations:
            with self.subTest(kind=kind, field=field):
                changed = copy.deepcopy(original)
                changed["sessions" if kind == "session" else "sets"][0][field] = value
                # The measured result itself remains equal; its source has changed.
                self.assertEqual(session_detail(changed, "old")["sets"][0]["e1rm_observed"], baseline["value"])
                chart = result(changed, baselines=[baseline])["charts"]["C10"]
                self.assertEqual(chart["status"], "unavailable")
                self.assertEqual(chart["invalid_baselines"][0]["reason"], "missing_or_changed_source_fingerprint")
        unfrozen = {key: value for key, value in baseline.items() if key != "source_fingerprint"}
        chart = result(original, baselines=[unfrozen])["charts"]["C10"]
        self.assertEqual(chart["status"], "unavailable")
        self.assertEqual(chart["invalid_baselines"][0]["reason"], "missing_or_changed_source_fingerprint")

    def test_top10_recomputed_with_stable_id_ties_and_other_unlinked(self):
        exercises = [exercise(f"e{i:02}", f"종목{i}") for i in range(12)]
        sets = [set_row(f"r{i}", eid=f"e{i:02}") for i in range(12)] + [set_row("unlinked", exercise_ids=[])]
        data = snapshot(exercises=exercises, sets=sets)
        chart = result(data)["charts"]["C06"]["modes"]["top10"]
        ids = [p["category_id"] for p in points(chart)]
        self.assertEqual(ids[:10], [f"e{i:02}" for i in range(10)])
        self.assertEqual(ids[-2:], ["__other__", "__unlinked__"])
        self.assertEqual(sum(p["value"] for p in points(chart)), 13)
        filtered = result(data, filters={"exercise_ids": ["e11"]})["charts"]["C06"]["modes"]["top10"]
        self.assertEqual(points(filtered)[0]["category_id"], "e11")
        self.assertEqual(len(points(filtered)), 1)

    def test_split_session_and_set_modes_have_distinct_denominators(self):
        data = snapshot(sessions=[session("s1", split="Pull"), session("s2", split=None)],
                        sets=[set_row("a"), set_row("b"), set_row("c", "s2")])
        modes = result(data)["charts"]["C09"]["modes"]
        self.assertEqual(modes["sessions"]["denominator"], 2)
        self.assertEqual(modes["sets"]["denominator"], 3)
        self.assertEqual(sum(p["value"] for p in points(modes["sessions"])), 2)
        self.assertEqual(sum(p["value"] for p in points(modes["sets"])), 3)

    def test_catalog_uses_ids_and_exact_names_without_variant_guess(self):
        data = snapshot(exercises=[exercise("dead1", "데드 리프트"), exercise("dead2", "데드리프트"),
                                   exercise("bench", "벤치 프레스"), exercise("variant", "벤치 프레스 머신"),
                                   exercise("pull", "풀업"), exercise("squat", "스쿼트")])
        values = catalog(data)
        self.assertEqual(values["default_core_exercise_ids"], ["bench", "squat", "pull"])
        self.assertEqual(values["ambiguous_default_exercises"][0]["exercise_ids"], ["dead1", "dead2"])
        self.assertNotIn("variant", values["default_core_exercise_ids"])

    def test_verified_english_library_names_default_to_four_without_variants(self):
        data = snapshot(exercises=[exercise("bench", "Barbell Bench Press"),
                                   exercise("squat", "Barbell Back Squat"),
                                   exercise("dead", "Conventional Deadlift"),
                                   exercise("pull", "Pull-up"),
                                   exercise("incline", "Incline Barbell Bench Press"),
                                   exercise("smith", "Smith Squat"),
                                   exercise("romanian", "Romanian Deadlift"),
                                   exercise("chin", "Chin-up")])
        values = catalog(data)
        self.assertEqual(values["default_core_exercise_ids"], ["bench", "squat", "dead", "pull"])
        self.assertFalse(values["ambiguous_default_exercises"])
        data["exercises"].append(exercise("second-bench", "바벨 벤치 프레스"))
        ambiguous = catalog(data)
        self.assertNotIn("bench", ambiguous["default_core_exercise_ids"])
        self.assertNotIn("second-bench", ambiguous["default_core_exercise_ids"])
        self.assertEqual(ambiguous["ambiguous_default_exercises"][0]["exercise_ids"], ["bench", "second-bench"])


class ExportAndDetailTests(unittest.TestCase):
    def test_csv_bom_precision_filtering_and_spreadsheet_formula_protection(self):
        data = snapshot(exercises=[exercise(label="  =HYPERLINK(\"x\")\n한글")],
                        sets=[set_row("a", set_type="@custom"), set_row("b", set_type="Warm-up")])
        csv_text = export_csv(data, {"range": {"preset": "all"}, "filters": {"set_types": ["@custom"]}}, today=TODAY)
        self.assertTrue(csv_text.startswith("\ufeff"))
        rows = list(csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff"))))
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["exercise"].startswith("'"))
        self.assertEqual(rows[0]["set_type"], "'@custom")
        self.assertAlmostEqual(float(rows[0]["e1rm_observed"]), 158.66666666666666)
        self.assertEqual(rows[0]["range_start"], "2026-10-02")

    def test_session_details_have_source_evidence_and_missing_id_raises(self):
        data = snapshot(sets=[set_row("a", url="https://www.notion.so/a")])
        detail = session_detail(data, "s1")
        self.assertEqual(detail["sets"][0]["id"], "a")
        self.assertEqual(detail["sets"][0]["url"], "https://www.notion.so/a")
        with self.assertRaises(KeyError):
            session_detail(data, "missing")


if __name__ == "__main__":
    unittest.main()
