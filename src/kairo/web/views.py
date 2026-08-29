"""web console 路由(APIRouter):dashboard / workspace / 产物预览 / 写操作 / step。"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
from html import escape
from pathlib import Path
from typing import Annotated
from urllib.parse import quote, urlencode

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
    delete_reference,
    prose_precheck,
    ref_product_blocks,
    workspace_run_plan,
)
from kairo.engine import accept as engine_accept
from kairo.listen_read import pair_audio_transcripts, parse_units
from kairo.timeline import (
    TimelineQueryError,
    effective_added_at,
    effective_occurred,
    is_fold_class,
    month_cells,
    parse_calendar_date,
    resolve_timeline_query,
    scan_timeline,
    shift_month_day,
)
from kairo.web.discovery import activity_label, scan_workspaces
from kairo.web.pins import read_pins, toggle_pin
from kairo.web.i18n import SUPPORTED, resolve_lang, translator
from kairo.web.render import render_markdown
from kairo.web.tasks import classify_task, stream_events
from kairo.workspace import (
    AddError,
    Workspace,
    WorkspaceNotFound,
    delete_workspace,
)

router = APIRouter()


def _t(request: Request):
    """请求语言 → translator t(key)。"""
    return translator(resolve_lang(request))


def _render(request: Request, name: str, ctx: dict) -> HTMLResponse:
    """统一渲染:注入 lang + t。所有 TemplateResponse 走这里。"""
    lang = resolve_lang(request)
    ctx = {"nav_active": "", **ctx, "lang": lang, "t": translator(lang)}
    return request.app.state.templates.TemplateResponse(request, name, ctx)


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


def _preview_html(ws: Workspace, location: str, slug: str | None = None) -> str | None:
    """把 workspace 内的 .md 渲染成 HTML;越界/缺失 → None(右栏给提示,不报错)。"""
    try:
        return render_markdown(_safe_doc(ws, location).read_text(), slug=slug)
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


def _open_local_path(path: Path) -> None:
    """用本机默认应用打开文件(本地 console;路径须已由 manifest 解析出)。"""
    path = path.resolve()
    if not path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)], start_new_session=True)
    elif sys.platform.startswith("linux"):
        subprocess.Popen(["xdg-open", str(path)], start_new_session=True)
    elif sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        raise HTTPException(status_code=501, detail="open not supported on this OS")


def _manifest_form_path(ws: Workspace, ref_id: str, key: str) -> Path:
    """从 manifest 解析 form 绝对路径(digest 或 forms[i]);key 非法 → 404。"""
    if key == "digest":
        p = (ws.references_dir() / ref_id / "digest.md").resolve()
        if not p.is_file():
            raise HTTPException(status_code=404, detail="form not found")
        return p
    man = ws.read_manifest(ref_id)
    try:
        idx = int(key)
    except ValueError:
        raise HTTPException(status_code=404, detail="form not found")
    if not 0 <= idx < len(man.forms):
        raise HTTPException(status_code=404, detail="form not found")
    return _form_path(ws, man.forms[idx].location).resolve()


def _render_doc(path: Path, *, slug: str | None = None) -> str:
    """.md → markdown;其余文本 → 保留换行的 <pre>(转义)。勿用于图片。"""
    text = path.read_text(errors="replace")
    if path.suffix.lower() in (".md", ".markdown"):
        return render_markdown(text, slug=slug)
    return f'<pre class="doc-plain">{escape(text)}</pre>'


def _render_transcript(path: Path, *, slug: str | None = None) -> str:
    """将带时间戳的原始 ASR 分段展示；无时间戳时保留原有 Markdown 呈现。"""
    units = parse_units(path.read_text(errors="replace"))
    if not any(unit.start is not None for unit in units):
        return _render_doc(path, slug=slug)
    parts = ['<div class="doc-transcript">']
    for unit in units:
        text = escape(unit.text)
        if unit.start is None:
            parts.append(f'<p class="transcript-untimed">{text}</p>')
        else:
            parts.append(
                f'<section class="transcript-unit"><time>{_clock(unit.start)}</time>'
                f'<p>{text}</p></section>'
            )
    return "".join(parts) + "</div>"


def _form_preview_html(ws: Workspace, slug: str, ref_id: str, form: dict) -> str | None:
    """按 form 类型生成预览 HTML:图片走 <img>,文本走 markdown/pre。"""
    path = _form_path(ws, form["location"])
    if _is_image_file(path):
        src = f"/w/{quote(slug)}/ref/{quote(ref_id)}/file/{quote(form['key'])}"
        return (
            f'<img class="doc-img" src="{src}" alt="{escape(path.name)}">'
        )
    if _is_text_file(path):
        return _render_transcript(path, slug=slug) if form["role"] == "transcript" else _render_doc(path, slug=slug)
    return None

def _clock(sec: float) -> str:
    m = int(sec // 60)
    s = sec - 60 * m
    if float(s).is_integer():
        return f"{m}:{int(s):02d}"
    return f"{m}:{s:04.1f}"


def _num_attr(n: float | None) -> str:
    if n is None:
        return ""
    return str(int(n)) if float(n).is_integer() else str(n)


def _form_index(man, form) -> str:
    for i, f in enumerate(man.forms):
        if f is form:
            return str(i)
    raise HTTPException(status_code=404, detail="form not found")


def _listen_read_html(request: Request, ws, slug: str, ref_id: str, man, audio_form) -> str:
    t = _t(request)
    pairs = pair_audio_transcripts(man.forms)
    pair = next((p for p in pairs if p.audio is audio_form), None)
    audio_key = _form_index(man, audio_form)
    audio_src = f"/w/{quote(slug)}/ref/{quote(ref_id)}/file/{quote(audio_key)}"
    units = []
    if pair and pair.linked and pair.transcript:
        path = _form_path(ws, pair.transcript.location)
        if path.is_file():
            parsed = parse_units(path.read_text(errors="replace"), duration=None)
            units = [
                {
                    "text": u.text,
                    "start_attr": _num_attr(u.start),
                    "end_attr": _num_attr(u.end),
                    "label": _clock(u.start) if u.start is not None else "",
                    "timed": u.start is not None,
                }
                for u in parsed
            ]
    switcher = []
    linked = [p for p in pairs if p.linked]
    if len(linked) > 1:
        switcher = [
            {
                "key": _form_index(man, p.audio),
                "label": Path(p.audio.location).name or _form_index(man, p.audio),
            }
            for p in linked
        ]
    lang = resolve_lang(request)
    return request.app.state.templates.get_template("_listen_read.html").render(
        {
            "slug": slug,
            "ref_id": ref_id,
            "audio_key": audio_key,
            "audio_src": audio_src,
            "units": units,
            "switcher": switcher,
            "t": t,
            "lang": lang,
        }
    )





def _target_states(ws: Workspace):
    """各 target 的 (path, status) —— 给左栏状态点。"""
    state = ws.read_state()
    out = []
    for t in ws.constitution.live_targets():
        ts = state.targets.get(t.path)
        status = ts.status if ts else "missing"
        out.append({"path": t.path, "status": status})
    return out


@router.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"ok": True})


def _dash_filter(raw: str | None) -> str:
    val = (raw or "").strip()
    return val if val in ("attention", "blocked") else ""


def _dash_groups(items, pins: list[str], q: str, filt: str):
    qn = (q or "").strip()
    needle = qn.casefold()

    def ok(s) -> bool:
        if needle and needle not in s.topic.casefold() and needle not in s.slug.casefold():
            return False
        if filt == "attention":
            return s.stale_count > 0 or s.blocked_count > 0
        if filt == "blocked":
            return s.blocked_count > 0
        return True

    matched = [s for s in items if ok(s)]
    by_slug = {s.slug: s for s in matched}
    pinned = [by_slug[p] for p in pins if p in by_slug]
    pinned_set = {s.slug for s in pinned}
    rest = [s for s in matched if s.slug not in pinned_set]
    rest.sort(key=lambda s: (-s.last_activity.timestamp(), s.slug))
    return pinned, rest, qn


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    q: str | None = None,
    filter: str | None = None,
) -> HTMLResponse:
    t = _t(request)
    root = request.app.state.root
    items = scan_workspaces(root)
    filt = _dash_filter(filter)
    pinned, rest, qn = _dash_groups(items, read_pins(root), q or "", filt)
    now = datetime.datetime.now().astimezone()
    for s in (*pinned, *rest):
        s.when = activity_label(
            s.last_activity,
            now=now,
            today=t("dash.today"),
            yesterday=t("dash.yesterday"),
        )
    match_n = len(pinned) + len(rest)
    return _render(
        request,
        "dashboard.html",
        {
            "root": str(root),
            "nav_active": "workspaces",
            "pinned": pinned,
            "rest": rest,
            "q": qn,
            "filter": filt,
            "total_n": len(items),
            "match_n": match_n,
            "attention_n": sum(1 for s in items if s.stale_count or s.blocked_count),
            "blocked_n": sum(1 for s in items if s.blocked_count),
            "show_sections": bool(pinned) and bool(rest),
            "has_query": bool(qn or filt),
        },
    )


@router.post("/workspaces/{slug}/pin")
def pin_workspace(
    request: Request,
    slug: str,
    q: str = Form(""),
    filter: str = Form(""),
) -> HTMLResponse:
    t = _t(request)
    root = Path(request.app.state.root)
    items = scan_workspaces(root)
    known = {s.slug for s in items}
    if slug not in known:
        raise HTTPException(status_code=404, detail=t("err.ws_not_found"))
    toggle_pin(root, slug, known)
    params: dict[str, str] = {}
    qn = (q or "").strip()
    filt = _dash_filter(filter)
    if qn:
        params["q"] = qn
    if filt:
        params["filter"] = filt
    dest = "/" if not params else "/?" + urlencode(params)
    if request.headers.get("hx-request"):
        return HTMLResponse("", headers={"HX-Redirect": dest})
    return RedirectResponse(dest, status_code=303)


@router.get("/timeline", response_class=HTMLResponse)
def timeline_view(
    request: Request,
    month: str | None = None,
    day: str | None = None,
    mode: str | None = None,
    unknown: str | None = None,
) -> HTMLResponse:
    t = _t(request)
    try:
        q = resolve_timeline_query(month=month, day=day, mode=mode, unknown=unknown)
    except TimelineQueryError:
        raise HTTPException(status_code=400, detail=t("tl.bad_query")) from None
    items = scan_timeline(request.app.state.root)
    unknown_items = [it for it in items if it.occurred_at is None]
    counts: dict[str, int] = {}
    for it in items:
        if it.occurred_at is not None:
            key = it.occurred_at.isoformat()
            counts[key] = counts.get(key, 0) + 1
    cells = []
    for d in month_cells(q.month.year, q.month.month):
        n = counts.get(d.isoformat(), 0)
        cells.append(
            {
                "date": d,
                "iso": d.isoformat(),
                "num": d.day,
                "mute": d.month != q.month.month,
                "today": d == datetime.date.today(),
                "on": q.view == "calendar" and d == q.day,
                "dots": min(n, 3),
            }
        )
    day_items = [it for it in items if it.occurred_at == q.day]
    recent_groups = []
    if q.view == "recent":
        ordered = sorted(items, key=lambda it: it.added_at, reverse=True)
        buckets: dict[str, list] = {}
        order: list[str] = []
        for it in ordered:
            key = it.added_at.astimezone().date().isoformat()
            if key not in buckets:
                buckets[key] = []
                order.append(key)
            buckets[key].append(it)
        recent_groups = [{"key": k, "entries": buckets[k]} for k in order]
    lang = resolve_lang(request)
    if lang == "zh":
        month_label = f"{q.month.year}年{q.month.month}月"
    else:
        month_label = q.month.strftime("%B %Y")
    prev_d = shift_month_day(q.day, -1)
    next_d = shift_month_day(q.day, 1)
    return _render(
        request,
        "timeline.html",
        {
            "nav_active": "timeline",
            "view": q.view,
            "q": q,
            "month_label": month_label,
            "weekdays": t("tl.weekday").split(),
            "cells": cells,
            "day_items": day_items,
            "unknown_items": unknown_items,
            "unknown_n": len(unknown_items),
            "recent_groups": recent_groups,
            "prev_day": prev_d.isoformat(),
            "next_day": next_d.isoformat(),
            "day_iso": q.day.isoformat(),
            "month_iso": q.month.strftime("%Y-%m"),
        },
    )


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


@router.post("/workspaces/{slug}/delete", response_class=HTMLResponse)
def delete_workspace_view(
    request: Request,
    slug: str,
    confirm_name: str = Form(""),
) -> HTMLResponse:
    """#78:dashboard 删除 workspace;须键入 slug 全等确认。"""
    t = _t(request)
    if confirm_name.strip() != slug:
        raise HTTPException(status_code=400, detail=t("err.ws_confirm_mismatch"))
    if request.app.state.registry.is_running(slug):
        raise HTTPException(status_code=409, detail=t("err.ws_busy"))
    try:
        delete_workspace(request.app.state.root, slug)
    except WorkspaceNotFound:
        raise HTTPException(status_code=404, detail=t("err.ws_not_found")) from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return HTMLResponse("", headers={"HX-Redirect": "/"})


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
        "retry": t("run.retry").format(n=plan["retryable_blocked_count"]),
        "run_and_retry": t("run.run_and_retry").format(
            n=plan["retryable_blocked_count"]
        ),
        "attention": t("run.attention"),
    }
    return {
        "run_mode": mode,
        "run_label": labels[mode],
        "run_disabled": mode in ("clean", "attention"),
        "run_pending": plan["pending_count"],
        "run_blocked": plan["blocked_count"],
    }


