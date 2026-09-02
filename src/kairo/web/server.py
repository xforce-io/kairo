"""FastAPI app 工厂 + uvicorn 启动。app.state 持有 root / 模板 / 任务注册表。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from kairo.web.i18n import resolve_lang, translator
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
    ``mode="public-read"`` reuses the Console shell with a public-read gate (#200).
    Any other runtime mode fails closed (never silently falls back to Console).
    """
    if mode not in _ALLOWED_MODES:
        raise UnknownAppMode(
            f"unknown app mode: {mode!r} (expected console or public-read)"
        )
    public = mode == "public-read"
    app = FastAPI(
        title="kairo public-read" if public else "kairo console",
        **(
            {"docs_url": None, "redoc_url": None, "openapi_url": None}
            if public
            else {}
        ),
    )
    app.state.root = Path(root)
    app.state.public_read = public
    app.state.templates = Jinja2Templates(directory=str(_HERE / "templates"))
    app.state.registry = TaskRegistry()
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")
    app.include_router(router)
    if not public:
        @app.exception_handler(StarletteHTTPException)
        async def console_http_error(request: Request, exc: StarletteHTTPException):
            headers = exc.headers or {}
            if request.headers.get("hx-request") or "text/html" not in request.headers.get("accept", ""):
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=headers)
            lang = resolve_lang(request)
            t = translator(lang)
            message = exc.detail if isinstance(exc.detail, str) else t("error.request_failed")
            return app.state.templates.TemplateResponse(
                request,
                "error.html",
                {
                    "nav_active": "",
                    "lang": lang,
                    "t": t,
                    "public_read": False,
                    "status_code": exc.status_code,
                    "message": message,
                },
                status_code=exc.status_code,
                headers=headers,
            )
    if public:
        from kairo.web.public import attach_public_surface

        attach_public_surface(app)
    return app


def run(
    root: Path,
    port: int = 8787,
    *,
    mode: str = "console",
    host: str = "127.0.0.1",
) -> None:
    import uvicorn

    # Fail closed before binding — unknown mode must not spawn Console.
    app = create_app(Path(root), mode=mode)
    uvicorn.run(app, host=host, port=port)
