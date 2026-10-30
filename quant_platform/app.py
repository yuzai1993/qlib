"""Local platform service with live monitor queries on read-only SQLite connections."""

from __future__ import annotations

import asyncio
import hmac
import mimetypes
import re
import secrets
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .analyses import AnalysisStore, ConflictError
from .research import ResearchCatalog
from .ledgers import LedgerCatalog, UNAVAILABLE as MONITOR_UNAVAILABLE

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
MONITOR_PATHS = {
    "overview",
    "nav",
    "positions",
    "positions/history",
    "batches",
    "pipeline",
    "alerts",
    "cashflows",
    "corporate-actions",
    "predictions",
    "predictions/dates",
    "predictions/summary",
    "predictions/daily-mean",
    "predictions/instruments",
}
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_MONITOR_BYTES = 8 * 1024 * 1024


def _monitor_path(path: str) -> bool:
    return bool(path in MONITOR_PATHS or re.fullmatch(r"batches/[A-Za-z0-9_.-]{1,160}(?:/fills)?", path)) and not any(
        part in {".", ".."} for part in path.split("/")
    )


def create_app(
    research_root: Path, data_root: Path | None = None, *, live_config: dict | None = None, project_root: Path | None = None
) -> FastAPI:
    research_root = Path(research_root).resolve()
    store = AnalysisStore(data_root, research_root)
    catalog = ResearchCatalog(research_root)
    csrf_token = secrets.token_urlsafe(32)
    app = FastAPI(title="量化研究平台", version="1.1.0", docs_url=None, redoc_url=None, openapi_url=None)
    monitor_slots = asyncio.Semaphore(6)
    ledgers = LedgerCatalog(Path(project_root or Path(__file__).resolve().parents[1]), live_config)
    ledgers.install_routes(app)

    async def bounded_monitor(request, call_next):
        if len(request.url.query) > 4096:
            return JSONResponse({"detail": "查询参数过长"}, status_code=400)
        try:
            await asyncio.wait_for(monitor_slots.acquire(), timeout=0.1)
        except TimeoutError:
            return JSONResponse({"detail": "监控查询繁忙，请稍后重试"}, status_code=503)
        try:
            response = await call_next(request)
            oversized = JSONResponse({"detail": "监控响应过大，请缩小查询范围"}, status_code=503)
            if int(response.headers.get("content-length", "0")) > MAX_MONITOR_BYTES:
                await response.body_iterator.aclose()
                return oversized
            body = bytearray()
            async for chunk in response.body_iterator:
                body.extend(chunk)
                if len(body) > MAX_MONITOR_BYTES:
                    await response.body_iterator.aclose()
                    return oversized
            return Response(bytes(body), status_code=response.status_code, headers=dict(response.headers))
        finally:
            monitor_slots.release()

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        host = request.headers.get("host", "")
        try:
            host_parts = urlsplit("//" + host)
            valid_host = (
                host_parts.hostname in LOCAL_HOSTS
                and not host_parts.username
                and not host_parts.password
                and host_parts.path == ""
            )
            host_parts.port
        except ValueError:
            valid_host = False
        if not valid_host:
            return JSONResponse({"detail": "仅允许本机访问"}, status_code=400)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if (
                origin != f"{request.url.scheme}://{host}"
                or request.headers.get("sec-fetch-site") == "cross-site"
                or not hmac.compare_digest(request.headers.get("x-csrf-token", ""), csrf_token)
            ):
                return JSONResponse({"detail": "请从本平台页面保存分析"}, status_code=403)
            raw_length = request.headers.get("content-length", "0")
            if not raw_length.isdigit() or int(raw_length) > MAX_REQUEST_BYTES:
                return JSONResponse({"detail": "请求超过 2 MiB"}, status_code=413)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > MAX_REQUEST_BYTES:
                    return JSONResponse({"detail": "请求超过 2 MiB"}, status_code=413)
            request._body = bytes(body)
        path = request.url.path.removeprefix("/api/")
        if request.url.path.startswith("/api/batches/") and not _monitor_path(path):
            return JSONResponse({"detail": "接口不存在"}, status_code=404)
        if request.url.path.startswith("/api/") and (_monitor_path(path) or path in {"health", "ledgers"}):
            response = await bounded_monitor(request, call_next)
        else:
            response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api") else "no-cache"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        return response

    @app.exception_handler(ConflictError)
    async def conflict_handler(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(ValueError)
    async def value_handler(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(FileNotFoundError)
    async def missing_handler(request, exc):
        return JSONResponse({"detail": "记录或原始文件不存在"}, status_code=404)

    @app.exception_handler(sqlite3.Error)
    async def ledger_error_handler(request, exc):
        return JSONResponse({"detail": MONITOR_UNAVAILABLE}, status_code=503)

    @app.get("/api/session")
    def session():
        return {"csrf_token": csrf_token}

    @app.get("/api/health")
    def health():
        monitor_ready = bool(ledgers.available())
        return {
            "status": "ok",
            "research_root": str(research_root),
            "monitor_mode": "direct-read-only",
            "monitor_status": "ready" if monitor_ready else "unavailable",
            "analysis_storage": str(store.root),
        }

    @app.get("/api/ledgers")
    def monitor_ledgers():
        return ledgers.snapshot()

    @app.get("/api/research")
    def research():
        return catalog.snapshot()

    @app.get("/api/research/compare")
    def compare(id: list[str] = Query(default=[])):
        if not 2 <= len(id) <= 12:
            raise ValueError("请选择 2–12 个同口径版本进行比较")
        return catalog.compare(id)

    @app.get("/api/resources/{resource_id}")
    def resource(resource_id: str, download: bool = False):
        path = catalog.resource(resource_id)
        if path is None or not path.is_file():
            raise FileNotFoundError("原始文件已清理或不可用")
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("资源超过 32 MiB，请在本地查看原文件")
        media = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix.lower() in {".html", ".htm"}:
            media = "text/html"
        # CSP sandbox also applies if the user opens a report in a new top-level tab.
        headers = {
            "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"
        }
        return FileResponse(path, media_type=media, filename=path.name if download else None, headers=headers)

    @app.get("/api/analyses")
    def analyses():
        return {"records": store.list()}

    @app.post("/api/analyses", status_code=201)
    def create_analysis(payload: dict):
        return store.create(payload)

    @app.put("/api/analyses/{record_id}")
    def update_analysis(record_id: str, payload: dict):
        return store.update(record_id, payload)

    @app.get("/api/analyses/{record_id}/export")
    def export_analysis(record_id: str):
        content = store.export(record_id)
        return Response(
            content,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="analysis-{record_id}.zip"'},
        )

    @app.get("/api/{monitor_path:path}")
    async def unavailable_monitor(monitor_path: str):
        if not _monitor_path(monitor_path):
            return JSONResponse({"detail": "接口不存在"}, status_code=404)
        return JSONResponse({"detail": MONITOR_UNAVAILABLE}, status_code=503)

    static = Path(__file__).parent / "static"
    if static.is_dir():
        app.mount("/static", StaticFiles(directory=str(static)), name="static")

        @app.get("/")
        def index():
            return FileResponse(static / "index.html")

    return app
