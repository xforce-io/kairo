"""web console 路由(APIRouter):dashboard / workspace / 产物预览 / 写操作 / step。"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from kairo.engine import accept as engine_accept
from kairo.web.discovery import scan_workspaces
from kairo.web.render import render_markdown
from kairo.web.tasks import stream_events
from kairo.workspace import AddError, Workspace, WorkspaceNotFound

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


def _refs_fragment(request: Request, ws: Workspace, slug: str) -> HTMLResponse:
    refs = []
    for ref_id in ws.list_reference_ids():
        man = ws.read_manifest(ref_id)
        refs.append({"id": ref_id, "title": man.title, "cls": man.source_class})
    return request.app.state.templates.TemplateResponse(
        request, "_refs_list.html", {"slug": slug, "refs": refs}
    )


def _save_upload(ws: Workspace, upload: UploadFile) -> Path:
    dest_dir = ws.root / ".kairo" / "uploads"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / Path(upload.filename or "upload.bin").name
    dest.write_bytes(upload.file.read())
    return dest


@router.post("/w/{slug}/ref", response_class=HTMLResponse)
def add_ref(
    request: Request,
    slug: str,
    path: str = Form(None),
    file: UploadFile = File(None),
) -> HTMLResponse:
    ws = _open(request, slug)
    if file is not None:
        src = _save_upload(ws, file)
    elif path:
        src = Path(path)
    else:
        raise HTTPException(status_code=400, detail="need file or path")
    try:
        ws.add([src])
    except AddError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _refs_fragment(request, ws, slug)


@router.post("/w/{slug}/corpus", response_class=HTMLResponse)
def add_corpus(request: Request, slug: str, path: str = Form(...)) -> HTMLResponse:
    ws = _open(request, slug)
    try:
        ws.add([Path(path)], source_class="corpus")
    except AddError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _refs_fragment(request, ws, slug)


@router.post("/w/{slug}/accept", response_class=HTMLResponse)
def accept_doc(request: Request, slug: str, doc: str = Form(...)) -> HTMLResponse:
    ws = _open(request, slug)
    engine_accept(ws, doc)
    state = ws.read_state()
    ts = state.targets.get(doc)
    status = ts.status if ts else "missing"
    return HTMLResponse(f'<span class="dot {status}"></span>{doc}: {status}')


@router.post("/w/{slug}/step", response_class=HTMLResponse)
def start_step(request: Request, slug: str, target: str = Form(None)) -> HTMLResponse:
    ws = _open(request, slug)
    reg = request.app.state.registry
    if reg.is_running(slug):
        return HTMLResponse('<p class="muted">⏳ 正在运行,请等待当前 step 结束。</p>')
    argv = [sys.executable, "-m", "kairo"] + (["re-step", target] if target else ["step"])
    task = reg.start(slug, ws.root, argv)
    return request.app.state.templates.TemplateResponse(
        request, "_step.html", {"slug": slug, "task_id": task.task_id}
    )


@router.get("/w/{slug}/step/{task_id}/stream")
def step_stream(request: Request, slug: str, task_id: str) -> StreamingResponse:
    task = request.app.state.registry.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return StreamingResponse(stream_events(task), media_type="text/event-stream")


@router.post("/w/{slug}/step/{task_id}/cancel", response_class=HTMLResponse)
def cancel_step(request: Request, slug: str, task_id: str) -> HTMLResponse:
    ok = request.app.state.registry.cancel(task_id)
    return HTMLResponse('<p class="muted">已取消。</p>' if ok else '<p class="muted">无法取消(已结束)。</p>')
