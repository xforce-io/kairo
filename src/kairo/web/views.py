"""web console 路由(APIRouter):dashboard / workspace / 产物预览 / 写操作 / step。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from kairo.web.discovery import scan_workspaces
from kairo.web.render import render_markdown
from kairo.workspace import Workspace, WorkspaceNotFound

router = APIRouter()


def _open(request: Request, slug: str) -> Workspace:
    try:
        return Workspace.open(Path(request.app.state.root) / slug)
    except WorkspaceNotFound:
        raise HTTPException(status_code=404, detail="workspace not found")


def _safe_doc(ws: Workspace, relpath: str) -> Path:
    """把 workspace 相对路径解析为 .md 绝对路径;越界/非 md/不存在 → 404。"""
    target = (ws.root / relpath).resolve()
    root = ws.root.resolve()
    if root not in target.parents or target.suffix != ".md" or not target.is_file():
        raise HTTPException(status_code=404, detail="doc not found")
    return target


def _target_states(ws: Workspace):
    """各 target 的 (path, status) —— 给左栏状态点。"""
    state = ws.read_state()
    out = []
    for t in ws.constitution.targets:
        ts = state.targets.get(t.path)
        status = ts.status if ts else "missing"
        out.append({"path": t.path, "status": status})
    return out


@router.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"ok": True})


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    root = request.app.state.root
    items = scan_workspaces(root)
    return request.app.state.templates.TemplateResponse(
        request, "dashboard.html", {"items": items, "root": str(root)}
    )


@router.get("/w/{slug}", response_class=HTMLResponse)
def workspace_view(request: Request, slug: str) -> HTMLResponse:
    ws = _open(request, slug)
    refs = []
    for ref_id in ws.list_reference_ids():
        man = ws.read_manifest(ref_id)
        refs.append({"id": ref_id, "title": man.title, "cls": man.source_class})
    return request.app.state.templates.TemplateResponse(
        request,
        "workspace.html",
        {
            "slug": slug,
            "topic": ws.constitution.topic,
            "targets": _target_states(ws),
            "refs": refs,
        },
    )


@router.get("/w/{slug}/doc", response_class=HTMLResponse)
def doc_view(request: Request, slug: str, path: str) -> HTMLResponse:
    ws = _open(request, slug)
    target = _safe_doc(ws, path)
    return request.app.state.templates.TemplateResponse(
        request, "_doc.html", {"title": path, "html": render_markdown(target.read_text())}
    )


@router.get("/w/{slug}/ref/{ref_id}", response_class=HTMLResponse)
def ref_view(request: Request, slug: str, ref_id: str) -> HTMLResponse:
    ws = _open(request, slug)
    if ref_id not in ws.list_reference_ids():
        raise HTTPException(status_code=404, detail="reference not found")
    man = ws.read_manifest(ref_id)
    forms = [
        {
            "role": f.role,
            "location": f.location,
            "origin": f.origin,
            "is_md": f.location.endswith(".md"),
        }
        for f in man.forms
    ]
    return request.app.state.templates.TemplateResponse(
        request,
        "_ref.html",
        {"slug": slug, "ref_id": ref_id, "title": man.title, "cls": man.source_class, "forms": forms},
    )
