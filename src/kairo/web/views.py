"""web console 路由(APIRouter):dashboard / workspace / 产物预览 / 写操作 / step。"""

from __future__ import annotations

import json
import sys
from html import escape
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)

from kairo.engine import (
    ProseError,
    can_generate_prose,
    prose_precheck,
    ref_product_blocks,
    workspace_run_plan,
)
from kairo.engine import accept as engine_accept
from kairo.web.discovery import scan_workspaces
from kairo.web.i18n import SUPPORTED, resolve_lang, translator
from kairo.web.render import render_markdown
from kairo.web.tasks import stream_events
from kairo.workspace import AddError, Workspace, WorkspaceNotFound

router = APIRouter()


def _t(request: Request):
    """请求语言 → translator t(key)。"""
    return translator(resolve_lang(request))


def _render(request: Request, name: str, ctx: dict) -> HTMLResponse:
    """统一渲染:注入 lang + t。所有 TemplateResponse 走这里。"""
    lang = resolve_lang(request)
    return request.app.state.templates.TemplateResponse(
        request, name, {**ctx, "lang": lang, "t": translator(lang)}
    )


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


def _preview_html(ws: Workspace, location: str) -> str | None:
    """把 workspace 内的 .md 渲染成 HTML;越界/缺失 → None(右栏给提示,不报错)。"""
    try:
        return render_markdown(_safe_doc(ws, location).read_text())
    except HTTPException:
        return None


# 可内联预览的文本后缀(.md 走 markdown,其余按纯文本保留换行)
_TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".text", ".vtt", ".srt", ".log"}


def _form_path(ws: Workspace, location: str) -> Path:
    """form.location 解析为绝对路径:相对 → ws 内;绝对 → 原样(均为 manifest 登记的可信路径)。"""
    p = Path(location)
    return p if p.is_absolute() else ws.root / location


def _is_text_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in _TEXT_SUFFIXES


# 可内联预览的图片后缀(附件:点击在阅读区显示原图)
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".heic"}


def _is_image_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES


def _render_doc(path: Path) -> str:
    """.md → markdown;其余文本 → 保留换行的 <pre>(转义)。勿用于图片。"""
    text = path.read_text(errors="replace")
    if path.suffix.lower() in (".md", ".markdown"):
        return render_markdown(text)
    return f'<pre class="doc-plain">{escape(text)}</pre>'


def _form_preview_html(ws: Workspace, slug: str, ref_id: str, form: dict) -> str | None:
    """按 form 类型生成预览 HTML:图片走 <img>,文本走 markdown/pre。"""
    path = _form_path(ws, form["location"])
    if _is_image_file(path):
        src = f"/w/{quote(slug)}/ref/{quote(ref_id)}/file/{quote(form['key'])}"
        return (
            f'<img class="doc-img" src="{src}" alt="{escape(path.name)}">'
        )
    if _is_text_file(path):
        return _render_doc(path)
    return None


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
    return _render(request, "dashboard.html", {"items": items, "root": str(root)})


@router.get("/set-lang/{code}")
def set_lang(request: Request, code: str) -> RedirectResponse:
    nxt = request.headers.get("referer") or "/"
    resp = RedirectResponse(nxt, status_code=303)
    if code in SUPPORTED:
        resp.set_cookie("lang", code, max_age=31_536_000, samesite="lax")
    return resp


@router.post("/workspaces", response_class=HTMLResponse)
def create_workspace(request: Request, topic: str = Form("")) -> HTMLResponse:
    t = _t(request)
    root = Path(request.app.state.root)
    topic = topic.strip()
    if not topic:
        raise HTTPException(status_code=400, detail=t("err.topic_empty"))
    if len(topic) > 64:
        raise HTTPException(status_code=400, detail=t("err.topic_too_long"))
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in topic):
        raise HTTPException(status_code=400, detail=t("err.topic_control"))
    if "/" in topic or "\\" in topic or topic.startswith(".") or topic in (".", ".."):
        raise HTTPException(status_code=400, detail=t("err.topic_illegal"))
    dest = (root / topic).resolve()
    if dest.parent != root.resolve():
        raise HTTPException(status_code=400, detail=t("err.topic_invalid"))
    if dest.exists():
        raise HTTPException(status_code=400, detail=t("err.topic_exists").format(topic=topic))
    Workspace.init(dest, topic=topic)
    return HTMLResponse("", headers={"HX-Redirect": "/w/" + quote(topic)})


