"""FastAPI app 工厂 + uvicorn 启动。app.state 持有 root / 模板 / 任务注册表。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from kairo.web.tasks import TaskRegistry
from kairo.web.views import router

_HERE = Path(__file__).parent


def create_app(root: Path) -> FastAPI:
    app = FastAPI(title="kairo console")
    app.state.root = Path(root)
    app.state.templates = Jinja2Templates(directory=str(_HERE / "templates"))
    app.state.registry = TaskRegistry()
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")
    app.include_router(router)
    return app


def run(root: Path, port: int = 8000) -> None:
    import uvicorn

    uvicorn.run(create_app(Path(root)), host="127.0.0.1", port=port)
