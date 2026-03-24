"""FastAPI application factory."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import create_router


def create_app(config: dict, project_root: Path) -> FastAPI:
    app = FastAPI(title="Paper Trading Dashboard", version="1.0.0")

    router = create_router(config, project_root)
    app.include_router(router, prefix="/api")

    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app