def _split_refs(ws: Workspace):
    """参考分两层:stream(观测,进『参考』组)/ corpus(基线,单独置底)。"""
    streams, corpus = [], []
    for ref_id in ws.list_reference_ids():
        man = ws.read_manifest(ref_id)
        item = {"id": ref_id, "title": man.title}
        (corpus if man.source_class == "corpus" else streams).append(item)
    return streams, corpus


def _run_button_ctx(request: Request, ws: Workspace, slug: str) -> dict:
    """#75 主按钮文案与是否可点。"""
    t = _t(request)
    running = request.app.state.registry.is_running(slug)
    plan = workspace_run_plan(ws)
    mode = plan["mode"]
    if running:
        return {
            "run_mode": "running",
            "run_label": t("run.running"),
            "run_disabled": True,
            "run_pending": plan["pending_count"],
            "run_blocked": plan["blocked_count"],
        }
    labels = {
        "clean": t("run.clean"),
        "run": t("run.run"),
        "retry": t("run.retry").format(n=plan["blocked_count"]),
        "run_and_retry": t("run.run_and_retry").format(n=plan["blocked_count"]),
    }
    return {
        "run_mode": mode,
        "run_label": labels[mode],
        "run_disabled": mode == "clean",
        "run_pending": plan["pending_count"],
        "run_blocked": plan["blocked_count"],
    }


@router.get("/w/{slug}", response_class=HTMLResponse)
def workspace_view(request: Request, slug: str) -> HTMLResponse:
    ws = _open(request, slug)
    streams, corpus = _split_refs(ws)
    return _render(
        request,
        "workspace.html",
        {
            "slug": slug,
            "topic": ws.constitution.topic,
            "targets": _target_states(ws),
            "streams": streams,
            "corpus": corpus,
            **_run_button_ctx(request, ws, slug),
        },
    )


@router.get("/w/{slug}/doc", response_class=HTMLResponse)
def doc_view(request: Request, slug: str, path: str) -> HTMLResponse:
    ws = _open(request, slug)
    target = _safe_doc(ws, path)
    exportable = path in {t.path for t in ws.constitution.targets}
    return _render(
        request,
        "_doc.html",
        {"title": path, "html": render_markdown(target.read_text()), "exportable": exportable},
    )


def _role_label(role: str, t) -> str:
    key = f"role.{role}"
    label = t(key)
    return label if label != key else role


def _ref_forms(ws: Workspace, ref_id: str, man, t) -> list[dict]:
    """form 列表(标注可预览 + 预览 key + 人读标签)。digest 是这条 reference 的目的产物,
    置顶以示主次;其余形态(音频/转写/附件等)按 manifest 顺序随后。"""
    forms = []
    if (ws.references_dir() / ref_id / "digest.md").is_file():
        forms.append(
            {
                "role": "digest",
                "role_label": _role_label("digest", t),
                "location": f"references/{ref_id}/digest.md",
                "previewable": True,
                "key": "digest",
            }
        )
    for i, f in enumerate(man.forms):
        p = _form_path(ws, f.location)
        forms.append(
            {
                "role": f.role,
                "role_label": _role_label(f.role, t),
                "location": f.location,
                "previewable": _is_text_file(p) or _is_image_file(p),
                "key": str(i),
            }
        )
    return forms


@router.get("/w/{slug}/ref/{ref_id}", response_class=HTMLResponse)
def ref_view(request: Request, slug: str, ref_id: str) -> HTMLResponse:
    """右栏元信息 + (OOB)中间预览主形态(默认 digest 摘要 → 否则 transcript → 首个可预览)。"""
    ws = _open(request, slug)
    if ref_id not in ws.list_reference_ids():
        raise HTTPException(status_code=404, detail="reference not found")
    t = _t(request)
    man = ws.read_manifest(ref_id)
    forms = _ref_forms(ws, ref_id, man, t)
    # 优先正文形态;避免尚无 transcript 时默认把 PNG 当文本打开
    primary = (
        next((f for f in forms if f["role"] == "digest" and f["previewable"]), None)
        or next((f for f in forms if f["role"] == "transcript" and f["previewable"]), None)
        or next((f for f in forms if f["role"] == "source_text" and f["previewable"]), None)
        or next((f for f in forms if f["role"] == "prose" and f["previewable"]), None)
        or next((f for f in forms if f["role"] == "attachment" and f["previewable"]), None)
        or next((f for f in forms if f["previewable"]), None)
    )
    sc = ws.constitution.source_classes.get(man.source_class)
    preview_title = f"{man.title} · {primary['role_label']}" if primary else ""
    preview_html = (
        _form_preview_html(ws, slug, ref_id, primary) if primary else None
    )
    return _render(
        request,
        "_ref_meta.html",
        {
            "slug": slug,
            "ref_id": ref_id,
            "title": man.title,
            "label": sc.label if sc else man.source_class,
            "hint": sc.hint if sc else "",
            "forms": forms,
            "preview_key": primary["key"] if primary else "",
            "preview_title": preview_title,
            "preview_html": preview_html,
            # 主预览是 digest 时,OOB 画布与 target 同款可导出 PDF
            "exportable": bool(primary and primary["role"] == "digest"),
            "empty_hint": t("ref.empty_hint"),
            "can_generate_prose": can_generate_prose(ws, ref_id),
            "blocks": ref_product_blocks(ws, ref_id),
        },
    )


