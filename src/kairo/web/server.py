"""FastAPI app 工厂 + uvicorn 启动。app.state 持有 root / 模板 / 任务注册表。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from kairo.web.tasks import TaskRegistry
from kairo.web.views import router

_HERE = Path(__file__).parent

AppMode = Literal["console", "public-read"]
_ALLOWED_MODES = frozenset({"console", "public-read"})


class UnknownAppMode(ValueError):
    """Raised when create_app/run receive an unsupported runtime mode."""


def create_app(root: Path, *, mode: str = "console") -> FastAPI:
    """Build a web app.

    ``mode="console"`` (default) is the local Console.
    ``mode="public-read"`` is the isolated anonymous public surface (#118).
    Any other runtime mode fails closed (never silently falls back to Console).
    """
    if mode not in _ALLOWED_MODES:
        raise UnknownAppMode(
            f"unknown app mode: {mode!r} (expected console or public-read)"
        )
    if mode == "public-read":
        from kairo.web.public import create_public_app

        return create_public_app(Path(root))

    app = FastAPI(title="kairo console")
    app.state.root = Path(root)
    app.state.templates = Jinja2Templates(directory=str(_HERE / "templates"))
    app.state.registry = TaskRegistry()
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")
    app.include_router(router)
    return app


def run(root: Path, port: int = 8787, *, mode: str = "console") -> None:
    import uvicorn

    # Fail closed before binding — unknown mode must not spawn Console.
    app = create_app(Path(root), mode=mode)
    uvicorn.run(app, host="127.0.0.1", port=port)
