import copy
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dashboard.reader import ReaderError
from dashboard.snapshots import NoSnapshotError, SnapshotConflictError, SnapshotManager, UnknownJobError


def source_snapshot():
    return {"sessions": [{"id": "session"}], "sets": [{"id": "set"}], "exercises": [{"id": "exercise"}],
            "fetched_at": "2026-10-04T01:00:00Z", "started_at": "2026-10-04T00:59:00Z",
            "source_max_last_edited_at": "2026-10-04T00:00:00Z", "source_fingerprint": {"sets": "fingerprint"},
            "optional_sources": {}}


class FakeReader:
    def __init__(self, snapshot=None, error=None, event=None):
        self.snapshot = snapshot or source_snapshot()
        self.error, self.event = error, event
        self.calls = 0

    def read(self, progress=None, deadline=None):
        self.calls += 1
        if progress:
            progress({"stage": "source", "source": "sets"})
        if self.event:
            self.event.wait(1)
        if self.error:
            raise self.error
        return copy.deepcopy(self.snapshot)


def completed(manager, job_id, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = manager.job(job_id)
        if job["state"] in {"succeeded", "failed"}:
            return job
        time.sleep(0.005)
    raise AssertionError("job did not finish")


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.cache = Path(self.directory.name) / ".local" / "dashboard-cache.json"
        self.managers = []

    def tearDown(self):
        for manager in self.managers:
            manager.close()
        self.directory.cleanup()

    def manager(self, reader, timeout=120):
        manager = SnapshotManager(lambda: reader, self.cache, timeout=timeout)
        self.managers.append(manager)
        return manager

    def test_first_failure_has_no_snapshot_and_no_fabricated_zero(self):
        manager = self.manager(FakeReader(error=ReaderError("접근 권한을 확인하세요.")))
        job = completed(manager, manager.refresh()["id"])
        self.assertEqual(job["state"], "failed")
        self.assertIsNone(manager.status()["snapshot_id"])
        self.assertEqual(manager.status()["source_counts"], {})
        with self.assertRaises(NoSnapshotError):
            manager.get()
        self.assertFalse(self.cache.exists())

    def test_success_private_atomic_cache_and_restart_are_marked_previous(self):
        reader = FakeReader()
        manager = self.manager(reader)
        job = completed(manager, manager.refresh()["id"])
        self.assertEqual(job["state"], "succeeded")
        snapshot = manager.get(job["snapshot_id"])
        self.assertEqual(json.loads(self.cache.read_text()), snapshot)
        self.assertEqual(os.stat(self.cache).st_mode & 0o777, 0o600)
        self.assertEqual(list(self.cache.parent.glob("*.tmp")), [])
        restarted = self.manager(FakeReader())
        self.assertEqual(restarted.get()["id"], snapshot["id"])
        self.assertEqual(restarted.status()["refresh_state"], "cached")
        self.assertTrue(restarted.status()["using_previous_data"])
        self.assertEqual(reader.calls, 1)

    def test_refresh_deduplicates_concurrent_calls_and_preserves_prior_data(self):
        reader = FakeReader()
        manager = self.manager(reader)
        completed(manager, manager.refresh()["id"])
        old = manager.get()
        event = threading.Event()
        reader.event = event
        first = manager.refresh()
        second = manager.refresh()
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(manager.get(), old)
        self.assertTrue(manager.status()["using_previous_data"])
        event.set()
        completed(manager, first["id"])
        self.assertEqual(reader.calls, 2)

    def test_failed_refresh_preserves_success_value_and_timestamp_on_disk(self):
        reader = FakeReader()
        manager = self.manager(reader)
        completed(manager, manager.refresh()["id"])
        old = manager.get()
        old_disk = self.cache.read_bytes()
        reader.error = ReaderError("통신 실패")
        job = completed(manager, manager.refresh()["id"])
        self.assertEqual(job["state"], "failed")
        self.assertEqual(manager.get(), old)
        self.assertEqual(self.cache.read_bytes(), old_disk)
        self.assertEqual(manager.status()["fetched_at"], old["fetched_at"])

    def test_cache_version_or_duplicates_are_rejected_without_loading_data(self):
        self.cache.parent.mkdir()
        for content in [{**source_snapshot(), "schema_version": 99},
                        {**source_snapshot(), "schema_version": 1, "id": "id", "sets": [{"id": "a"}, {"id": "a"}]}]:
            self.cache.write_text(json.dumps(content))
            manager = self.manager(FakeReader())
            with self.assertRaises(NoSnapshotError):
                manager.get()
            self.assertEqual(manager.status()["refresh_state"], "failed")

    def test_superseded_id_is_a_conflict_and_returned_records_are_copies(self):
        manager = self.manager(FakeReader())
        completed(manager, manager.refresh()["id"])
        first = manager.get()
        first["sets"].clear()
        self.assertEqual(len(manager.get()["sets"]), 1)
        completed(manager, manager.refresh()["id"])
        with self.assertRaises(SnapshotConflictError) as caught:
            manager.get(first["id"])
        self.assertEqual(caught.exception.current_id, manager.get()["id"])
        with self.assertRaises(UnknownJobError):
            manager.job("missing")

    def test_timeout_fails_promptly_and_late_result_cannot_publish(self):
        event = threading.Event()
        manager = self.manager(FakeReader(event=event), timeout=0.04)
        job = completed(manager, manager.refresh()["id"])
        self.assertEqual(job["state"], "failed")
        event.set()
        time.sleep(0.02)
        self.assertFalse(self.cache.exists())
        with self.assertRaises(NoSnapshotError):
            manager.get()

    def test_atomic_save_failure_keeps_memory_and_file_last_good(self):
        manager = self.manager(FakeReader())
        completed(manager, manager.refresh()["id"])
        old = manager.get()
        old_disk = self.cache.read_bytes()
        with patch("dashboard.snapshots.os.replace", side_effect=OSError("private secret should not leak")):
            job = completed(manager, manager.refresh()["id"])
        self.assertEqual(manager.get(), old)
        self.assertEqual(self.cache.read_bytes(), old_disk)
        self.assertNotIn("private secret", job["error"])
        self.assertEqual(list(self.cache.parent.glob("*.tmp")), [])

    def test_optional_failure_retains_independent_previous_records_and_timestamp(self):
        snapshot = source_snapshot()
        snapshot["optional_sources"] = {"health": {"state": "succeeded", "fetched_at": "old-health", "records": [{"id": "health-row"}]}}
        reader = FakeReader(snapshot)
        manager = self.manager(reader)
        completed(manager, manager.refresh()["id"])
        reader.snapshot["optional_sources"] = {"health": {"state": "failed", "error": "source unavailable", "records": []}}
        completed(manager, manager.refresh()["id"])
        result = manager.get()["optional_sources"]["health"]
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["fetched_at"], "old-health")
        self.assertEqual(result["records"], [{"id": "health-row"}])
        self.assertTrue(result["using_previous_data"])
        self.assertNotIn("records", manager.status()["optional_sources"]["health"])

    def test_empty_health_success_still_retains_its_timestamp_after_failure(self):
        snapshot = source_snapshot()
        snapshot["optional_sources"] = {"health": {"state": "succeeded", "fetched_at": "empty-health-success", "records": []}}
        reader = FakeReader(snapshot)
        manager = self.manager(reader)
        completed(manager, manager.refresh()["id"])
        reader.snapshot["optional_sources"] = {"health": {"state": "failed", "error": "source unavailable", "records": []}}
        completed(manager, manager.refresh()["id"])
        result = manager.get()["optional_sources"]["health"]
        self.assertEqual(result["fetched_at"], "empty-health-success")
        self.assertTrue(result["using_previous_data"])


if __name__ == "__main__":
    unittest.main()