@router.get("/w/{slug}/ref/{ref_id}/form/{key}", response_class=HTMLResponse)
def ref_form_view(request: Request, slug: str, ref_id: str, key: str) -> HTMLResponse:
    """预览某 form 正文。路径由服务端从 manifest 解析(可信),客户端只给受校验的 ref_id + key。"""
    ws = _open(request, slug)
    if ref_id not in ws.list_reference_ids():
        raise HTTPException(status_code=404, detail="reference not found")
    man = ws.read_manifest(ref_id)
    if key == "digest":
        path, role = ws.references_dir() / ref_id / "digest.md", "digest"
    else:
        try:
            idx = int(key)
        except ValueError:
            raise HTTPException(status_code=404, detail="form not found")
        if not 0 <= idx < len(man.forms):
            raise HTTPException(status_code=404, detail="form not found")
        path, role = _form_path(ws, man.forms[idx].location), man.forms[idx].role
    t = _t(request)
    title = f"{man.title} · {_role_label(role, t)}"
    if _is_image_file(path):
        img = (
            f'<img class="doc-img" src="/w/{quote(slug)}/ref/{ref_id}/file/{quote(key)}"'
            f' alt="{escape(path.name)}">'
        )
        return _render(request, "_doc.html", {"title": title, "html": img})
    if not _is_text_file(path):
        raise HTTPException(status_code=404, detail="not previewable")
    # digest 是这条 reference 的目的产物,与 target 同款可导出 PDF;其它形态(转写/音频)不导出
    return _render(
        request,
        "_doc.html",
        {"title": title, "html": _render_doc(path), "exportable": key == "digest"},
    )


@router.get("/w/{slug}/ref/{ref_id}/file/{key}")
def ref_form_file(request: Request, slug: str, ref_id: str, key: str) -> FileResponse:
    """直供某 form 的原始文件字节(图片预览用)。路径由服务端从 manifest 解析(可信),
    再校验落在 workspace 内,杜绝越界。"""
    ws = _open(request, slug)
    if ref_id not in ws.list_reference_ids():
        raise HTTPException(status_code=404, detail="reference not found")
    man = ws.read_manifest(ref_id)
    try:
        idx = int(key)
    except ValueError:
        raise HTTPException(status_code=404, detail="form not found")
    if not 0 <= idx < len(man.forms):
        raise HTTPException(status_code=404, detail="form not found")
    path = _form_path(ws, man.forms[idx].location).resolve()
    if ws.root.resolve() not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(path)


@router.get("/w/{slug}/target", response_class=HTMLResponse)
def target_view(request: Request, slug: str, path: str) -> HTMLResponse:
    """右栏产物元信息(状态/blocked 原因) + (OOB)中间预览正文。"""
    ws = _open(request, slug)
    if path not in {t.path for t in ws.constitution.targets}:
        raise HTTPException(status_code=404, detail="target not found")
    ts = ws.read_state().targets.get(path)
    status = ts.status if ts else "missing"
    has_doc = (ws.root / path).is_file()
    return _render(
        request,
        "_target_meta.html",
        {
            "slug": slug,
            "path": path,
            "status": status,
            "reason": ts.reason if ts else None,
            "has_doc": has_doc,
            "preview_title": path,
            "preview_html": _preview_html(ws, path) if has_doc else None,
            "exportable": True,
            "empty_hint": _t(request)("target.empty_hint"),
        },
    )


