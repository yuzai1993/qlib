"""FastAPI 应用工厂（实盘监控仪表盘，只读）。"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import create_router


def create_app(config: dict, project_root: Path) -> FastAPI:
    app = FastAPI(title="Live Trading Monitor", version="1.0.0")

    app.include_router(create_router(config, project_root), prefix="/api")

    @app.middleware("http")
    async def no_stale_static(request, call_next):
        """静态资源强制走 etag 协商缓存，避免改版后浏览器用旧 JS/CSS。"""
        response = await call_next(request)
        if not request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True),
                  name="static")
    return app
