from __future__ import annotations

from copy import deepcopy
import csv
from datetime import datetime
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from dashboard.server import create_app
from dashboard.snapshots import NoSnapshotError, SnapshotConflictError, UnknownJobError


def snapshot():
    today = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    return {
        "id": "snapshot-1", "schema_version": 1,
        "fetched_at": today + "T10:00:00+09:00",
        "sessions": [{"id": "session-1", "date": today, "split": "Push", "done": True}],
        "exercises": [{"id": "bench", "label": "벤치 프레스", "muscle": "가슴"}],
        "sets": [{"id": f"set-{i}", "session_ids": ["session-1"], "exercise_ids": ["bench"],
                  "load": 100, "reps": 5, "done": True, "set_type": None,
                  "load_unit": "kg", "condition": None, "pullup": False}
                 for i in range(5)],
    }


class MemoryManager:
    def __init__(self, value):
        self.value = value
        self.refresh_calls = 0
        self.closed = False

    def status(self):
        return {"snapshot_id": self.value["id"] if self.value else None,
                "fetched_at": self.value["fetched_at"] if self.value else None,
                "refresh_state": "idle", "using_previous_data": False}

    def get(self, snapshot_id=None):
        if self.value is None:
            raise NoSnapshotError("none")
        if snapshot_id and snapshot_id != self.value["id"]:
            raise SnapshotConflictError(self.value["id"])
        return deepcopy(self.value)

    def refresh(self):
        self.refresh_calls += 1
        return {"id": "job-1", "state": "running"}

    def job(self, identifier):
        if identifier != "job-1":
            raise UnknownJobError(identifier)
        return {"id": "job-1", "state": "succeeded", "snapshot_id": "snapshot-1"}

    def close(self):
        self.closed = True


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.manager = MemoryManager(snapshot())
        app = create_app(manager=self.manager, static_dir=self.directory.name)
        self.client = TestClient(app, base_url="http://127.0.0.1:8000")

    def tearDown(self):
        self.client.close()
        self.directory.cleanup()

    def request(self):
        return {"snapshot_id": "snapshot-1", "range": {"preset": "12w"},
                "core_exercise_ids": ["bench"]}

    def test_health_does_not_start_notion_query(self):
        self.assertEqual(self.client.get("/healthz").json(), {"status": "ok", "read_only": True})
        self.assertEqual(self.manager.refresh_calls, 0)

    def test_complete_analysis_through_public_contract(self):
        response = self.client.post("/api/analysis", json=self.request())
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["summary"]["sessions"], 1)
        self.assertEqual(result["summary"]["sets"], 5)
        self.assertEqual(result["core_exercises"][0]["current_set"]["load"], 100)
        self.assertIsNone(result["core_exercises"][0]["verified_pr"])
        self.assertEqual(set(result["charts"]), {f"C{i:02}" for i in range(1, 11)})
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.manager.refresh_calls, 0)

    def test_old_snapshot_rejected_and_latest_id_given(self):
        response = self.client.post("/api/analysis", json={**self.request(), "snapshot_id": "old"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["snapshot_id"], "snapshot-1")

    def test_refresh_during_calculation_cannot_mark_old_response_fresh(self):
        from dashboard.analytics import analyze
        def concurrent_refresh(value, payload):
            self.manager.value["id"] = "snapshot-2"
            return analyze(value, payload)
        with patch("dashboard.server.analytics.analyze", side_effect=concurrent_refresh):
            response = self.client.post("/api/analysis", json=self.request())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["snapshot_id"], "snapshot-2")

    def test_no_data_is_503_instead_of_zero_metrics(self):
        self.manager.value = None
        self.assertEqual(self.client.get("/api/catalog").status_code, 503)
        self.assertEqual(self.client.post("/api/analysis", json=self.request()).status_code, 503)

    def test_refresh_is_async_job_and_unknown_job_is_404(self):
        response = self.client.post("/api/refresh")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["id"], "job-1")
        self.assertEqual(self.client.get("/api/refresh/job-1").status_code, 200)
        self.assertEqual(self.client.get("/api/refresh/missing").status_code, 404)

    def test_origin_and_host_restrictions(self):
        self.assertEqual(self.client.post("/api/refresh", headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.client.get("/api/status", headers={"Host": "127.0.0.1.evil.example"}).status_code, 400)
        self.assertEqual(self.client.post("/api/refresh", headers={"Origin": "http://127.0.0.1:8000"}).status_code, 202)
        self.assertEqual(self.client.post("/api/refresh", headers={"Origin": "http://127.0.0.1:8001"}).status_code, 403)

    def test_inputs_do_not_accept_token_or_unsupported_mutations(self):
        response = self.client.post("/api/analysis", json={**self.request(), "token": "unused"})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("unused", response.text)
        self.assertEqual(self.client.patch("/api/sessions/session-1", json={"load": 1}).status_code, 404)
        oversized = self.client.post("/api/analysis", content="x" * 140_000,
                                     headers={"content-type": "application/json"})
        self.assertEqual(oversized.status_code, 413)

    def test_chunked_unknown_length_request_is_bounded(self):
        response = self.client.post(
            "/api/analysis", content=iter([b"x" * 70_000, b"x" * 70_000]),
            headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)

    def test_session_detail_and_unknown_api_are_not_spa_success(self):
        result = self.client.get("/api/sessions/session-1?snapshot_id=snapshot-1")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(result.json()["sets"]), 5)
        self.assertEqual(self.client.get("/api/sessions/missing?snapshot_id=snapshot-1").status_code, 404)
        self.assertEqual(self.client.get("/api/unknown").status_code, 404)

    def test_export_has_bom_and_protects_text_formulas(self):
        self.manager.value["exercises"][0]["label"] = "=SUM(1,1)"
        response = self.client.post("/api/export/csv", json=self.request())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"\xef\xbb\xbf"))
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertFalse(any(cell == "=SUM(1,1)" for row in rows for cell in row))
        self.assertTrue(any("SUM(1,1)" in cell for row in rows for cell in row))
        self.assertIn("attachment", response.headers["content-disposition"])

    def test_static_build_and_api_share_one_origin(self):
        Path(self.directory.name, "index.html").write_text("<html>Fitness Tracker</html>")
        with TestClient(create_app(manager=self.manager, static_dir=self.directory.name),
                        base_url="http://127.0.0.1:8000") as client:
            self.assertIn("Fitness Tracker", client.get("/").text)
            self.assertEqual(client.get("/api/status").status_code, 200)
        self.assertTrue(self.manager.closed)


if __name__ == "__main__":
    unittest.main()