def _refs_fragment(request: Request, ws: Workspace, slug: str) -> HTMLResponse:
    # 仅 stream:该片段唯一的注入点是参考组的上传表单;corpus 自成一组,不混入
    streams, _ = _split_refs(ws)
    return _render(request, "_refs_list.html", {"slug": slug, "refs": streams})


def _save_upload_to(dest_dir: Path, upload: UploadFile) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / Path(upload.filename or "upload.bin").name
    dest.write_bytes(upload.file.read())
    return dest


def _save_upload(ws: Workspace, upload: UploadFile) -> Path:
    return _save_upload_to(ws.root / ".kairo" / "uploads", upload)


@router.post("/w/{slug}/ref", response_class=HTMLResponse)
def add_ref(
    request: Request,
    slug: str,
    path: str = Form(None),
    file: UploadFile = File(None),
    # 表单字段名仍为 copy;参数名避开 pydantic BaseModel.copy 阴影
    copy_flag: Annotated[str | None, Form(alias="copy")] = None,
) -> HTMLResponse:
    """统一摄入:路径(+可选 copy)或浏览器文件(必物化到 uploads)。"""
    ws = _open(request, slug)
    # 空 file 域(合并表单)视为未上传
    has_file = file is not None and bool(file.filename)
    try:
        if has_file:
            src = _save_upload(ws, file)  # 浏览器无稳定 path → 必 copy
            ws.add([src])
        elif path:
            # checkbox 未勾选时字段缺失;勾选时常为 "1" / "on"
            do_copy = bool(copy_flag) and str(copy_flag).lower() not in (
                "0",
                "false",
                "off",
            )
            ws.add([Path(path)], copy=do_copy)
        else:
            raise HTTPException(status_code=400, detail="need file or path")
    except AddError as e:
        raise HTTPException(status_code=400, detail=str(e))
    resp = _refs_fragment(request, ws, slug)
    return _with_running_add_toast(request, slug, resp)


def _with_running_add_toast(
    request: Request, slug: str, resp: HTMLResponse
) -> HTMLResponse:
    """#75 方案松:运行中仍可添加,但 toast 说明下次运行才处理。"""
    if request.app.state.registry.is_running(slug):
        msg = _t(request)("run.add_while_running")
        resp.headers["HX-Trigger"] = json.dumps({"kairoToast": msg})
    return resp


@router.post("/w/{slug}/ref/{ref_id}/attach", response_class=HTMLResponse)
def attach_to_ref(
    request: Request,
    slug: str,
    ref_id: str,
    path: str = Form(None),
    files: list[UploadFile] = File(None),
) -> HTMLResponse:
    ws = _open(request, slug)
    if ref_id not in ws.list_reference_ids():
        raise HTTPException(status_code=404, detail="reference not found")
    ref_dir = ws.references_dir() / ref_id
    uploads = [f for f in (files or []) if f.filename]
    try:
        if uploads:
            srcs = [_save_upload_to(ref_dir, f) for f in uploads]  # 浏览器 → 必 copy 进 ref
            ws.add(srcs, ref_id=ref_id)
        elif path:
            # 路径 attach:统一走 copy=True 物化进 ref 目录(自包含,#44/#64)
            ws.add([Path(path)], ref_id=ref_id, copy=True)
        else:
            raise HTTPException(status_code=400, detail="need file or path")
    except AddError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # 复用 ref 详情渲染,刷新右栏元信息
    return ref_view(request, slug, ref_id)


@router.post("/w/{slug}/ref/{ref_id}/title", response_class=HTMLResponse)
def rename_ref(
    request: Request, slug: str, ref_id: str, title: str = Form(...)
) -> HTMLResponse:
    """重命名一条 reference 的展示名。title 仅供人读,不动 id/目录/产物溯源。"""
    ws = _open(request, slug)
    if ref_id not in ws.list_reference_ids():
        raise HTTPException(status_code=404, detail="reference not found")
    try:
        ws.set_title(ref_id, title)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # 复用 ref 详情渲染,刷新右栏元信息(含新标题)
    return ref_view(request, slug, ref_id)


def _entry_rows(entries) -> list[dict]:
    return [
        {
            "name": e.name,
            "note": e.note,
            "aka": ", ".join(e.aka),
            "tags": ", ".join(e.tags),
        }
        for e in entries
    ]


def _parse_aka(aka: str) -> list[str]:
    return [p.strip() for p in aka.replace("，", ",").split(",") if p.strip()]


def _parse_tags(tags: str) -> list[str]:
    return [p.strip() for p in tags.replace("，", ",").split(",") if p.strip()]