@router.get("/w/{slug}", response_class=HTMLResponse)
def workspace_view(request: Request, slug: str, ref: str | None = None) -> HTMLResponse:
    ws = _open(request, slug)
    streams, corpus = _split_refs(ws)
    running = request.app.state.registry.current(slug)
    ids = {r["id"] for r in streams} | {r["id"] for r in corpus}
    select_ref = ref if ref in ids else None
    return _render(
        request,
        "workspace.html",
        {
            "slug": slug,
            "topic": ws.constitution.topic,
            "targets": _target_states(ws),
            "streams": streams,
            "corpus": corpus,
            "select_ref": select_ref,
            "run_task_id": running.task_id if running else None,
            **(
                _step_template_vars(request, ws, slug, running)
                if running is not None
                else {}
            ),
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
        {
            "title": path,
            "html": render_markdown(target.read_text(), slug=slug),
            "exportable": exportable,
        },
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
        dig = ws.references_dir() / ref_id / "digest.md"
        forms.append(
            {
                "role": "digest",
                "role_label": _role_label("digest", t),
                "location": f"references/{ref_id}/digest.md",
                "filename": dig.name,
                "ext": dig.suffix.lstrip(".").upper() or "MD",
                "previewable": True,
                "openable": False,
                "key": "digest",
            }
        )
    for i, f in enumerate(man.forms):
        p = _form_path(ws, f.location)
        previewable = (
            _is_text_file(p)
            or _is_image_file(p)
            or (f.role == "audio" and p.is_file())
        )
        name = p.name if p.name else f.location
        forms.append(
            {
                "role": f.role,
                "role_label": _role_label(f.role, t),
                "location": f.location,
                "filename": name,
                "ext": (p.suffix.lstrip(".").upper() if p.suffix else "FILE"),
                "previewable": previewable,
                "openable": (not previewable) and p.is_file(),
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
        or next((f for f in forms if f["previewable"] and f["role"] != "audio"), None)
    )
    sc = ws.constitution.source_classes.get(man.source_class)
    preview_title = f"{man.title} · {primary['role_label']}" if primary else ""
    preview_html = (
        _form_preview_html(ws, slug, ref_id, primary) if primary else None
    )
    open_form = next((f for f in forms if f.get("openable")), None) if primary is None else None
    is_corpus = sc is not None and not sc.fold
    # #88:无可预览时基线 document → 空态卡片;目录树等仍通用 empty_hint
    if primary is None and is_corpus and any(f["role"] == "document" for f in forms):
        empty_hint = t("ref.empty_hint_corpus")
        empty_card = True
    else:
        empty_hint = t("ref.empty_hint")
        empty_card = False
    occ, occ_src = effective_occurred(ref_id, man.occurred_at)
    man_path = ws.references_dir() / ref_id / "manifest.yaml"
    added_dt = effective_added_at(man.added_at, man_path)
    can_edit_time = is_fold_class(ws, man.source_class)
    blocks = ref_product_blocks(ws, ref_id)
    # 基线干净指针无 blocked 时隐藏「重新处理」(避免假故障感);stream 或 blocked 仍显示
    show_retry = bool(blocks) or not is_corpus
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
            "empty_hint": empty_hint,
            "empty_card": empty_card,
            "open_form": open_form,
            "can_generate_prose": can_generate_prose(ws, ref_id),
            "blocks": blocks,
            "show_retry": show_retry,
            "can_edit_time": can_edit_time,
            "occurred_iso": occ.isoformat() if occ else "",
            "occurred_src": occ_src,
            "added_display": added_dt.astimezone().strftime("%Y-%m-%d %H:%M"),
        },
    )


@router.post("/w/{slug}/ref/{ref_id}/occurred", response_class=HTMLResponse)
def ref_occurred_view(
    request: Request,
    slug: str,
    ref_id: str,
    occurred_at: str = Form(""),
) -> HTMLResponse:
    ws = _open(request, slug)
    t = _t(request)
    if ref_id not in ws.list_reference_ids():
        raise HTTPException(status_code=404, detail="reference not found")
    raw = (occurred_at or "").strip()
    try:
        if not raw:
            ws.set_occurred(ref_id, None)
        else:
            parsed = parse_calendar_date(raw)
            if parsed is None:
                raise HTTPException(status_code=400, detail=t("tl.bad_date"))
            ws.set_occurred(ref_id, parsed)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ref_view(request, slug, ref_id)


@router.get("/w/{slug}/ref/{ref_id}/form/{key}", response_class=HTMLResponse)
def ref_form_view(request: Request, slug: str, ref_id: str, key: str) -> HTMLResponse:
    """预览某 form 正文。路径由服务端从 manifest 解析(可信),客户端只给受校验的 ref_id + key。"""
    ws = _open(request, slug)
    if ref_id not in ws.list_reference_ids():
        raise HTTPException(status_code=404, detail="reference not found")
    man = ws.read_manifest(ref_id)
    if key == "digest":
        path, role = ws.references_dir() / ref_id / "digest.md", "digest"
        form = None
    else:
        try:
            idx = int(key)
        except ValueError:
            raise HTTPException(status_code=404, detail="form not found")
        if not 0 <= idx < len(man.forms):
            raise HTTPException(status_code=404, detail="form not found")
        form = man.forms[idx]
        path, role = _form_path(ws, form.location), form.role
    t = _t(request)
    title = f"{man.title} · {_role_label(role, t)}"
    if role == "audio" and form is not None and path.is_file():
        return _render(
            request,
            "_doc.html",
            {"title": title, "html": _listen_read_html(request, ws, slug, ref_id, man, form)},
        )
    if _is_image_file(path):
        img = (
            f'<img class="doc-img" src="/w/{quote(slug)}/ref/{ref_id}/file/{quote(key)}"'
            f' alt="{escape(path.name)}">'
        )
        return _render(request, "_doc.html", {"title": title, "html": img})
    if not _is_text_file(path):
        raise HTTPException(status_code=404, detail="not previewable")
    return _render(
        request,
        "_doc.html",
        {
            "title": title,
            "html": _render_transcript(path, slug=slug) if role == "transcript" else _render_doc(path, slug=slug),
            "exportable": key == "digest",
        },
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


@router.post("/w/{slug}/ref/{ref_id}/form/{key}/open")
def ref_form_open(request: Request, slug: str, ref_id: str, key: str) -> JSONResponse:
    """#88:用系统默认应用打开 form 路径(本地 console;路径仅来自 manifest)。"""
    ws = _open(request, slug)
    if ref_id not in ws.list_reference_ids():
        raise HTTPException(status_code=404, detail="reference not found")
    path = _manifest_form_path(ws, ref_id, key)
    _open_local_path(path)
    return JSONResponse({"ok": True, "path": str(path)})


@router.get("/w/{slug}/target", response_class=HTMLResponse)
def target_view(request: Request, slug: str, path: str) -> HTMLResponse:
    """右栏产物元信息(状态/blocked 原因) + (OOB)中间预览正文。"""
    ws = _open(request, slug)
    if path not in {t.path for t in ws.constitution.targets}:
        raise HTTPException(status_code=404, detail="target not found")
    ts = ws.read_state().targets.get(path)
    status = ts.status if ts else "missing"
    has_doc = (ws.root / path).is_file()
    diag = ts.diagnostic if ts else None
    return _render(
        request,
        "_target_meta.html",
        {
            "slug": slug,
            "path": path,
            "status": status,
            "reason": ts.reason if ts else None,
            "diagnostic_summary": diag.summary if diag else None,
            "diagnostic_stage": diag.stage if diag else None,
            "diagnostic_provider": diag.provider if diag else None,
            "has_doc": has_doc,
            "can_regen": has_doc or (ts is not None and ts.status == "blocked"),
            "regen_confirm": _t(request)(
                "target.regen_confirm" if has_doc else "target.regen_confirm_empty"
            ),
            "preview_title": path,
            "preview_html": _preview_html(ws, path, slug) if has_doc else None,
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


def _glossary_fragment(
    request: Request,
    ws: Workspace,
    slug: str,
    *,
    error: str | None = None,
    error_scope: str | None = None,
    form: dict | None = None,
) -> HTMLResponse:
    from kairo.glossary import (
        GlossaryError,
        machine_migration_hint,
        workspace_effective,
    )

    t = _t(request)
    load_failed = False
    effective = []
    local = []
    pending: list = []
    candidates: list = []
    extract_errors: dict = {}
    eff_hash = ""
    try:
        items = workspace_effective(ws.root, serve_root=Path(request.app.state.root))
        effective = [
            {
                "name": i.entry.name,
                "note": i.entry.note,
                "aka": ", ".join(i.entry.aka),
                "tags": ", ".join(i.entry.tags),
                "origin": i.origin,
            }
            for i in items
        ]
        local = _entry_rows([i.entry for i in items if i.origin != "inherited"])
        from kairo.glossary import load_workspace_glossary

        local = _entry_rows(load_workspace_glossary(ws.root))
        from kairo.workspace import restep_target_for

        pending = [
            {"key": k, "target": restep_target_for(k)}
            for k in ws.glossary_pending(serve_root=Path(request.app.state.root))
        ]
        from kairo.glossary_review import load_review, open_candidates

        review = load_review(ws.root)
        candidates = [c.model_dump() for c in open_candidates(ws.root)]
        for candidate in candidates:
            candidate["status_label"] = t(
                f"glossary.status_{candidate['status']}"
            )
        extract_errors = review.extract_errors
    except GlossaryError as e:
        load_failed = True
        error = error or str(e)
    form = form or {}
    origin_label = {
        "inherited": t("glossary.origin_inherited"),
        "local": t("glossary.origin_local"),
        "override": t("glossary.origin_override"),
    }
    for row in effective:
        row["origin_label"] = origin_label.get(row["origin"], row["origin"])
    return _render(
        request,
        "_glossary.html",
        {
            "slug": slug,
            "effective_entries": effective,
            "local_entries": local,
            "local_count": len(local),
            "effective_count": len(effective),
            "machine_hint": machine_migration_hint(),
            "hint": t("glossary.restep_hint"),
            "error": error,
            "error_scope": error_scope,
            "load_failed": load_failed,
            "form_name": form.get("name", ""),
            "form_note": form.get("note", ""),
            "form_aka": form.get("aka", ""),
            "form_tags": form.get("tags", ""),
            "pending": pending,
            "eff_hash": eff_hash,
            "candidates": candidates if not load_failed else [],
            "extract_errors": extract_errors if not load_failed else {},
        },
    )


@router.get("/w/{slug}/glossary", response_class=HTMLResponse)
def glossary_view(request: Request, slug: str) -> HTMLResponse:
    """#163:右栏真名册(生效视图 + 本 workspace)。"""
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
    """只写 workspace 层;shared 请走 Root /glossary。"""
    from kairo.glossary import GlossaryError, parse_scope

    ws = _open(request, slug)
    parts = _parse_aka(aka)
    tag_parts = _parse_tags(tags)
    form = {"name": name, "note": note, "aka": aka, "tags": tags}
    try:
        chosen = parse_scope(scope)
        if chosen != "workspace":
            raise GlossaryError("workspace 真名册只能写本层;公共条目请到 Root 首页")
        ws.add_glossary_entry(
            name,
            note=note,
            aka=parts,
            tags=tag_parts,
            serve_root=Path(request.app.state.root),
        )
    except (GlossaryError, ValueError) as e:
        return _glossary_fragment(
            request, ws, slug, error=str(e), error_scope=scope.strip() or None, form=form
        )
    return _glossary_fragment(request, ws, slug)


@router.post("/w/{slug}/glossary/{index}/delete", response_class=HTMLResponse)
def glossary_delete(
    request: Request,
    slug: str,
    index: int,
    scope: str = Form("workspace"),
) -> HTMLResponse:
    from kairo.glossary import GlossaryError, parse_scope

    ws = _open(request, slug)
    try:
        chosen = parse_scope(scope)
        if chosen != "workspace":
            raise GlossaryError("workspace 真名册只能写本层;公共条目请到 Root 首页")
        ws.remove_glossary_entry(index, serve_root=Path(request.app.state.root))
    except (GlossaryError, ValueError, IndexError) as e:
        return _glossary_fragment(
            request, ws, slug, error=str(e), error_scope=scope.strip() or None
        )
    return _glossary_fragment(request, ws, slug)


@router.post("/w/{slug}/glossary/candidates/{cid}/accept", response_class=HTMLResponse)
def glossary_candidate_accept(request: Request, slug: str, cid: str) -> HTMLResponse:
    from kairo.glossary import GlossaryError
    from kairo.glossary_review import accept_workspace

    ws = _open(request, slug)
    try:
        accept_workspace(ws, cid)
    except (GlossaryError, ValueError) as e:
        return _glossary_fragment(request, ws, slug, error=str(e))
    return _glossary_fragment(request, ws, slug)


@router.post("/w/{slug}/glossary/candidates/{cid}/merge", response_class=HTMLResponse)
def glossary_candidate_merge(
    request: Request, slug: str, cid: str, existing_name: str = Form(...)
) -> HTMLResponse:
    from kairo.glossary import GlossaryError
    from kairo.glossary_review import merge_workspace

    ws = _open(request, slug)
    try:
        merge_workspace(ws, cid, existing_name)
    except (GlossaryError, ValueError) as e:
        return _glossary_fragment(request, ws, slug, error=str(e))
    return _glossary_fragment(request, ws, slug)


@router.post("/w/{slug}/glossary/candidates/{cid}/ignore", response_class=HTMLResponse)
def glossary_candidate_ignore(request: Request, slug: str, cid: str) -> HTMLResponse:
    from kairo.glossary import GlossaryError
    from kairo.glossary_review import ignore_candidate

    ws = _open(request, slug)
    try:
        ignore_candidate(ws.root, cid)
    except GlossaryError as e:
        return _glossary_fragment(request, ws, slug, error=str(e))
    return _glossary_fragment(request, ws, slug)


@router.post("/w/{slug}/glossary/candidates/{cid}/promote", response_class=HTMLResponse)
def glossary_candidate_promote(request: Request, slug: str, cid: str) -> HTMLResponse:
    from kairo.glossary import GlossaryError
    from kairo.glossary_review import promote_candidate

    ws = _open(request, slug)
    try:
        promote_candidate(ws.root, cid)
    except GlossaryError as e:
        return _glossary_fragment(request, ws, slug, error=str(e))
    return _glossary_fragment(request, ws, slug)


@router.post("/w/{slug}/ref/{ref_id}/glossary-extract", response_class=HTMLResponse)
def glossary_extract_retry(request: Request, slug: str, ref_id: str) -> HTMLResponse:
    from kairo.glossary import GlossaryError
    from kairo.glossary_review import extract_after_digest
    from kairo.provider import select_provider

    ws = _open(request, slug)
    path = ws.root / "references" / ref_id / "digest.md"
    try:
        if not path.is_file():
            raise GlossaryError(f"digest 不存在:{ref_id}")
        extract_after_digest(ws, ref_id, path.read_text(), provider=select_provider())
    except GlossaryError as e:
        return _glossary_fragment(request, ws, slug, error=str(e))
    return _glossary_fragment(request, ws, slug)


def _root_glossary_page(
    request: Request,
    *,
    error: str | None = None,
    form: dict | None = None,
    success: bool = False,
) -> HTMLResponse:
    from kairo.glossary import (
        GlossaryError,
        load_glossary_file,
        load_workspace_glossary,
        machine_migration_hint,
        root_glossary_path,
    )
    from kairo.web.discovery import scan_workspaces

    serve = Path(request.app.state.root)
    form = form or {}
    load_failed = False
    entries = []
    impact = []
    promotions = []
    try:
        entries = load_glossary_file(root_glossary_path(serve))
        names = {e.name for e in entries}
        cand = (form.get("name") or "").strip()
        if cand:
            names.add(cand)
        for s in scan_workspaces(serve):
            local = load_workspace_glossary(serve / s.slug)
            local_names = [e.name for e in local]
            ov = [n for n in local_names if n in names]
            impact.append(
                {
                    "slug": s.slug,
                    "overrides": ov,
                    "override_n": len(ov),
                    "local_names": local_names,
                }
            )
        from kairo.glossary_review import STATUS_PENDING_ROOT, load_review

        promotions = []
        for s in scan_workspaces(serve):
            for c in load_review(serve / s.slug).candidates:
                if c.status == STATUS_PENDING_ROOT:
                    row = c.model_dump()
                    row["slug"] = s.slug
                    promotions.append(row)
    except GlossaryError as e:
        load_failed = True
        error = error or str(e)
    return _render(
        request,
        "root_glossary.html",
        {
            "entries": _entry_rows(entries),
            "count": len(entries),
            "impact": impact,
            "ws_n": len(impact),
            "override_ws_n": sum(1 for i in impact if i["override_n"]),
            "machine_hint": machine_migration_hint(),
            "error": error,
            "success": success,
            "load_failed": load_failed,
            "form_name": form.get("name", ""),
            "form_note": form.get("note", ""),
            "form_aka": form.get("aka", ""),
            "form_tags": form.get("tags", ""),
            "promotions": promotions,
        },
    )


@router.get("/glossary", response_class=HTMLResponse)
def root_glossary_view(request: Request) -> HTMLResponse:
    """#163:Root 首页公共真名册。"""
    return _root_glossary_page(request)


@router.post("/glossary", response_class=HTMLResponse)
def root_glossary_add(
    request: Request,
    name: str = Form(...),
    note: str = Form(""),
    aka: str = Form(""),
    tags: str = Form(""),
) -> HTMLResponse:
    from kairo.workspace import stamp_serve_workspaces as _stamp_serve_workspaces
    from kairo.glossary import (
        GlossaryError,
        add_entry,
        load_glossary_file,
        root_glossary_path,
        save_glossary_file,
        validate_entries,
    )

    serve = Path(request.app.state.root)
    form = {"name": name, "note": note, "aka": aka, "tags": tags}
    try:
        path = root_glossary_path(serve)
        entries = add_entry(
            load_glossary_file(path),
            name,
            note=note,
            aka=_parse_aka(aka),
            tags=_parse_tags(tags),
        )
        validate_entries(entries, path=path)
        save_glossary_file(path, entries)
        _stamp_serve_workspaces(serve)
    except (GlossaryError, ValueError) as e:
        return _root_glossary_page(request, error=str(e), form=form)
    return _root_glossary_page(request, success=True)


@router.post("/glossary/{index}/delete", response_class=HTMLResponse)
def root_glossary_delete(request: Request, index: int) -> HTMLResponse:
    from kairo.workspace import stamp_serve_workspaces as _stamp_serve_workspaces
    from kairo.glossary import (
        GlossaryError,
        load_glossary_file,
        remove_entry,
        root_glossary_path,
        save_glossary_file,
        validate_entries,
    )

    serve = Path(request.app.state.root)
    try:
        path = root_glossary_path(serve)
        nxt = remove_entry(load_glossary_file(path), index)
        validate_entries(nxt, path=path)
        save_glossary_file(path, nxt)
        _stamp_serve_workspaces(serve)
    except (GlossaryError, ValueError, IndexError) as e:
        return _root_glossary_page(request, error=str(e))
    return _root_glossary_page(request, success=True)


@router.post("/glossary/candidates/{slug}/{cid}/accept", response_class=HTMLResponse)
def root_candidate_accept(request: Request, slug: str, cid: str) -> HTMLResponse:
    from kairo.glossary import GlossaryError
    from kairo.glossary_review import accept_root

    try:
        accept_root(Path(request.app.state.root), slug, cid)
    except (GlossaryError, ValueError) as e:
        return _root_glossary_page(request, error=str(e))
    return _root_glossary_page(request, success=True)


@router.post("/glossary/candidates/{slug}/{cid}/merge", response_class=HTMLResponse)
def root_candidate_merge(
    request: Request, slug: str, cid: str, existing_name: str = Form(...)
) -> HTMLResponse:
    from kairo.glossary import GlossaryError
    from kairo.glossary_review import merge_root

    try:
        merge_root(Path(request.app.state.root), slug, cid, existing_name)
    except (GlossaryError, ValueError) as e:
        return _root_glossary_page(request, error=str(e))
    return _root_glossary_page(request, success=True)


@router.post("/glossary/candidates/{slug}/{cid}/reject", response_class=HTMLResponse)
def root_candidate_reject(
    request: Request, slug: str, cid: str, reason: str = Form("")
) -> HTMLResponse:
    from kairo.glossary import GlossaryError
    from kairo.glossary_review import reject_root

    try:
        reject_root(Path(request.app.state.root) / slug, cid, reason)
    except GlossaryError as e:
        return _root_glossary_page(request, error=str(e))
    return _root_glossary_page(request, success=True)


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


def _title_for_work_item(ws: Workspace, item) -> str:
    key = getattr(item, "key", "") or ""
    parts = key.replace("\\", "/").split("/")
    if len(parts) >= 2 and parts[0] == "references":
        ref_id = parts[1]
        try:
            title = ws.read_manifest(ref_id).title
            if title:
                return title
        except Exception:
            pass
        return ref_id
    return key.rsplit("/", 1)[-1] or key


def _step_template_vars(request: Request, ws: Workspace, slug: str, task) -> dict:
    """#157:首屏即人话进度(+已锁存则健康),不等 SSE。"""
    from kairo.engine import pending
    from kairo.web.tasks import render_health_html, render_progress_html

    t = _t(request)

    def _pending():
        return pending(ws)

    progress_html = render_progress_html(
        task,
        t,
        pending_fn=_pending if task.job_kind == "reconcile" else None,
        title_fn=lambda item: _title_for_work_item(ws, item),
    )
    health_html = render_health_html(t) if task.transport_seen else ""
    return {
        "slug": slug,
        "task_id": task.task_id,
        "progress_html": progress_html,
        "health_html": health_html,
    }


def _step_response(
    request: Request, ws: Workspace, slug: str, task
) -> HTMLResponse:
    """#114:运行视图 + OOB 主按钮 disabled Running…(start 与 attach 共用)。"""
    step = _render(
        request, "_step.html", _step_template_vars(request, ws, slug, task)
    ).body.decode()
    t = _t(request)
    btn = _run_button_ctx(request, ws, slug)
    oob = (
        f'<div id="run-btn-wrap" hx-swap-oob="true">'
        f"{_run_button_html(slug, btn, t)}</div>"
    )
    return HTMLResponse(step + oob)


@router.post("/w/{slug}/step", response_class=HTMLResponse)
def start_step(request: Request, slug: str, target: str = Form(None)) -> HTMLResponse:
    """兼容旧入口:有 target 时 re-step 文档/ref;无 target 时改走 run(含自动重试 blocked)。

    #114:已有运行中任务则附着同一 task_id,不新开 job;响应 OOB 刷新主按钮。
    """
    ws = _open(request, slug)
    reg = request.app.state.registry
    existing = reg.current(slug)
    if existing is not None:
        # target 与当前 job 类型可能不同,仍附着——串行锁下不能并行第二 job
        return _step_response(request, ws, slug, existing)
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
    try:
        task = reg.start(slug, ws.root, argv, job_kind="reconcile")
    except RuntimeError:
        # 竞态:判断 is_running 与 start 之间被抢占 → 附着
        task = reg.current(slug)
        if task is None:
            raise
    return _step_response(request, ws, slug, task)



@router.post("/w/{slug}/run", response_class=HTMLResponse)
def start_run(request: Request, slug: str) -> HTMLResponse:
    """#75 主按钮:推进工作区(有 blocked 则自动重试)。"""
    return start_step(request, slug, target=None)


@router.get("/w/{slug}/run-summary", response_class=HTMLResponse)
def run_summary(request: Request, slug: str, task_id: str | None = None) -> HTMLResponse:
    """运行结束后的结果条。#97:任务终态优先于 workspace plan;失败/取消不渲染成功结论。"""
    ws = _open(request, slug)
    t = _t(request)
    reg = request.app.state.registry
    task = reg.get(task_id) if task_id else None
    result = classify_task(task if task_id else None)
    # 无 task_id 或任务已回收 → missing;未结束 → running。二者均非成功。
    if result.kind in ("missing", "running"):
        title_key = "run.task_missing" if result.kind == "missing" else "run.task_pending"
        lines = [
            f'<p class="run-summary-title run-summary-warn">{t(title_key)}</p>',
            f'<p class="muted">{t("run.task_missing_hint")}</p>',
        ]
    elif result.kind == "failed":
        lines = [
            f'<p class="run-summary-title run-summary-error">{t("run.failed")}</p>',
        ]
        if result.exit_code is not None:
            lines.append(
                f'<p class="run-summary-meta">{t("run.exit_code").format(code=result.exit_code)}</p>'
            )
        summary = result.message or t("run.failed_no_detail")
        lines.append(
            f'<p class="run-summary-error-detail">{escape(summary)}</p>'
        )
        lines.append(f'<p class="muted">{t("run.failed_retry_hint")}</p>')
    elif result.kind == "cancelled":
        lines = [
            f'<p class="run-summary-title run-summary-cancel">{t("run.cancelled")}</p>',
            f'<p class="muted">{t("run.cancelled_hint")}</p>',
        ]
    else:
        # succeeded: 才用 plan 描述收敛结果
        plan = workspace_run_plan(ws)
        lines = [f'<p class="run-summary-title">{t("run.done")}</p>']
        if plan["blocked_count"]:
            lines.append(
                f'<p class="run-summary-fail">{t("run.still_blocked").format(n=plan["blocked_count"])}</p>'
            )
            lines.append('<ul class="ref-block-list">')
            for item in plan["blocked_refs"]:
                parts = []
                for b in item["blocks"]:
                    bit = b["reason"]
                    if b.get("stage"):
                        bit = f"{bit} (stage={b['stage']})"
                    if b.get("provider"):
                        bit = f"{bit} (provider={b['provider']})"
                    if b.get("summary"):
                        bit = f"{bit} — {b['summary']}"
                    parts.append(bit)
                reasons = ", ".join(parts)
                rid = item["ref_id"]
                lines.append(
                    f"<li><code>{escape(rid)}</code> · {escape(reasons)}</li>"
                )
            for item in plan.get("blocked_targets") or []:
                bit = item.get("reason") or "blocked"
                if item.get("stage"):
                    bit = f"{bit} (stage={item['stage']})"
                if item.get("provider"):
                    bit = f"{bit} (provider={item['provider']})"
                if item.get("summary"):
                    bit = f"{bit} — {item['summary']}"
                lines.append(
                    f"<li><code>{escape(item['path'])}</code> · {escape(bit)}</li>"
                )
            lines.append("</ul>")
        else:
            lines.append(f'<p class="muted">{t("run.done_ok")}</p>')
    # 任务结束后释放运行锁(is_running 看 done);OOB 刷新 Run 按钮以便再次发起
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
        button = (
            f'<button type="button" class="{cls}"{disabled} '
            f'id="run-btn">{escape(btn["run_label"])}</button>'
        )
    else:
        button = (
            f'<button type="button" class="{cls}" id="run-btn" '
            f'hx-post="/w/{quote(slug)}/run" hx-target="#step-area" '
            f'hx-swap="innerHTML">{escape(btn["run_label"])}</button>'
        )
    if btn["run_blocked"]:
        button += (
            '<span class="run-blocked-total">'
            + escape(t("run.blocked_total").format(n=btn["run_blocked"]))
            + "</span>"
        )
    return button


@router.post("/w/{slug}/ref/{ref_id}/retry", response_class=HTMLResponse)
def retry_ref(request: Request, slug: str, ref_id: str) -> HTMLResponse:
    """#73:重新处理参考(清 blocked/派生产物后 step);走子进程 retry-ref。

    #114:运行中则附着当前 task。
    """
    ws = _open(request, slug)
    if ref_id not in ws.list_reference_ids():
        raise HTTPException(status_code=404, detail="reference not found")
    reg = request.app.state.registry
    existing = reg.current(slug)
    if existing is not None:
        return _step_response(request, ws, slug, existing)
    argv = [sys.executable, "-m", "kairo", "retry-ref", ref_id]
    try:
        task = reg.start(slug, ws.root, argv, job_kind="reconcile")
    except RuntimeError:
        task = reg.current(slug)
        if task is None:
            raise
    return _step_response(request, ws, slug, task)




@router.post("/w/{slug}/ref/{ref_id}/delete", response_class=HTMLResponse)
def delete_ref(
    request: Request,
    slug: str,
    ref_id: str,
    recompose: str = Form("0"),
) -> HTMLResponse:
    """#77:删除参考。默认保留产物正文;可选立即整篇 re-step。"""
    ws = _open(request, slug)
    if ref_id not in ws.list_reference_ids():
        raise HTTPException(status_code=404, detail="reference not found")
    reg = request.app.state.registry
    want_recompose = recompose in ("1", "true", "on", "yes")
    if want_recompose and reg.is_running(slug):
        raise HTTPException(status_code=409, detail=_t(request)("step.busy"))
    try:
        delete_reference(ws, ref_id, recompose=False)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if want_recompose:
        argv = [sys.executable, "-m", "kairo", "re-step"]
        task = reg.start(slug, ws.root, argv, job_kind="reconcile")
        # 进度进 step-area;列表/元信息/阅读区 OOB 清掉已删参考
        step = _render(
            request, "_step.html", _step_template_vars(request, ws, slug, task)
        ).body.decode()
        streams, _corpus = _split_refs(ws)
        refs = _render(
            request, "_refs_list.html", {"slug": slug, "refs": streams}
        ).body.decode()
        t = _t(request)
        btn = _run_button_ctx(request, ws, slug)
        oob = (
            f'<div id="refs-list" hx-swap-oob="true">{refs}</div>'
            f'<div id="meta" hx-swap-oob="true">'
            f'<p class="panel-hint">{escape(t("panel.hint"))}</p></div>'
            f'<main id="reader" class="pane-read" hx-swap-oob="true">'
            f'<p class="reader-empty">{escape(t("reader.empty"))}</p></main>'
            f'<div id="run-btn-wrap" hx-swap-oob="true">'
            f"{_run_button_html(slug, btn, t)}</div>"
        )
        return HTMLResponse(step + oob)
    return HTMLResponse("", headers={"HX-Redirect": "/w/" + quote(slug)})


@router.post("/w/{slug}/ref/{ref_id}/prose", response_class=HTMLResponse)
def start_prose(request: Request, slug: str, ref_id: str) -> HTMLResponse:
    """#60:单 ref 按需生成可读文稿;子进程 kairo prose,复用 step 任务区。

    #114:运行中则附着当前 task。
    """
    ws = _open(request, slug)
    if ref_id not in ws.list_reference_ids():
        raise HTTPException(status_code=404, detail="reference not found")
    try:
        prose_precheck(ws, ref_id)
    except ProseError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    reg = request.app.state.registry
    existing = reg.current(slug)
    if existing is not None:
        return _step_response(request, ws, slug, existing)
    argv = [sys.executable, "-m", "kairo", "prose", ref_id]
    try:
        title = ws.read_manifest(ref_id).title or ref_id
        task = reg.start(
            slug, ws.root, argv, job_kind="prose", object_title=title
        )
    except RuntimeError:
        task = reg.current(slug)
        if task is None:
            raise
    return _step_response(request, ws, slug, task)




@router.get("/w/{slug}/step/{task_id}/stream")
def step_stream(request: Request, slug: str, task_id: str) -> StreamingResponse:
    task = request.app.state.registry.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    ws = _open(request, slug)
    t = _t(request)
    from kairo.engine import pending

    def _pending():
        return pending(ws)

    return StreamingResponse(
        stream_events(
            task,
            t=t,
            pending_fn=_pending if task.job_kind == "reconcile" else None,
            title_fn=lambda item: _title_for_work_item(ws, item),
        ),
        media_type="text/event-stream",
    )


@router.post("/w/{slug}/step/{task_id}/cancel", response_class=HTMLResponse)
def cancel_step(request: Request, slug: str, task_id: str) -> HTMLResponse:
    ok = request.app.state.registry.cancel(task_id)
    t = _t(request)
    msg = t("step.canceled") if ok else t("step.cannot_cancel")
    return HTMLResponse(f'<p class="muted">{msg}</p>')
