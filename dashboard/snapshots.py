"""Background refresh jobs and atomic, private last-good snapshot storage."""
from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .reader import ReadDeadlineError, ReaderError, utc_now


SCHEMA_VERSION = 1


class NoSnapshotError(RuntimeError):
    pass


class SnapshotConflictError(RuntimeError):
    def __init__(self, current_id):
        self.current_id = current_id
        super().__init__("스냅샷이 갱신되었습니다. 최신 분석을 다시 요청하세요.")


class UnknownJobError(KeyError):
    pass


def _validate_snapshot(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported snapshot version")
    for key in ["id", "fetched_at", "started_at"]:
        if not isinstance(snapshot.get(key), str) or not snapshot[key]:
            raise ValueError("missing snapshot metadata")
    for key in ["sessions", "sets", "exercises"]:
        rows = snapshot.get(key)
        if not isinstance(rows, list) or any(not isinstance(row, dict) or not row.get("id") for row in rows):
            raise ValueError("invalid snapshot records")
        if len({row["id"] for row in rows}) != len(rows):
            raise ValueError("duplicate snapshot records")
    json.dumps(snapshot, allow_nan=False)


def _atomic_save(path, snapshot):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".dashboard-cache-", suffix=".tmp", dir=path.parent)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        else:
            os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(snapshot, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


class SnapshotManager:
    def __init__(self, reader_factory, cache_path, timeout=120):
        if not 0 < timeout <= 120:
            raise ValueError("refresh timeout must be between 0 and 120 seconds")
        self.reader_factory = reader_factory
        self.cache_path = Path(cache_path)
        self.timeout = timeout
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="notion-read")
        self._jobs = {}
        self._active = None
        self._snapshot = None
        self._last_error = None
        self._refresh_state = "idle"
        self._closed = False
        self._load()

    def _load(self):
        if not self.cache_path.exists():
            return
        try:
            snapshot = json.loads(self.cache_path.read_text(encoding="utf-8"))
            _validate_snapshot(snapshot)
            self._snapshot = snapshot
            self._refresh_state = "cached"
        except (OSError, ValueError, TypeError):
            self._last_error = "저장된 캐시를 읽을 수 없습니다. Notion을 새로 조회하세요."
            self._refresh_state = "failed"

    def status(self):
        with self._lock:
            snapshot = self._snapshot or {}
            optional = {name: {key: value for key, value in result.items() if key != "records"}
                        | {"record_count": len(result.get("records", []))}
                        for name, result in snapshot.get("optional_sources", {}).items()}
            return copy.deepcopy({
                "snapshot_id": snapshot.get("id"), "fetched_at": snapshot.get("fetched_at"),
                "started_at": snapshot.get("started_at"), "source_max_last_edited_at": snapshot.get("source_max_last_edited_at"),
                "refresh_state": self._refresh_state, "current_job_id": self._active,
                "error": self._last_error, "using_previous_data": bool(snapshot) and self._refresh_state != "succeeded",
                "source_counts": snapshot.get("source_counts", {}), "optional_sources": optional,
                "schema_version": SCHEMA_VERSION,
            })

    def refresh(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("snapshot manager is closed")
            if self._active and self._jobs[self._active]["state"] in {"queued", "running"}:
                return copy.deepcopy(self._jobs[self._active])
            identifier = str(uuid.uuid4())
            job = {"id": identifier, "state": "queued", "progress": {"stage": "queued"},
                   "started_at": utc_now(), "finished_at": None, "snapshot_id": None, "error": None}
            self._jobs[identifier] = job
            self._active = identifier
            self._refresh_state = "queued"
            self._last_error = None
            # Jobs are bounded; a misbehaving transport cannot leave the UI in "refreshing" forever.
            deadline = time.monotonic() + self.timeout
            timer = threading.Timer(self.timeout, self._expire, args=(identifier,))
            timer.daemon = True
            timer.start()
            self._executor.submit(self._run, identifier, deadline, timer)
            self._prune()
            return copy.deepcopy(job)

    def _prune(self):
        completed = [identifier for identifier, job in self._jobs.items() if job["state"] in {"succeeded", "failed"}]
        for identifier in completed[:-30]:
            del self._jobs[identifier]

    def _expire(self, identifier):
        self._fail(identifier, "Notion 조회 제한 시간을 초과했습니다. 다시 시도하세요.")

    def _fail(self, identifier, message):
        with self._lock:
            job = self._jobs.get(identifier)
            if not job or job["state"] not in {"queued", "running"}:
                return
            job.update(state="failed", error=message, finished_at=utc_now())
            if self._active == identifier:
                self._active = None
                self._refresh_state = "failed"
                self._last_error = message

    def _run(self, identifier, deadline, timer):
        try:
            with self._lock:
                job = self._jobs.get(identifier)
                if not job or job["state"] != "queued":
                    return
                job["state"] = "running"
                self._refresh_state = "running"

            def progress(value):
                with self._lock:
                    job = self._jobs.get(identifier)
                    if job and job["state"] == "running":
                        job["progress"] = copy.deepcopy(value)

            raw = self.reader_factory().read(progress=progress, deadline=deadline)
            if time.monotonic() >= deadline:
                raise ReadDeadlineError("Notion 조회 제한 시간을 초과했습니다. 다시 시도하세요.")
            snapshot = copy.deepcopy(raw)
            snapshot.update(id=str(uuid.uuid4()), schema_version=SCHEMA_VERSION)
            snapshot.setdefault("started_at", self._jobs[identifier]["started_at"])
            snapshot.setdefault("fetched_at", utc_now())
            snapshot.setdefault("source_counts", {role: len(snapshot.get(role, [])) for role in ["sessions", "sets", "exercises"]})
            snapshot.setdefault("optional_sources", {})
            _validate_snapshot(snapshot)
            with self._lock:
                job = self._jobs.get(identifier)
                if not job or job["state"] != "running":
                    return
                # Independent optional sources keep their prior success while a health read fails.
                previous = (self._snapshot or {}).get("optional_sources", {})
                for source, result in snapshot["optional_sources"].items():
                    if result.get("state") == "failed" and previous.get(source, {}).get("fetched_at"):
                        prior = previous[source]
                        result.update(records=copy.deepcopy(prior.get("records", [])), fetched_at=prior.get("fetched_at"),
                                      using_previous_data=True, source_max_last_edited_at=prior.get("source_max_last_edited_at"))
                _atomic_save(self.cache_path, snapshot)
                self._snapshot = snapshot
                job.update(state="succeeded", snapshot_id=snapshot["id"], finished_at=utc_now(), progress={"stage": "complete"})
                self._active = None
                self._refresh_state = "succeeded"
                self._last_error = None
        except ReaderError as error:
            self._fail(identifier, str(error))
        except Exception:
            # No arbitrary exception text may expose credentials or source records.
            self._fail(identifier, "조회 결과를 저장할 수 없습니다. 연결과 로컬 파일 권한을 확인하세요.")
        finally:
            timer.cancel()

    def job(self, identifier):
        with self._lock:
            if identifier not in self._jobs:
                raise UnknownJobError(identifier)
            return copy.deepcopy(self._jobs[identifier])

    def get(self, snapshot_id=None):
        with self._lock:
            if not self._snapshot:
                raise NoSnapshotError("사용 가능한 조회 결과가 없습니다. Notion을 새로 조회하세요.")
            if snapshot_id is not None and snapshot_id != self._snapshot["id"]:
                raise SnapshotConflictError(self._snapshot["id"])
            return copy.deepcopy(self._snapshot)

    def close(self):
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)