def _glossary_fragment(request: Request, ws: Workspace, slug: str) -> HTMLResponse:
    from kairo.glossary import (
        load_glossary_file,
        machine_glossary_path,
        root_glossary_path,
    )

    t = _t(request)
    serve_root = Path(request.app.state.root)
    shared = load_glossary_file(root_glossary_path(serve_root))
    machine = load_glossary_file(machine_glossary_path())
    local = ws.constitution.glossary
    return _render(
        request,
        "_glossary.html",
        {
            "slug": slug,
            "shared_entries": _entry_rows(shared),
            "local_entries": _entry_rows(local),
            "shared_count": len(shared),
            "local_count": len(local),
            "machine_count": len(machine),
            "hint": t("glossary.restep_hint"),
            "shared_hint": t("glossary.shared_hint"),
        },
    )


@router.get("/w/{slug}/glossary", response_class=HTMLResponse)
def glossary_view(request: Request, slug: str) -> HTMLResponse:
    """#69/#71:右栏真名册面板(公共 root + 本工作区)。"""
    return _glossary_fragment(request, _open(request, slug), slug)


@router.post("/w/{slug}/glossary", response_class=HTMLResponse)
def glossary_add(
    request: Request,
    slug: str,
    name: str = Form(...),
    note: str = Form(""),
    aka: str = Form(""),
    tags: str = Form(""),
    scope: str = Form("workspace"),
) -> HTMLResponse:
    """追加一条;scope=workspace|shared(root glossary.yaml)。"""
    from kairo.glossary import add_entry, load_glossary_file, root_glossary_path, save_glossary_file

    ws = _open(request, slug)
    parts = _parse_aka(aka)
    tag_parts = _parse_tags(tags)
    try:
        if scope == "shared":
            path = root_glossary_path(Path(request.app.state.root))
            entries = add_entry(
                load_glossary_file(path), name, note=note, aka=parts, tags=tag_parts
            )
            save_glossary_file(path, entries)
        else:
            ws.add_glossary_entry(name, note=note, aka=parts, tags=tag_parts)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _glossary_fragment(request, ws, slug)


@router.post("/w/{slug}/glossary/{index}/delete", response_class=HTMLResponse)
def glossary_delete(
    request: Request,
    slug: str,
    index: int,
    scope: str = Form("workspace"),
) -> HTMLResponse:
    from kairo.glossary import (
        load_glossary_file,
        remove_entry,
        root_glossary_path,
        save_glossary_file,
    )

    ws = _open(request, slug)
    try:
        if scope == "shared":
            path = root_glossary_path(Path(request.app.state.root))
            entries = remove_entry(load_glossary_file(path), index)
            save_glossary_file(path, entries)
        else:
            ws.remove_glossary_entry(index)
    except IndexError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return _glossary_fragment(request, ws, slug)


@router.post("/w/{slug}/corpus", response_class=HTMLResponse)
def add_corpus(request: Request, slug: str, path: str = Form(...)) -> HTMLResponse:
    ws = _open(request, slug)
    try:
        ws.add([Path(path)], source_class="corpus")
    except AddError as e:
        raise HTTPException(status_code=400, detail=str(e))
    headers = {"HX-Refresh": "true"}
    if request.app.state.registry.is_running(slug):
        headers["HX-Trigger"] = json.dumps(
            {"kairoToast": _t(request)("run.add_while_running")}
        )
    return HTMLResponse("", headers=headers)


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
    """兼容旧入口:有 target 时 re-step 文档/ref;无 target 时改走 run(含自动重试 blocked)。"""
    ws = _open(request, slug)
    reg = request.app.state.registry
    if reg.is_running(slug):
        return HTMLResponse(f'<p class="muted">{_t(request)("step.busy")}</p>')
    if target:
        argv = [sys.executable, "-m", "kairo", "re-step", target]
    else:
        # #75:无 target 的「推进」= run(自动清 blocked)
        plan = workspace_run_plan(ws)
        if plan["mode"] == "clean":
            return HTMLResponse(
                f'<p class="muted run-summary">{_t(request)("run.clean_msg")}</p>'
            )
        argv = [sys.executable, "-m", "kairo", "run"]
    task = reg.start(slug, ws.root, argv)
    return _render(request, "_step.html", {"slug": slug, "task_id": task.task_id})


@router.post("/w/{slug}/run", response_class=HTMLResponse)
def start_run(request: Request, slug: str) -> HTMLResponse:
    """#75 主按钮:推进工作区(有 blocked 则自动重试)。"""
    return start_step(request, slug, target=None)


