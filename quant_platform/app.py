"""Local-only platform service. Live data is fetched over an explicit GET proxy."""

from __future__ import annotations

import asyncio
import hmac
import mimetypes
import re
import secrets
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .analyses import AnalysisStore, ConflictError
from .research import ResearchCatalog

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
MAX_PROXY_BYTES = 8 * 1024 * 1024


def _monitor_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1"}
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("监控上游必须是固定的本机 HTTP 地址")
    if not parsed.port:
        raise ValueError("监控上游必须明确指定端口")
    return value.rstrip("/")


def create_app(
    research_root: Path, data_root: Path, monitor_url: str = "http://127.0.0.1:8082", *, transport=None
) -> FastAPI:
    upstream = _monitor_origin(monitor_url)
    research_root = Path(research_root).resolve()
    store = AnalysisStore(data_root, research_root)
    catalog = ResearchCatalog(research_root)
    csrf_token = secrets.token_urlsafe(32)
    proxy_slots = asyncio.Semaphore(6)
    app = FastAPI(title="量化研究平台", version="1.0.0", docs_url=None, redoc_url=None, openapi_url=None)

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

    @app.get("/api/session")
    def session():
        return {"csrf_token": csrf_token}

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "research_root": str(research_root),
            "monitor_url": upstream,
            "analysis_storage": str(store.root),
        }

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
    async def monitor(monitor_path: str, request: Request):
        allowed = monitor_path in MONITOR_PATHS or re.fullmatch(
            r"batches/[A-Za-z0-9_.-]{1,160}(?:/fills)?", monitor_path
        )
        if not allowed or any(part in {".", ".."} for part in monitor_path.split("/")):
            return JSONResponse({"detail": "接口不存在"}, status_code=404)
        if len(request.url.query) > 4096:
            raise ValueError("查询参数过长")
        try:
            async with proxy_slots:
                async with httpx.AsyncClient(
                    timeout=5, follow_redirects=False, trust_env=False, transport=transport
                ) as client:
                    async with client.stream(
                        "GET", upstream + "/api/" + monitor_path, params=request.query_params.multi_items()
                    ) as response:
                        if response.status_code != 200:
                            return JSONResponse({"detail": "实盘监控暂不可用，研究与分析仍可使用"}, status_code=503)
                        if "application/json" not in response.headers.get("content-type", ""):
                            return JSONResponse({"detail": "实盘监控返回格式异常"}, status_code=503)
                        content = bytearray()
                        async for chunk in response.aiter_bytes():
                            content.extend(chunk)
                            if len(content) > MAX_PROXY_BYTES:
                                return JSONResponse({"detail": "监控响应过大，请缩小查询范围"}, status_code=503)
                        return Response(bytes(content), media_type="application/json")
        except httpx.HTTPError:
            return JSONResponse({"detail": "实盘监控连接失败，研究与分析仍可使用"}, status_code=503)

    static = Path(__file__).parent / "static"
    if static.is_dir():
        app.mount("/static", StaticFiles(directory=str(static)), name="static")

        @app.get("/")
        def index():
            return FileResponse(static / "index.html")

    return app
