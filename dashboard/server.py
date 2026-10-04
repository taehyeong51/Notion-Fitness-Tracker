"""One local origin serves a static React app and read-only analysis APIs."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import analytics
from .models import AnalysisRequest, ExportRequest
from .reader import Reader
from .snapshots import (
    NoSnapshotError, SnapshotConflictError, SnapshotManager, UnknownJobError,
)

ROOT = Path(__file__).resolve().parent.parent
MAX_BODY_BYTES = 128 * 1024


def create_app(
    config_path: str | Path = ROOT / ".local/dashboard-config.json",
    cache_path: str | Path = ROOT / ".local/dashboard-cache.json",
    *,
    manager=None,
    allowed_hosts: list[str] | None = None,
    static_dir: str | Path | None = None,
) -> FastAPI:
    manager = manager or SnapshotManager(lambda: Reader(config_path), cache_path)
    permitted_hosts = {
        host.lower().rstrip(".")
        for host in (allowed_hosts or ["127.0.0.1", "localhost", "::1"])
    }

    @asynccontextmanager
    async def lifespan(app):
        yield
        manager.close()

    app = FastAPI(
        title="Fitness Tracker", docs_url=None, redoc_url=None,
        openapi_url=None, lifespan=lifespan,
    )
    app.state.snapshots = manager

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        # Return field locations and diagnoses, never echo arbitrary body values.
        errors = [{"loc": list(error["loc"]), "msg": error["msg"]}
                  for error in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        try:
            hostname = (request.url.hostname or "").lower().rstrip(".")
        except ValueError:
            hostname = ""
        if hostname not in permitted_hosts:
            return JSONResponse({"detail": "허용되지 않은 호스트입니다."}, status_code=400)
        origin = request.headers.get("origin")
        if origin:
            try:
                parsed = urlsplit(origin)
                origin_host = (parsed.hostname or "").lower().rstrip(".")
                origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
                request_port = request.url.port or (443 if request.url.scheme == "https" else 80)
                same_origin = (
                    parsed.scheme == request.url.scheme
                    and origin_host == hostname
                    and origin_port == request_port
                    and not parsed.username and not parsed.password
                    and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment
                )
            except ValueError:
                same_origin = False
            if not same_origin:
                return JSONResponse({"detail": "같은 주소의 웹에서 요청하세요."}, status_code=403)
        length = request.headers.get("content-length")
        if length:
            try:
                if int(length) < 0 or int(length) > MAX_BODY_BYTES:
                    return JSONResponse({"detail": "요청이 너무 큽니다."}, status_code=413)
            except ValueError:
                return JSONResponse({"detail": "잘못된 요청 길이입니다."}, status_code=400)
        if request.method in {"POST", "PUT", "PATCH"}:
            chunks, received = [], 0
            async for chunk in request.stream():
                received += len(chunk)
                if received > MAX_BODY_BYTES:
                    return JSONResponse({"detail": "요청이 너무 큽니다."}, status_code=413)
                chunks.append(chunk)
            # Starlette's CachedRequest replays its cached body to call_next.
            request._body = b"".join(chunks)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(NoSnapshotError)
    async def no_snapshot(request, exc):
        return JSONResponse(
            {"detail": "사용 가능한 데이터가 없습니다. Notion 조회를 실행하세요.",
             "code": "NO_SNAPSHOT"}, status_code=503,
        )

    @app.exception_handler(SnapshotConflictError)
    async def snapshot_conflict(request, exc):
        return JSONResponse(
            {"detail": "새 데이터가 조회되었습니다. 분석을 다시 요청하세요.",
             "snapshot_id": manager.status().get("snapshot_id"),
             "code": "SNAPSHOT_CHANGED"}, status_code=409,
        )

    @app.exception_handler(UnknownJobError)
    async def unknown_job(request, exc):
        return JSONResponse({"detail": "조회 작업을 찾을 수 없습니다."}, status_code=404)

    @app.get("/healthz")
    def health():
        return {"status": "ok", "read_only": True}

    @app.get("/api/status")
    def status():
        return manager.status()

    @app.post("/api/refresh", status_code=202)
    def refresh():
        return manager.refresh()

    @app.get("/api/refresh/{job_id}")
    def refresh_job(job_id: str):
        return manager.job(job_id)

    @app.get("/api/catalog")
    def get_catalog():
        return analytics.catalog(manager.get())

    @app.post("/api/analysis")
    async def analysis(payload: AnalysisRequest):
        snapshot = manager.get(payload.snapshot_id)
        try:
            result = await run_in_threadpool(
                analytics.analyze, snapshot, payload.model_dump(mode="json"),
            )
        except ValueError as exc:
            raise HTTPException(400, detail=str(exc)) from None
        state = manager.status()
        if state.get("snapshot_id") != payload.snapshot_id:
            raise SnapshotConflictError(state.get("snapshot_id"))
        result.setdefault("meta", {}).update({
            "refresh_state": state.get("refresh_state"),
            "using_previous_data": state.get("using_previous_data", False),
        })
        return result

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str, snapshot_id: str):
        snapshot = manager.get(snapshot_id)
        try:
            return analytics.session_detail(snapshot, session_id)
        except (KeyError, ValueError):
            raise HTTPException(404, detail="해당 운동을 찾을 수 없습니다.") from None

    @app.post("/api/export/csv")
    async def export(payload: ExportRequest):
        snapshot = manager.get(payload.snapshot_id)
        try:
            content = await run_in_threadpool(
                analytics.export_csv, snapshot, payload.model_dump(mode="json"), payload.kind,
            )
        except ValueError as exc:
            raise HTTPException(400, detail=str(exc)) from None
        if manager.status().get("snapshot_id") != payload.snapshot_id:
            raise SnapshotConflictError(manager.status().get("snapshot_id"))
        filename = f"fitness-{payload.kind}-{snapshot.get('fetched_at', 'export')[:10]}.csv"
        if isinstance(content, str):
            content = content.lstrip("\ufeff").encode("utf-8-sig")
        return Response(content, media_type="text/csv; charset=utf-8", headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        })

    # An unknown API path must never become an HTML success response.
    @app.api_route("/api/{unknown:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def unknown_api(unknown: str):
        raise HTTPException(404, detail="존재하지 않는 API입니다.")

    built = Path(static_dir) if static_dir is not None else ROOT / "web/dist"
    if (built / "index.html").is_file():
        app.mount("/", StaticFiles(directory=built, html=True), name="web")
    else:
        @app.get("/", response_class=HTMLResponse)
        def unbuilt():
            return HTMLResponse(
                '<html lang="ko"><meta charset="utf-8"><title>Fitness Tracker</title>'
                '<h1>웹 빌드가 필요합니다</h1><p>저장소의 web 디렉터리에서 '
                '<code>npm ci</code>, <code>npm run build</code>를 실행한 뒤 서버를 다시 켜세요.</p></html>',
                status_code=503,
            )
    return app