@router.get("/w/{slug}/run-summary", response_class=HTMLResponse)
def run_summary(request: Request, slug: str) -> HTMLResponse:
    """运行结束后的结果条(替代静默整页蒸发进度)。"""
    ws = _open(request, slug)
    t = _t(request)
    plan = workspace_run_plan(ws)
    lines = [f'<p class="run-summary-title">{t("run.done")}</p>']
    if plan["blocked_count"]:
        lines.append(
            f'<p class="run-summary-fail">{t("run.still_blocked").format(n=plan["blocked_count"])}</p>'
        )
        lines.append('<ul class="ref-block-list">')
        for item in plan["blocked_refs"]:
            reasons = ", ".join(b["reason"] for b in item["blocks"])
            rid = item["ref_id"]
            lines.append(
                f"<li><code>{escape(rid)}</code> · {escape(reasons)}</li>"
            )
        lines.append("</ul>")
    else:
        lines.append(f'<p class="muted">{t("run.done_ok")}</p>')
    # 轻量刷新主按钮状态:提示用户可刷新;并给 OOB 替换 run 按钮区
    btn = _run_button_ctx(request, ws, slug)
    lines.append(
        '<div id="run-btn-wrap" hx-swap-oob="true">'
        + _run_button_html(slug, btn, t)
        + "</div>"
    )
    return HTMLResponse("".join(lines))


def _run_button_html(slug: str, btn: dict, t) -> str:
    disabled = " disabled" if btn["run_disabled"] else ""
    cls = "btn btn-ghost" if btn["run_mode"] in ("clean", "running") else "btn btn-step"
    if btn["run_disabled"]:
        return (
            f'<button type="button" class="{cls}"{disabled} '
            f'id="run-btn">{escape(btn["run_label"])}</button>'
        )
    return (
        f'<button type="button" class="{cls}" id="run-btn" '
        f'hx-post="/w/{quote(slug)}/run" hx-target="#step-area" '
        f'hx-swap="innerHTML">{escape(btn["run_label"])}</button>'
    )


@router.post("/w/{slug}/ref/{ref_id}/retry", response_class=HTMLResponse)
def retry_ref(request: Request, slug: str, ref_id: str) -> HTMLResponse:
    """#73:重新处理参考(清 blocked/派生产物后 step);走子进程 retry-ref。"""
    ws = _open(request, slug)
    if ref_id not in ws.list_reference_ids():
        raise HTTPException(status_code=404, detail="reference not found")
    reg = request.app.state.registry
    if reg.is_running(slug):
        return HTMLResponse(f'<p class="muted">{_t(request)("step.busy")}</p>')
    argv = [sys.executable, "-m", "kairo", "retry-ref", ref_id]
    task = reg.start(slug, ws.root, argv)
    return _render(request, "_step.html", {"slug": slug, "task_id": task.task_id})


@router.post("/w/{slug}/ref/{ref_id}/prose", response_class=HTMLResponse)
def start_prose(request: Request, slug: str, ref_id: str) -> HTMLResponse:
    """#60:单 ref 按需生成可读文稿;子进程 kairo prose,复用 step 任务区。"""
    ws = _open(request, slug)
    if ref_id not in ws.list_reference_ids():
        raise HTTPException(status_code=404, detail="reference not found")
    try:
        prose_precheck(ws, ref_id)
    except ProseError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    reg = request.app.state.registry
    if reg.is_running(slug):
        return HTMLResponse(f'<p class="muted">{_t(request)("step.busy")}</p>')
    argv = [sys.executable, "-m", "kairo", "prose", ref_id]
    task = reg.start(slug, ws.root, argv)
    return _render(request, "_step.html", {"slug": slug, "task_id": task.task_id})


@router.get("/w/{slug}/step/{task_id}/stream")
def step_stream(request: Request, slug: str, task_id: str) -> StreamingResponse:
    task = request.app.state.registry.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return StreamingResponse(stream_events(task), media_type="text/event-stream")


@router.post("/w/{slug}/step/{task_id}/cancel", response_class=HTMLResponse)
def cancel_step(request: Request, slug: str, task_id: str) -> HTMLResponse:
    ok = request.app.state.registry.cancel(task_id)
    t = _t(request)
    msg = t("step.canceled") if ok else t("step.cannot_cancel")
    return HTMLResponse(f'<p class="muted">{msg}</p>')
