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

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
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
from kairo.review import (
    ReviewError,
    collect_digests,
    generate_review_body,
    is_journal_item,
    occupied_span,
    prepare_range,
    resolve_review_workspace,
    write_review_reference,
)
from kairo.timeline import (
    MAX_RANGE_DAYS,
    TimelineQueryError,
    cell_href,
    effective_added_at,
    effective_occurred,
    filter_range,
    format_range_label,
    is_fold_class,
    month_cells,
    parse_calendar_date,
    range_day_count,
    resolve_timeline_query,
    scan_timeline,
    shift_month_day,
    week_bounds,
)
from kairo.web.discovery import activity_label, scan_workspaces
from kairo.web.pins import read_pins, toggle_pin
from kairo.web.i18n import SUPPORTED, resolve_lang, translator
from kairo.web.public import (
    PublicationWriteError,
    public_bounds,
    set_reference_public,
)
from kairo.web.render import render_markdown
from kairo.web.tasks import classify_task, stream_events
from kairo.models import State
from kairo.rules import effective_compose_block_reason
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


def _is_public_read(request: Request) -> bool:
    return bool(getattr(request.app.state, "public_read", False))


def _public_bounds(
    request: Request,
) -> tuple[set[str], set[tuple[str, str]], set[tuple[str, str]]] | None:
    return public_bounds(Path(request.app.state.root))


def _deny_unpublished() -> None:
    raise HTTPException(status_code=404)


def _require_public_ref(request: Request, slug: str, ref_id: str) -> None:
    if not _is_public_read(request):
        return
    bounds = _public_bounds(request)
    if bounds is None or (slug, ref_id) not in bounds[2]:
        _deny_unpublished()


def _require_public_target(request: Request, slug: str, path: str) -> None:
    if not _is_public_read(request):
        return
    bounds = _public_bounds(request)
    if bounds is None or (slug, path) not in bounds[1]:
        _deny_unpublished()


def _is_public_ref(request: Request, slug: str, ref_id: str) -> bool:
    bounds = _public_bounds(request)
    if bounds is None:
        return False
    return (slug, ref_id) in bounds[2]


def _render(request: Request, name: str, ctx: dict) -> HTMLResponse:
    """统一渲染:注入 lang + t。所有 TemplateResponse 走这里。"""
    lang = resolve_lang(request)
    ctx = {
        "nav_active": "",
        **ctx,
        "lang": lang,
        "t": translator(lang),
        "public_read": _is_public_read(request),
    }
    resp = request.app.state.templates.TemplateResponse(request, name, ctx)
    if _is_public_read(request):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def _knowledge_error_text(request: Request, raw: str) -> str:
    """知识域错误在 Web 边界按稳定类别本地化，绝不把中文异常原文带入英文页。"""
    value = str(raw)
    if any(token in value for token in ("YAML", "无法解析", "严格", "时间", "content_hash")):
        key = "knowledge.error_invalid_document"
    elif "scope" in value or "范围" in value:
        key = "knowledge.error_scope"
    elif any(token in value for token in ("name 不能为空", "title 不能为空")):
        key = "knowledge.error_title_required"
    elif any(token in value for token in ("title 重复", "规范标题", "规范化", "alias", "别名", "冲突")):
        key = "knowledge.error_duplicate_name"
    elif "候选" in value and any(token in value for token in ("不可", "不存在", "状态")):
        key = "knowledge.error_candidate_state"
    elif any(token in value for token in ("保存", "写入", "迁移", "transaction")):
        key = "knowledge.error_save"
    else:
        key = "knowledge.error_unknown"
    return _t(request)(key)


def _open(request: Request, slug: str) -> Workspace:
    if _is_public_read(request):
        bounds = _public_bounds(request)
        if bounds is None or slug not in bounds[0]:
            raise HTTPException(status_code=404, detail="workspace not found")
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
def healthz(request: Request) -> JSONResponse:
    headers = {}
    if _is_public_read(request):
        headers["Cache-Control"] = "no-store"
    return JSONResponse({"ok": True}, headers=headers)


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
    journals = [s for s in matched if getattr(s, "journal", False)]
    pinned = [by_slug[p] for p in pins if p in by_slug]
    pinned_set = {s.slug for s in pinned}
    if pinned:
        # 总结跟置顶同一行,但不写入 pinned.yaml,针脚仍按用户置顶集合。
        pinned = pinned + [j for j in journals if j.slug not in pinned_set]
        pinned_set = {s.slug for s in pinned}
        rest = [s for s in matched if s.slug not in pinned_set]
        rest.sort(key=lambda s: (-s.last_activity.timestamp(), s.slug))
    else:
        rest = [s for s in matched if not getattr(s, "journal", False)]
        rest.sort(key=lambda s: (-s.last_activity.timestamp(), s.slug))
        rest = journals + rest
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
    if _is_public_read(request):
        bounds = _public_bounds(request)
        if bounds is None:
            items = []
        else:
            items = [s for s in items if s.slug in bounds[0]]
    filt = _dash_filter(filter)
    pin_list = read_pins(root)
    pinned, rest, qn = _dash_groups(items, pin_list, q or "", filt)
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
            "pin_slugs": set(pin_list),
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
    start: str | None = Query(None, alias="from"),
    end: str | None = Query(None, alias="to"),
) -> HTMLResponse:
    t = _t(request)
    try:
        q = resolve_timeline_query(
            month=month,
            day=day,
            mode=mode,
            unknown=unknown,
            start=start,
            end=end,
        )
    except TimelineQueryError:
        raise HTTPException(status_code=400, detail=t("tl.bad_query")) from None
    items = scan_timeline(request.app.state.root)
    unknown_items = [it for it in items if it.occurred_at is None]
    counts: dict[str, int] = {}
    for it in items:
        if it.occurred_at is not None:
            key = it.occurred_at.isoformat()
            counts[key] = counts.get(key, 0) + 1
    range_on = q.start is not None and q.end is not None
    r0, r1 = (q.start, q.end) if range_on else (q.day, q.day)
    cells = []
    weeks = []
    month_days = month_cells(q.month.year, q.month.month)
    for i, d in enumerate(month_days):
        n = counts.get(d.isoformat(), 0)
        in_range = q.view == "calendar" and r0 <= d <= r1
        edge = in_range and (d == r0 or d == r1)
        cell = {
            "date": d,
            "iso": d.isoformat(),
            "num": d.day,
            "mute": d.month != q.month.month,
            "today": d == datetime.date.today(),
            "on": (not range_on or r0 == r1) and q.view == "calendar" and d == q.day,
            "in": in_range and not edge and range_on and r0 != r1,
            "edge": edge and range_on and r0 != r1,
            "dots": min(n, 3),
            "href": cell_href(q, d),
        }
        cells.append(cell)
        if i % 7 == 0:
            w0, w1 = week_bounds(d)
            weeks.append(
                {
                    "from": w0.isoformat(),
                    "to": w1.isoformat(),
                    "href": f"/timeline?from={w0.isoformat()}&to={w1.isoformat()}",
                    "days": [],
                }
            )
        weeks[-1]["days"].append(cell)
    if range_on:
        day_items = [
            it
            for it in filter_range(items, q.start, q.end)
            if not is_journal_item(it, request.app.state.root)
        ]
    else:
        day_items = [it for it in items if it.occurred_at == q.day]
    range_groups: list[dict] = []
    if range_on and r0 != r1:
        buckets: dict[str, list] = {}
        order: list[str] = []
        for it in day_items:
            key = it.occurred_at.isoformat() if it.occurred_at else ""
            if key not in buckets:
                buckets[key] = []
                order.append(key)
            buckets[key].append(it)
        range_groups = [{"key": k, "entries": buckets[k]} for k in order]
    digest_n = sum(
        1
        for it in day_items
        if (Path(request.app.state.root) / it.workspace / "references" / it.id / "digest.md").is_file()
    )
    span = range_day_count(r0, r1) if range_on else 1
    too_long = span > MAX_RANGE_DAYS
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
            "weeks": weeks,
            "day_items": day_items,
            "unknown_items": unknown_items,
            "unknown_n": len(unknown_items),
            "recent_groups": recent_groups,
            "prev_day": prev_d.isoformat(),
            "next_day": next_d.isoformat(),
            "day_iso": q.day.isoformat(),
            "month_iso": q.month.strftime("%Y-%m"),
            "range_on": range_on and r0 != r1,
            "range_from": r0.isoformat(),
            "range_to": r1.isoformat(),
            "range_label": format_range_label(r0, r1, zh=(lang == "zh")),
            "range_groups": range_groups,
            "digest_n": digest_n,
            "no_digest_n": len(day_items) - digest_n,
            "too_long": too_long,
            "max_range_days": MAX_RANGE_DAYS,
        },
    )


@router.post("/timeline/review")
def timeline_review(
    request: Request,
    start: str = Form("", alias="from"),
    end: str = Form("", alias="to"),
    workspace: str = Form(""),
):
    t = _t(request)
    a = parse_calendar_date(start)
    b = parse_calendar_date(end)
    if a is None or b is None:
        raise HTTPException(status_code=400, detail=t("tl.bad_date"))
    if a > b:
        a, b = b, a
    root = Path(request.app.state.root)
    slug = workspace.strip()
    items = scan_timeline(root)
    try:
        found = prepare_range(items, a, b, root=root)
        with_d, without = collect_digests(root, found)
        body = generate_review_body(
            with_d,
            without,
            artifact_dir=root / ".kairo" / "review-work",
        )
        ws = resolve_review_workspace(root, slug)
        occ = occupied_span([it for it, _ in with_d]) or (a, b)
        rid = write_review_reference(ws, occ[0], occ[1], body, occurred=b)
    except WorkspaceNotFound:
        raise HTTPException(status_code=404, detail=t("err.ws_not_found"))
    except ReviewError as e:
        key = {
            "range-too-long": "tl.review_too_long",
            "empty-range": "tl.empty_range",
            "no-digest": "tl.review_no_digest",
            "empty": "tl.review_failed",
        }.get(str(e), "tl.review_failed")
        raise HTTPException(status_code=400, detail=t(key).format(n=MAX_RANGE_DAYS)) from e
    dest = "/w/" + quote(ws.root.name) + "?ref=" + quote(rid)
    return RedirectResponse(dest, status_code=303)


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
def workspace_view(
    request: Request,
    slug: str,
    ref: str | None = None,
    task_id: str | None = None,
) -> HTMLResponse:
    ws = _open(request, slug)
    streams, corpus = _split_refs(ws)
    targets = _target_states(ws)
    if _is_public_read(request):
        bounds = _public_bounds(request)
        if bounds is None:
            raise HTTPException(status_code=404)
        _, pub_targets, pub_refs = bounds
        streams = [r for r in streams if (slug, r["id"]) in pub_refs]
        corpus = [r for r in corpus if (slug, r["id"]) in pub_refs]
        targets = [t for t in targets if (slug, t["path"]) in pub_targets]
    registry = request.app.state.registry
    running = registry.current(slug)
    requested_task = registry.get(task_id) if task_id else None
    if requested_task is not None and requested_task.slug != slug:
        requested_task = None
    shown_task = running or requested_task
    ids = {r["id"] for r in streams} | {r["id"] for r in corpus}
    select_ref = ref if ref in ids else None
    return _render(
        request,
        "workspace.html",
        {
            "slug": slug,
            "topic": ws.constitution.topic,
            "targets": targets,
            "streams": streams,
            "corpus": corpus,
            "select_ref": select_ref,
            "run_task_id": shown_task.task_id if shown_task else None,
            **(
                _step_template_vars(request, ws, slug, shown_task)
                if shown_task is not None
                else {}
            ),
            **_run_button_ctx(request, ws, slug),
            "glossary_todo_n": _glossary_todo_n(ws, Path(request.app.state.root)),
        },
    )



@router.get("/w/{slug}/doc", response_class=HTMLResponse)
def doc_view(request: Request, slug: str, path: str) -> HTMLResponse:
    ws = _open(request, slug)
    _require_public_target(request, slug, path)
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
    _require_public_ref(request, slug, ref_id)
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
            "is_public": _is_public_ref(request, slug, ref_id),
        },
    )


@router.post("/w/{slug}/ref/{ref_id}/public", response_class=HTMLResponse)
def ref_public_view(
    request: Request,
    slug: str,
    ref_id: str,
    public: str = Form(""),
) -> HTMLResponse:
    if _is_public_read(request):
        raise HTTPException(status_code=404)
    ws = _open(request, slug)
    if ref_id not in ws.list_reference_ids():
        raise HTTPException(status_code=404, detail="reference not found")
    want = (public or "").strip() in {"1", "true", "on"}
    try:
        set_reference_public(Path(request.app.state.root), ws, ref_id, public=want)
    except PublicationWriteError as exc:
        key = "ref.lock_failed"
        status = 409 if exc.code in {"corrupt", "invalid"} else 400
        raise HTTPException(status_code=status, detail=_t(request)(key)) from exc
    return ref_view(request, slug, ref_id)


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
    _require_public_ref(request, slug, ref_id)
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
    _require_public_ref(request, slug, ref_id)
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


def _target_meta_vars(
    request: Request, ws: Workspace, slug: str, path: str, *, include_reader: bool
) -> dict:
    ts = ws.read_state().targets.get(path)
    status = ts.status if ts else "missing"
    has_doc = (ws.root / path).is_file()
    diag = ts.diagnostic if ts else None
    return {
        "slug": slug,
        "path": path,
        "status": status,
        "reason": effective_compose_block_reason(ws, path, ts),
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
        "include_reader": include_reader,
    }


@router.get("/w/{slug}/target", response_class=HTMLResponse)
def target_view(request: Request, slug: str, path: str) -> HTMLResponse:
    """右栏产物元信息(状态/blocked 原因) + (OOB)中间预览正文。"""
    ws = _open(request, slug)
    if path not in {t.path for t in ws.constitution.targets}:
        raise HTTPException(status_code=404, detail="target not found")
    _require_public_target(request, slug, path)
    return _render(
        request,
        "_target_meta.html",
        _target_meta_vars(request, ws, slug, path, include_reader=True),
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


def _knowledge_drift_ref_title(workspace: Workspace | None, ref_id: str) -> str:
    if workspace is None:
        return ref_id
    try:
        man = workspace.read_manifest(ref_id)
    except (OSError, ValueError):
        return ref_id
    title = (man.title or "").strip() or ref_id
    day = (man.occurred_at or "")[:10]
    if day:
        return f"{title} · {day}"
    return title


def _knowledge_drift_rows(
    state: State,
    current_hash: str,
    *,
    live_targets: set[str],
    slug: str = "",
    workspace: Workspace | None = None,
) -> list[dict[str, str]]:
    """仅报告实际消费知识上下文的产物，避免把原料/证据误报为待重算。"""
    rows: list[dict[str, str]] = []
    ws_href = f"/w/{quote(slug)}" if slug else ""
    for path, product in state.products.items():
        parts = path.split("/")
        consumes_knowledge = (
            len(parts) == 3
            and parts[0] == "references"
            and parts[2] in {"digest.md", "prose.md"}
        )
        if consumes_knowledge and product.knowledge_hash != current_hash:
            ref_id = parts[1]
            kind = "digest" if parts[2] == "digest.md" else "prose"
            # re-step 的 reference 契约接收 ref_id，而不是产物路径。
            rows.append(
                {
                    "path": path,
                    "target": ref_id,
                    "kind": kind,
                    "title": _knowledge_drift_ref_title(workspace, ref_id),
                    "href": f"{ws_href}?ref={quote(ref_id)}" if ws_href else "",
                }
            )
    for path, target_state in state.targets.items():
        if path in live_targets and target_state.knowledge_hash != current_hash:
            rows.append(
                {
                    "path": path,
                    "target": path,
                    "kind": "live",
                    "title": path,
                    "href": ws_href,
                }
            )
    return rows


def _glossary_todo_n(ws: Workspace, serve_root: Path) -> int:
    try:
        from kairo.knowledge_review import todo_count as knowledge_todo_count
        from kairo.knowledge import current_hash

        knowledge_todos = knowledge_todo_count(ws.root)
        current = current_hash(serve_root, ws.root)
        state = ws.read_state()
        live_targets = {target.path for target in ws.constitution.live_targets()}
        knowledge_todos += len(
            _knowledge_drift_rows(
                state,
                current,
                live_targets=live_targets,
                slug=ws.root.name,
                workspace=ws,
            )
        )
        return knowledge_todos
    except ValueError:
        return 1


def _knowledge_run_boundary(ws: Workspace) -> dict:
    """纯数据快照：必须在 reg.start 前取得，避免极速子进程先写完。"""
    try:
        from kairo.knowledge_review import load_review

        review = load_review(ws.root)
        state = ws.read_state()
        return {
            "candidates": frozenset(candidate.id for candidate in review.candidates),
            "errors": frozenset(review.extract_errors),
            "error_versions": dict(review.extract_error_versions),
            "products": {
            key: value.knowledge_generation for key, value in state.products.items()
            },
            "targets": {
            key: value.knowledge_generation for key, value in state.targets.items()
            },
        }
    except Exception:
        return {}


def _apply_knowledge_run_boundary(task, boundary: dict) -> None:
    task.knowledge_before_candidates = boundary.get("candidates", frozenset())
    task.knowledge_before_errors = boundary.get("errors", frozenset())
    task.knowledge_before_error_versions = boundary.get("error_versions", {})
    task.knowledge_before_products = boundary.get("products", {})
    task.knowledge_before_targets = boundary.get("targets", {})


def _workspace_glossary_ctx(
    request: Request,
    ws: Workspace,
    slug: str,
    *,
    error: str | None = None,
    error_scope: str | None = None,
    form: dict | None = None,
) -> dict:
    from kairo.glossary import GlossaryError, load_workspace_glossary, workspace_effective

    t = _t(request)
    load_failed = False
    local = []
    pending: list = []
    candidates: list = []
    extract_errors: dict = {}
    try:
        workspace_effective(ws.root, serve_root=Path(request.app.state.root))
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
    return {
        "slug": slug,
        "local_entries": local,
        "local_count": len(local),
        "error": error,
        "error_scope": error_scope,
        "load_failed": load_failed,
        "form_name": form.get("name", ""),
        "form_note": form.get("note", ""),
        "form_aka": form.get("aka", ""),
        "form_tags": form.get("tags", ""),
        "pending": pending,
        "candidates": candidates if not load_failed else [],
        "extract_errors": extract_errors if not load_failed else {},
    }


def _glossary_fragment(
    request: Request,
    ws: Workspace,
    slug: str,
    *,
    error: str | None = None,
    error_scope: str | None = None,
    form: dict | None = None,
) -> HTMLResponse:
    """写失败留在统一页表单；成功 303 到可刷新的 GET。"""
    if error:
        return _root_glossary_page(
            request,
            selected_slug=slug,
            ws_error=error,
            ws_error_scope=error_scope,
            ws_form=form,
        )
    return RedirectResponse(
        url=f"/glossary?workspace={quote(slug)}", status_code=303
    )


@router.get("/w/{slug}/glossary")
def glossary_view(request: Request, slug: str):
    """旧右栏入口转到统一维护页。"""
    _open(request, slug)
    try:
        from kairo.knowledge import load_global

        load_global(Path(request.app.state.root))
    except ValueError as exc:
        return _knowledge_page(request, selected_slug=slug, error=str(exc))
    return RedirectResponse(
        url=f"/knowledge?workspace={quote(slug)}", status_code=303
    )


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
    """兼容 POST：写入唯一 KnowledgeStore。"""
    if not name.strip():
        return _knowledge_page(request, selected_slug=slug, error="name 不能为空")
    if scope.strip() != "workspace":
        return _knowledge_page(request, selected_slug=slug, error=f"{name}（{note}）未保存：未知 scope {scope!r}，workspace 知识只能写本层")
    return knowledge_workspace_add(request, slug, title=name, description=note, aliases=aka, tags=tags)


@router.post("/w/{slug}/glossary/{index}/delete", response_class=HTMLResponse)
def glossary_delete(
    request: Request,
    slug: str,
    index: int,
    scope: str = Form("workspace"),
) -> HTMLResponse:
    ws = _open(request, slug)
    try:
        from kairo.knowledge import KnowledgeError, migrate_workspace, save_workspace

        if scope.strip() != "workspace":
            raise KnowledgeError(f"未知 scope {scope!r}，workspace 知识只能写本层")
        document = migrate_workspace(ws.root)
        document.entries.pop(index)
        save_workspace(ws.root, document)
    except (KnowledgeError, IndexError) as e:
        return _knowledge_page(request, selected_slug=slug, error=str(e))
    return _knowledge_page(request, selected_slug=slug, success=True)


@router.post("/w/{slug}/glossary/candidates/{cid}/accept", response_class=HTMLResponse)
def glossary_candidate_accept(request: Request, slug: str, cid: str) -> HTMLResponse:
    try:
        from kairo.knowledge_review import accept_workspace

        accept_workspace(_open(request, slug).root, cid)
    except (ValueError, OSError) as e:
        return _knowledge_page(request, selected_slug=slug, error=str(e))
    return _knowledge_page(request, selected_slug=slug, success=True)


@router.post("/w/{slug}/glossary/candidates/{cid}/merge", response_class=HTMLResponse)
def glossary_candidate_merge(
    request: Request, slug: str, cid: str, existing_name: str = Form(...)
) -> HTMLResponse:
    try:
        from kairo.knowledge import load_workspace
        from kairo.knowledge_review import merge_workspace

        root = _open(request, slug).root
        document, _ = load_workspace(root)
        target = next((entry.id for entry in document.entries if entry.title == existing_name), "")
        merge_workspace(root, cid, target)
    except (ValueError, OSError) as e:
        return _knowledge_page(request, selected_slug=slug, error=str(e))
    return _knowledge_page(request, selected_slug=slug, success=True)


@router.post("/w/{slug}/glossary/candidates/{cid}/ignore", response_class=HTMLResponse)
def glossary_candidate_ignore(request: Request, slug: str, cid: str) -> HTMLResponse:
    try:
        from kairo.knowledge_review import ignore

        ignore(_open(request, slug).root, cid)
    except (ValueError, OSError) as e:
        return _knowledge_page(request, selected_slug=slug, error=str(e))
    return _knowledge_page(request, selected_slug=slug, success=True)


@router.post("/w/{slug}/glossary/candidates/{cid}/promote", response_class=HTMLResponse)
def glossary_candidate_promote(request: Request, slug: str, cid: str) -> HTMLResponse:
    try:
        from kairo.knowledge import KnowledgeError
        from kairo.knowledge_review import accept_workspace, load_review, promote_entry

        root = _open(request, slug).root
        candidate = next((item for item in load_review(root).candidates if item.id == cid or item.legacy_id == cid), None)
        if candidate is None:
            raise KnowledgeError(f"知识候选不存在:{cid}")
        # root_rejected 已有本地 ke-* authority；旧 gc URL 不能再走一次 accept。
        if candidate.status == "rejected_global" and candidate.entry_id:
            promote_entry(root, candidate.entry_id)
        elif candidate.status == "pending":
            promote_entry(root, accept_workspace(root, cid).id)
        else:
            raise KnowledgeError(f"候选不可提升:{candidate.status}")
    except (ValueError, OSError) as e:
        return _knowledge_page(request, selected_slug=slug, error=str(e))
    return _knowledge_page(request, selected_slug=slug, success=True)


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
    selected_slug: str | None = None,
    ws_error: str | None = None,
    ws_error_scope: str | None = None,
    ws_form: dict | None = None,
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
    scanned = []
    try:
        scanned = list(scan_workspaces(serve))
        slugs = {s.slug for s in scanned}
        query_ws = (selected_slug or request.query_params.get("workspace") or "").strip()
        selected_slug = query_ws if query_ws in slugs else None
        entries = load_glossary_file(root_glossary_path(serve))
        names = {e.name for e in entries}
        cand = (form.get("name") or "").strip()
        if cand:
            names.add(cand)
        for s in scanned:
            local = load_workspace_glossary(serve / s.slug)
            local_names = [e.name for e in local]
            ov = [n for n in local_names if n in names]
            try:
                todo_n = _glossary_todo_n(_open(request, s.slug), serve)
            except HTTPException:
                todo_n = 0
            impact.append(
                {
                    "slug": s.slug,
                    "overrides": ov,
                    "override_n": len(ov),
                    "local_names": local_names,
                    "todo_n": todo_n,
                }
            )
        from kairo.glossary_review import STATUS_PENDING_ROOT, load_review

        promotions = []
        for s in scanned:
            for c in load_review(serve / s.slug).candidates:
                if c.status == STATUS_PENDING_ROOT:
                    row = c.model_dump()
                    row["slug"] = s.slug
                    promotions.append(row)
    except GlossaryError as e:
        load_failed = True
        error = error or str(e)
        selected_slug = None
    ctx = {
        "nav_active": "glossary",
        "entries": _entry_rows(entries),
        "count": len(entries),
        "impact": impact,
        "ws_n": len(impact),
        "override_ws_n": sum(1 for i in impact if i["override_n"]),
        "machine_hint": machine_migration_hint(),
        "error": error,
        "success": success,
        "load_failed": load_failed,
        "shared_form_name": form.get("name", ""),
        "shared_form_note": form.get("note", ""),
        "shared_form_aka": form.get("aka", ""),
        "shared_form_tags": form.get("tags", ""),
        "form_name": "",
        "form_note": "",
        "form_aka": "",
        "form_tags": "",
        "promotions": promotions,
        "selected_slug": selected_slug,
        "slug": selected_slug or "",
        "local_entries": [],
        "local_count": 0,
        "pending": [],
        "candidates": [],
        "extract_errors": {},
        "error_scope": ws_error_scope,
        "ws_panel_error": None,
    }
    if selected_slug and not load_failed:
        ws_ctx = _workspace_glossary_ctx(
            request,
            _open(request, selected_slug),
            selected_slug,
            error=ws_error,
            error_scope=ws_error_scope,
            form=ws_form,
        )
        root_error = ctx["error"]
        ctx.update(ws_ctx)
        ctx["ws_panel_error"] = ws_ctx.get("error")
        ctx["error"] = root_error
    elif ws_error:
        ctx["error"] = ctx["error"] or ws_error
    return _render(request, "root_glossary.html", ctx)


@router.get("/glossary", response_class=HTMLResponse)
def root_glossary_view(request: Request):
    """旧书签始终进入唯一知识入口；读取时自动迁移 legacy 真名册。"""
    query = request.url.query
    return RedirectResponse(f"/knowledge{('?' + query) if query else ''}", status_code=303)


@router.post("/glossary", response_class=HTMLResponse)
def root_glossary_add(
    request: Request,
    name: str = Form(...),
    note: str = Form(""),
    aka: str = Form(""),
    tags: str = Form(""),
    workspace: str = Form(""),
) -> HTMLResponse:
    """兼容 POST：公共 glossary 写入 global KnowledgeStore。"""
    return knowledge_global_add(request, title=name, description=note, aliases=aka, tags=tags, workspace=workspace)


@router.post("/glossary/{index}/delete", response_class=HTMLResponse)
def root_glossary_delete(
    request: Request, index: int, workspace: str = Form("")
) -> HTMLResponse:
    from kairo.knowledge import KnowledgeError, migrate_global, save_global

    serve = Path(request.app.state.root)
    try:
        document = migrate_global(serve)
        document.entries.pop(index)
        save_global(serve, document)
    except (KnowledgeError, IndexError) as e:
        return _knowledge_page(request, selected_slug=workspace or None, error=str(e))
    return _knowledge_page(request, selected_slug=workspace or None, success=True)


@router.post("/glossary/candidates/{slug}/{cid}/accept", response_class=HTMLResponse)
def root_candidate_accept(
    request: Request, slug: str, cid: str, workspace: str = Form("")
) -> HTMLResponse:
    try:
        from kairo.knowledge_review import accept_global

        accept_global(Path(request.app.state.root), _open(request, slug).root, cid)
    except (ValueError, OSError) as e:
        return _knowledge_page(request, selected_slug=slug, error=str(e))
    return _knowledge_page(request, selected_slug=slug, success=True)


@router.post("/glossary/candidates/{slug}/{cid}/merge", response_class=HTMLResponse)
def root_candidate_merge(
    request: Request,
    slug: str,
    cid: str,
    existing_name: str = Form(...),
    workspace: str = Form(""),
) -> HTMLResponse:
    try:
        from kairo.knowledge import load_global
        from kairo.knowledge_review import merge_global

        serve = Path(request.app.state.root)
        target = next((entry.id for entry in load_global(serve)[0].entries if entry.title == existing_name), "")
        merge_global(serve, _open(request, slug).root, cid, target)
    except (ValueError, OSError) as e:
        return _knowledge_page(request, selected_slug=workspace or slug, error=str(e))
    return _knowledge_page(request, selected_slug=workspace or slug, success=True)


@router.post("/glossary/candidates/{slug}/{cid}/reject", response_class=HTMLResponse)
def root_candidate_reject(
    request: Request,
    slug: str,
    cid: str,
    reason: str = Form(""),
    workspace: str = Form(""),
) -> HTMLResponse:
    try:
        from kairo.knowledge_review import reject_global

        reject_global(_open(request, slug).root, cid, reason)
    except (ValueError, OSError) as e:
        return _knowledge_page(request, selected_slug=workspace or slug, error=str(e))
    return _knowledge_page(request, selected_slug=workspace or slug, success=True)


# #182 统一知识页。旧 /glossary 保留为兼容维护入口；新的写入和候选闭环只走此页。
def _knowledge_page(
    request: Request, *, selected_slug: str | None = None, error: str | None = None, success: bool = False
) -> HTMLResponse:
    from kairo.knowledge import KnowledgeError, load_global, load_workspace
    from kairo.knowledge_review import invalidate_stale

    serve = Path(request.app.state.root)
    slugs = [item.slug for item in scan_workspaces(serve)]
    selected = selected_slug or request.query_params.get("workspace")
    selected = selected if selected in slugs else None
    filter_text = (request.query_params.get("filter") or "").strip().lower()
    global_entries = []
    local_entries = []
    candidates = []
    extract_errors: list[dict[str, str]] = []
    promotions = []
    drift: list[dict[str, str]] = []
    try:
        global_entries = load_global(serve)[0].entries
        for entry in global_entries:
            for source in entry.sources:
                source.__dict__["available"] = bool(source.workspace_slug) and (serve / source.workspace_slug / source.path).is_file()
        if filter_text:
            global_entries = [entry for entry in global_entries if filter_text in " ".join([entry.title, entry.description, entry.status, *entry.tags, *(source.path for source in entry.sources)]).lower()]
        for slug in slugs:
            review = invalidate_stale(serve / slug)
            promotion_entries = {entry.id: entry for entry in load_workspace(serve / slug)[0].entries}
            for candidate in review.candidates:
                if candidate.status == "pending_global":
                    row = candidate.model_dump()
                    row["slug"] = slug
                    # promotion 必须显示当前 entry_id 对应的完整可审字段，候选快照只是兼容回退。
                    entry = promotion_entries.get(candidate.entry_id)
                    if entry is not None:
                        row.update({
                            "title": entry.title,
                            "description": entry.description,
                            "tags": entry.tags,
                            "aliases": [alias.model_dump() for alias in entry.aliases],
                            "sources": [
                                {**source.model_dump(), "available": (serve / slug / source.path).is_file()}
                                for source in entry.sources
                            ],
                        })
                    else:
                        row["sources"] = [
                            {**source.model_dump(), "available": (serve / slug / source.path).is_file()}
                            for source in candidate.sources
                        ]
                    if not filter_text or filter_text in " ".join([candidate.title, candidate.description, candidate.status, *candidate.tags, candidate.path]).lower():
                        promotions.append(row)
        if selected:
            local_entries = load_workspace(serve / selected)[0].entries
            if filter_text:
                local_entries = [entry for entry in local_entries if filter_text in " ".join([entry.title, entry.description, entry.status, *entry.tags, *(source.path for source in entry.sources)]).lower()]
            from kairo.knowledge import current_hash

            current = current_hash(serve, serve / selected)
            workspace = _open(request, selected)
            state = workspace.read_state()
            live_targets = {
                target.path for target in workspace.constitution.live_targets()
            }
            drift.extend(
                _knowledge_drift_rows(
                    state,
                    current,
                    live_targets=live_targets,
                    slug=selected,
                    workspace=workspace,
                )
            )
            review = invalidate_stale(serve / selected)
            for candidate in review.candidates:
                if candidate.status not in {"pending", "pending_global", "rejected_global"}:
                    continue
                row = candidate.model_dump()
                row["available"] = (serve / selected / candidate.path).is_file()
                row["sources"] = [
                    {
                        **source.model_dump(),
                        "available": (serve / selected / source.path).is_file(),
                    }
                    for source in candidate.sources
                ]
                if candidate.suggestion:
                    targets = {entry.id: entry.title for entry in [*local_entries, *global_entries]}
                    parts = []
                    for term, value in candidate.suggestion.items():
                        if not str(value).startswith("merge:"):
                            continue
                        title = targets.get(str(value).removeprefix("merge:"), "")
                        if title:
                            parts.append(f"{term} → {title}")
                    row["suggestion_text"] = "；".join(parts)
                haystack = " ".join([
                    candidate.title, candidate.description, candidate.status,
                    candidate.path, *candidate.tags,
                ]).lower()
                if not filter_text or filter_text in haystack:
                    candidates.append(row)
            extract_errors = [
                {
                    "key": key,
                    "message": message,
                    "source_kind": review.extract_error_meta.get(key, {}).get("source_kind", key.split(":", 1)[0]),
                    "path": review.extract_error_meta.get(key, {}).get("path", key.split(":", 1)[1] if ":" in key else key),
                    "version": review.extract_error_meta.get(key, {}).get("version", str(review.extract_error_versions.get(key, 0))),
                }
                for key, message in review.extract_errors.items()
            ]
            for entry in local_entries:
                for source in entry.sources:
                    # 模型允许额外显示字段；不写回存储。
                    source.__dict__["available"] = (serve / selected / source.path).is_file()
    except KnowledgeError as exc:
        error = error or str(exc)
    if error:
        error = _knowledge_error_text(request, error)
    return _render(
        request,
        "knowledge.html",
        {
            "nav_active": "knowledge",
            "root": str(serve),
            "slugs": slugs,
            "selected_slug": selected,
            "global_entries": global_entries,
            "local_entries": local_entries,
            "candidates": candidates,
            "candidate_open_count": sum(item["status"] in {"pending", "pending_global"} for item in candidates),
            "promotions": promotions,
            "extract_errors": extract_errors,
            "knowledge_drift": drift,
            "knowledge_filter": filter_text,
            "error": error,
            "success": success,
        },
    )


@router.get("/knowledge", response_class=HTMLResponse)
def knowledge_view(request: Request) -> HTMLResponse:
    return _knowledge_page(request)


def _knowledge_aliases(raw: str):
    from kairo.knowledge import KnowledgeAlias

    return [KnowledgeAlias(value=value) for value in _parse_aka(raw)]


@router.post("/knowledge/global", response_class=HTMLResponse)
def knowledge_global_add(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    aliases: str = Form(""),
    tags: str = Form(""),
    workspace: str = Form(""),
) -> HTMLResponse:
    from kairo.knowledge import KnowledgeError, load_global, new_entry, save_global, validate_entries

    serve = Path(request.app.state.root)
    try:
        document, _ = load_global(serve)
        entry = new_entry(title=title, scope="global", aliases=_knowledge_aliases(aliases), description=description, tags=_parse_tags(tags))
        validate_entries([*document.entries, entry], scope="global")
        document.entries.append(entry)
        save_global(serve, document)
    except (KnowledgeError, ValueError) as exc:
        return _knowledge_page(request, selected_slug=workspace or None, error=f"{title}（{description}）未保存：{exc}")
    return _knowledge_page(request, selected_slug=workspace or None, success=True)


@router.post("/w/{slug}/knowledge", response_class=HTMLResponse)
def knowledge_workspace_add(
    request: Request, slug: str, title: str = Form(...), description: str = Form(""), aliases: str = Form(""), tags: str = Form("")
) -> HTMLResponse:
    from kairo.knowledge import KnowledgeError, load_workspace, new_entry, save_workspace, validate_entries

    ws = _open(request, slug)
    try:
        document, _ = load_workspace(ws.root)
        entry = new_entry(title=title, scope="workspace", aliases=_knowledge_aliases(aliases), description=description, tags=_parse_tags(tags))
        validate_entries([*document.entries, entry], scope="workspace")
        document.entries.append(entry)
        save_workspace(ws.root, document)
    except (KnowledgeError, ValueError) as exc:
        return _knowledge_page(request, selected_slug=slug, error=str(exc))
    return _knowledge_page(request, selected_slug=slug, success=True)


@router.post("/w/{slug}/knowledge/{entry_id}/obsolete", response_class=HTMLResponse)
def knowledge_obsolete(request: Request, slug: str, entry_id: str) -> HTMLResponse:
    from kairo.knowledge import KnowledgeError
    from kairo.knowledge_review import set_obsolete

    try:
        set_obsolete(_open(request, slug).root, entry_id)
    except KnowledgeError as exc:
        return _knowledge_page(request, selected_slug=slug, error=str(exc))
    return _knowledge_page(request, selected_slug=slug, success=True)


@router.post("/w/{slug}/knowledge/{entry_id}/promote", response_class=HTMLResponse)
def knowledge_entry_promote(request: Request, slug: str, entry_id: str) -> HTMLResponse:
    from kairo.knowledge import KnowledgeError
    from kairo.knowledge_review import promote_entry

    try:
        promote_entry(_open(request, slug).root, entry_id)
    except KnowledgeError as exc:
        return _knowledge_page(request, selected_slug=slug, error=str(exc))
    return _knowledge_page(request, selected_slug=slug, success=True)


@router.post("/w/{slug}/knowledge/extract", response_class=HTMLResponse)
def knowledge_extract_retry_early(request: Request, slug: str, path: str = Form(...), source_kind: str = Form("")) -> HTMLResponse:
    """静态路由必须先于 `{entry_id}`，否则 FastAPI 会把 extract 当条目 id。"""
    return _knowledge_extract_retry_impl(request, slug, path, source_kind=source_kind)


@router.post("/w/{slug}/knowledge/{entry_id}", response_class=HTMLResponse)
def knowledge_update(
    request: Request, slug: str, entry_id: str, title: str = Form(...), description: str = Form(""), aliases: str = Form(""), tags: str = Form("")
) -> HTMLResponse:
    from kairo.knowledge import KnowledgeError
    from kairo.knowledge_review import update_workspace_entry

    try:
        update_workspace_entry(_open(request, slug).root, entry_id, title=title, description=description, aliases=_knowledge_aliases(aliases), tags=_parse_tags(tags))
    except KnowledgeError as exc:
        return _knowledge_page(request, selected_slug=slug, error=str(exc))
    return _knowledge_page(request, selected_slug=slug, success=True)


@router.post("/w/{slug}/knowledge/candidates/{candidate_id}/{action}", response_class=HTMLResponse)
def knowledge_candidate_action(request: Request, slug: str, candidate_id: str, action: str, entry_id: str = Form("")) -> HTMLResponse:
    from kairo.knowledge import KnowledgeError
    from kairo.knowledge_review import accept_workspace, ignore, merge_workspace

    try:
        root = _open(request, slug).root
        if action == "accept":
            accept_workspace(root, candidate_id)
        elif action == "ignore":
            ignore(root, candidate_id)
        elif action == "merge":
            merge_workspace(root, candidate_id, entry_id)
        else:
            raise KnowledgeError(f"未知候选动作:{action}")
    except KnowledgeError as exc:
        return _knowledge_page(request, selected_slug=slug, error=str(exc))
    return _knowledge_page(request, selected_slug=slug, success=True)


@router.post("/w/{slug}/knowledge/candidates/{candidate_id}", response_class=HTMLResponse)
def knowledge_candidate_update(request: Request, slug: str, candidate_id: str, title: str = Form(...), description: str = Form(""), aliases: str = Form(""), tags: str = Form("")) -> HTMLResponse:
    from kairo.knowledge import KnowledgeError
    from kairo.knowledge_review import update_candidate

    try:
        update_candidate(_open(request, slug).root, candidate_id, title=title, description=description, aliases=_knowledge_aliases(aliases), tags=_parse_tags(tags))
    except KnowledgeError as exc:
        return _knowledge_page(request, selected_slug=slug, error=str(exc))
    return _knowledge_page(request, selected_slug=slug, success=True)


def _knowledge_extract_retry_impl(request: Request, slug: str, path: str, *, source_kind: str = "") -> HTMLResponse:
    """仅重试已完成产物的候选提取，不重跑 Digest/Compose。"""
    from kairo.knowledge import KnowledgeError
    from kairo.knowledge_review import extract_after_success
    from kairo.provider import select_provider

    ws = _open(request, slug)
    try:
        candidate_path = Path(path)
        allowed_digest = len(candidate_path.parts) == 3 and candidate_path.parts[0] == "references" and candidate_path.name == "digest.md"
        if candidate_path.is_absolute() or ".." in candidate_path.parts or not (path == "understanding.md" or allowed_digest):
            raise KnowledgeError("候选提取 path 非法")
        source = ws.root / candidate_path
        if not source.is_file():
            raise KnowledgeError("候选提取来源不存在")
        from kairo.knowledge_review import load_review

        review = load_review(ws.root)
        from kairo.knowledge_review import extract_error_meta
        if not source_kind:
            candidates = [meta.get("source_kind", key.split(":", 1)[0]) for key, meta in review.extract_error_meta.items() if meta.get("path", key.split(":", 1)[1] if ":" in key else key) == path]
            source_kind = candidates[0] if len(set(candidates)) == 1 else ("compose" if path == "understanding.md" else "digest")
        meta = extract_error_meta(review, source_kind, path)
        if not meta:
            raise KnowledgeError("候选提取错误不存在或来源类型不匹配")
        extract_after_success(ws.root, Path(request.app.state.root), source_kind=source_kind, path=path, text=source.read_text(encoding="utf-8"), provider=select_provider())
    except (KnowledgeError, OSError) as exc:
        return _knowledge_page(request, selected_slug=slug, error=str(exc))
    return _knowledge_page(request, selected_slug=slug, success=True)


@router.post("/knowledge/candidates/{slug}/{candidate_id}/{action}", response_class=HTMLResponse)
def knowledge_global_action(
    request: Request, slug: str, candidate_id: str, action: str, reason: str = Form(""), entry_id: str = Form("")
) -> HTMLResponse:
    from kairo.knowledge import KnowledgeError
    from kairo.knowledge_review import accept_global, merge_global, reject_global

    try:
        root = _open(request, slug).root
        if action == "accept":
            accept_global(Path(request.app.state.root), root, candidate_id)
        elif action == "merge":
            merge_global(Path(request.app.state.root), root, candidate_id, entry_id)
        elif action == "reject":
            reject_global(root, candidate_id, reason)
        else:
            raise KnowledgeError(f"未知公共审核动作:{action}")
    except KnowledgeError as exc:
        return _knowledge_page(request, selected_slug=slug, error=str(exc))
    return _knowledge_page(request, selected_slug=slug, success=True)


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
    """HTMX 得运行片段；完整导航回工作区壳并附着同一任务。"""
    if not request.headers.get("hx-request"):
        return RedirectResponse(
            "/w/" + quote(slug) + "?task_id=" + quote(task.task_id),
            status_code=303,
        )
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
            if not request.headers.get("hx-request"):
                return RedirectResponse("/w/" + quote(slug), status_code=303)
            return HTMLResponse(
                f'<p class="muted run-summary">{_t(request)("run.clean_msg")}</p>'
            )
        argv = [sys.executable, "-m", "kairo", "run"]
    try:
        # 必须在子进程实际工作前建立边界，避免历史候选被说成本次产出。
        boundary = _knowledge_run_boundary(ws)
        task = reg.start(slug, ws.root, argv, job_kind="reconcile")
        _apply_knowledge_run_boundary(task, boundary)
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
    # task id 是全局 registry key；不得被另一 workspace 借用来读取诊断或运行结论。
    if task is not None and task.slug != slug:
        task = None
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
        lines.extend(_knowledge_run_summary_lines(ws, slug, task, t))
    # 任务结束后释放运行锁;OOB 刷新主按钮、活 target 圆点与元信息(#180)
    lines.append(_run_status_oob(request, ws, slug, t))
    return HTMLResponse("".join(lines))


def _knowledge_run_summary_lines(ws: Workspace, slug: str, task, t) -> list[str]:
    """只读取 task 启动边界之后的产物和审核记录，绝不把历史 pending 伪装成本轮。"""
    if task is None:
        return [f'<p class="run-summary-meta">{escape(t("knowledge.run_not_available"))}</p>']
    try:
        from kairo.knowledge_review import load_review

        review = load_review(ws.root)
        candidates = [
            candidate
            for candidate in review.candidates
            if candidate.id not in task.knowledge_before_candidates
        ]
        errors = [
            path for path, version in review.extract_error_versions.items()
            if version != task.knowledge_before_error_versions.get(path, 0)
        ]
        state = ws.read_state()
        changed_products = [
            value for key, value in state.products.items()
            if value.knowledge_generation
            and value.knowledge_generation != task.knowledge_before_products.get(key)
        ]
        changed_targets = [
            value for key, value in state.targets.items()
            if value.knowledge_generation
            and value.knowledge_generation != task.knowledge_before_targets.get(key)
        ]
        diagnostics = [
            value.knowledge_diagnostic
            for value in [*changed_products, *changed_targets]
            if value.knowledge_diagnostic is not None
        ]
        unavailable = [item for item in diagnostics if not item.available]
        matched = sum(len(item.matched_entry_ids) for item in diagnostics)
        ambiguity = sum(item.ambiguities for item in diagnostics)
        truncated = sum(item.truncated for item in diagnostics)
        skipped = sum(item.skipped for item in diagnostics)
        digest_candidates = sum(item.source_kind == "digest" for item in candidates)
        compose_candidates = sum(item.source_kind == "compose" for item in candidates)
        lines: list[str] = []
        if candidates:
            lines.append(
                f'<p class="run-summary-meta"><a href="/knowledge?workspace={quote(slug)}">'
                f'{escape(t("knowledge.run_candidates").format(digest=digest_candidates, compose=compose_candidates))}</a></p>'
            )
        if unavailable:
            lines.append(
                f'<p class="run-summary-meta run-summary-error">{escape(t("knowledge.run_unavailable").format(n=len(unavailable)))}</p>'
            )
        elif diagnostics:
            lines.append(
                f'<p class="run-summary-meta">{escape(t("knowledge.run_match_stats").format(matched=matched, ambiguity=ambiguity, truncated=truncated, skipped=skipped))}</p>'
            )
        if errors:
            # error 内容可能来自 provider；结果区只泄露数量和安全入口。
            lines.append(
                f'<p class="run-summary-meta"><a href="/knowledge?workspace={quote(slug)}">{escape(t("knowledge.run_extract_errors").format(n=len(errors)))}</a></p>'
            )
        if not candidates and not diagnostics and not errors:
            lines.append(f'<p class="run-summary-meta">{escape(t("knowledge.run_empty"))}</p>')
        return lines
    except Exception:
        return [f'<p class="run-summary-meta">{escape(t("knowledge.run_not_available"))}</p>']


def _run_status_oob(request: Request, ws: Workspace, slug: str, t) -> str:
    """run-summary 后把 ACTIONS / 左栏圆点 / METADATA 换成当前 state。"""
    btn = _run_button_ctx(request, ws, slug)
    nav = _render(
        request,
        "_targets_list.html",
        {"slug": slug, "targets": _target_states(ws)},
    ).body.decode()
    parts = [
        '<div id="run-btn-wrap" hx-swap-oob="true">'
        + _run_button_html(slug, btn, t)
        + "</div>",
        f'<div id="targets-list" hx-swap-oob="true">{nav}</div>',
    ]
    live = [item.path for item in ws.constitution.live_targets()]
    if live:
        path = "understanding.md" if "understanding.md" in live else live[0]
        meta = _render(
            request,
            "_target_meta.html",
            _target_meta_vars(request, ws, slug, path, include_reader=False),
        ).body.decode()
        parts.append(f'<div id="meta" hx-swap-oob="true">{meta}</div>')
    return "".join(parts)


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
        boundary = _knowledge_run_boundary(ws)
        task = reg.start(slug, ws.root, argv, job_kind="reconcile")
        _apply_knowledge_run_boundary(task, boundary)
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
        boundary = _knowledge_run_boundary(ws)
        task = reg.start(slug, ws.root, argv, job_kind="reconcile")
        _apply_knowledge_run_boundary(task, boundary)
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
        boundary = _knowledge_run_boundary(ws)
        task = reg.start(
            slug, ws.root, argv, job_kind="prose", object_title=title
        )
        _apply_knowledge_run_boundary(task, boundary)
    except RuntimeError:
        task = reg.current(slug)
        if task is None:
            raise
    return _step_response(request, ws, slug, task)




@router.get("/w/{slug}/step/{task_id}/stream")
def step_stream(request: Request, slug: str, task_id: str) -> StreamingResponse:
    task = request.app.state.registry.get(task_id)
    if task is None or task.slug != slug:
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
    task = request.app.state.registry.get(task_id)
    if task is None or task.slug != slug:
        raise HTTPException(status_code=404, detail="task not found")
    ok = request.app.state.registry.cancel(task_id)
    t = _t(request)
    if ok:
        # 只替换按钮，保留 SSE 与 done hook；终态摘要负责刷新主按钮和状态圆点。
        return HTMLResponse(f'<button class="btn btn-ghost" disabled>{t("step.canceling")}</button>')
    return HTMLResponse(f'<span class="muted">{t("step.cannot_cancel")}</span>')


def _console_only(request: Request) -> None:
    if _is_public_read(request):
        raise HTTPException(status_code=404)


def _api_error(exc: Exception, status: int = 400) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": str(exc), "code": getattr(exc, "code", None)},
        status_code=status,
    )


def _serve(request: Request) -> Path:
    return Path(request.app.state.root)


@router.get("/projects", response_class=HTMLResponse)
def projects_page(request: Request, error: str | None = None) -> HTMLResponse:
    _console_only(request)
    from kairo.projects import list_projects

    return _render(
        request,
        "projects.html",
        {
            "nav_active": "projects",
            "root": str(_serve(request)),
            "projects": list_projects(_serve(request)),
            "error": error or "",
        },
    )


@router.post("/projects", response_class=HTMLResponse)
def projects_create(request: Request, name: str = Form(...)) -> HTMLResponse:
    _console_only(request)
    from kairo.projects import ProjectError, create_project

    try:
        project = create_project(_serve(request), name)
    except ProjectError as e:
        return projects_page(request, error=str(e))
    return RedirectResponse(f"/projects/{project.id}", status_code=303)


@router.get("/projects/{project_id}", response_class=HTMLResponse)
def project_page(request: Request, project_id: str, error: str = "", notice: str = "") -> HTMLResponse:
    _console_only(request)
    from kairo.projects import ProjectError, get_project, list_runs

    try:
        project = get_project(_serve(request), project_id)
    except ProjectError:
        raise HTTPException(status_code=404)
    from kairo.web.discovery import scan_workspaces

    return _render(
        request,
        "project.html",
        {
            "nav_active": "projects",
            "project": project,
            "available_workspaces": scan_workspaces(_serve(request)),
            "runs": list_runs(_serve(request), project_id),
            "error": error,
            "notice": notice,
        },
    )


@router.post("/projects/{project_id}/edit")
def project_edit_form(request: Request, project_id: str, name: str = Form(...)) -> RedirectResponse:
    _console_only(request)
    from kairo.projects import ProjectError, edit_project

    try:
        edit_project(_serve(request), project_id, name=name)
    except ProjectError as e:
        return project_page(request, project_id, error=str(e))
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/workspaces")
async def project_link_form(request: Request, project_id: str) -> HTMLResponse:
    _console_only(request)
    from kairo.projects import ProjectError, set_workspaces

    form = await request.form()
    slugs = [str(v) for v in form.getlist("workspaces")]
    try:
        set_workspaces(_serve(request), project_id, slugs)
    except ProjectError as e:
        return project_page(request, project_id, error=str(e))
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/workspaces/{slug}/unlink")
def project_unlink_form(request: Request, project_id: str, slug: str) -> HTMLResponse:
    _console_only(request)
    from kairo.projects import ProjectError, unlink_workspace

    try:
        unlink_workspace(_serve(request), project_id, slug)
    except ProjectError as e:
        return project_page(request, project_id, error=str(e))
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/datasources")
def project_ds_add_form(
    request: Request,
    project_id: str,
    url: str = Form(...),
    purpose: str = Form(""),
) -> HTMLResponse:
    _console_only(request)
    from kairo.projects import ProjectError, add_datasource

    try:
        add_datasource(_serve(request), project_id, url=url, purpose=purpose)
    except ProjectError as e:
        return project_page(request, project_id, error=str(e))
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/datasources/{ds_id}/read")
def project_ds_read_form(request: Request, project_id: str, ds_id: str) -> HTMLResponse:
    _console_only(request)
    from kairo.projects import ProjectError, read_project_datasource
    from kairo.readers import ReadError

    try:
        text = read_project_datasource(_serve(request), project_id, ds_id)
    except (ProjectError, ReadError) as e:
        code = getattr(e, "code", None)
        return project_page(request, project_id, error=f"{code or 'error'}: {e}")
    return project_page(request, project_id, notice=text[:500])


@router.post("/projects/{project_id}/datasources/{ds_id}/delete")
def project_ds_rm_form(request: Request, project_id: str, ds_id: str) -> HTMLResponse:
    _console_only(request)
    from kairo.projects import ProjectError, remove_datasource

    try:
        remove_datasource(_serve(request), project_id, ds_id)
    except ProjectError as e:
        return project_page(request, project_id, error=str(e))
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/tasks")
def project_task_create_form(
    request: Request,
    project_id: str,
    name: str = Form(...),
    datasource_id: str = Form(...),
    schedule: str = Form("once"),
) -> HTMLResponse:
    _console_only(request)
    from kairo.projects import ProjectError, create_task

    try:
        create_task(_serve(request), project_id, name=name, datasource_id=datasource_id, schedule=schedule)
    except ProjectError as e:
        return project_page(request, project_id, error=str(e))
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/tasks/{task_id}/run")
def project_task_run_form(request: Request, project_id: str, task_id: str) -> HTMLResponse:
    _console_only(request)
    from kairo.projects import ProjectError, run_task

    try:
        record = run_task(_serve(request), project_id, task_id)
    except ProjectError as e:
        return project_page(request, project_id, error=str(e))
    if record.status == "succeeded":
        return RedirectResponse(f"/projects/{project_id}/runs/{record.id}", status_code=303)
    return project_page(request, project_id, error=record.reason or "failed")


@router.get("/projects/{project_id}/runs/{run_id}", response_class=HTMLResponse)
def artifact_page(request: Request, project_id: str, run_id: str) -> HTMLResponse:
    _console_only(request)
    from kairo.projects import ProjectError, get_project, get_run, read_artifact

    try:
        project = get_project(_serve(request), project_id)
        run = get_run(_serve(request), project_id, run_id)
        body = read_artifact(_serve(request), project_id, run_id)
    except ProjectError:
        raise HTTPException(status_code=404)
    return _render(
        request,
        "artifact.html",
        {
            "nav_active": "projects",
            "project": project,
            "run": run,
            "body_html": render_markdown(body),
        },
    )


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, error: str = "", notice: str = "") -> HTMLResponse:
    _console_only(request)
    from kairo.settings import as_public_dict

    return _render(
        request,
        "settings.html",
        {
            "nav_active": "settings",
            "settings": as_public_dict(),
            "error": error,
            "notice": notice,
        },
    )


@router.post("/settings")
def settings_set_form(request: Request, path: str = Form(...), value: str = Form(...)) -> HTMLResponse:
    _console_only(request)
    from kairo.settings import SettingsError, set_dotted

    try:
        set_dotted(path, value)
    except SettingsError as e:
        return settings_page(request, error=str(e))
    return RedirectResponse("/settings", status_code=303)


@router.get("/api/projects")
def api_projects_list(request: Request) -> JSONResponse:
    _console_only(request)
    from kairo.projects import list_projects, project_to_dict

    return JSONResponse({"ok": True, "projects": [project_to_dict(p) for p in list_projects(_serve(request))]})


@router.post("/api/projects")
async def api_projects_create(request: Request) -> JSONResponse:
    _console_only(request)
    from kairo.projects import ProjectError, create_project, project_to_dict

    body = await request.json()
    try:
        project = create_project(_serve(request), body.get("name") or "")
    except ProjectError as e:
        return _api_error(e)
    return JSONResponse({"ok": True, "project": project_to_dict(project)})


@router.get("/api/projects/{project_id}")
def api_project_get(request: Request, project_id: str) -> JSONResponse:
    _console_only(request)
    from kairo.projects import ProjectError, get_project, list_runs, project_to_dict

    try:
        project = get_project(_serve(request), project_id)
    except ProjectError as e:
        return _api_error(e, 404)
    return JSONResponse(
        {
            "ok": True,
            "project": project_to_dict(project),
            "runs": [r.model_dump() for r in list_runs(_serve(request), project_id)],
        }
    )


@router.patch("/api/projects/{project_id}")
async def api_project_patch(request: Request, project_id: str) -> JSONResponse:
    _console_only(request)
    from kairo.projects import ProjectError, edit_project, project_to_dict

    body = await request.json()
    try:
        project = edit_project(_serve(request), project_id, name=body.get("name"))
    except ProjectError as e:
        return _api_error(e)
    return JSONResponse({"ok": True, "project": project_to_dict(project)})


@router.post("/api/projects/{project_id}/workspaces")
async def api_project_link(request: Request, project_id: str) -> JSONResponse:
    _console_only(request)
    from kairo.projects import ProjectError, link_workspace, project_to_dict, set_workspaces

    body = await request.json()
    slugs = body.get("workspaces")
    try:
        if isinstance(slugs, list):
            project = set_workspaces(_serve(request), project_id, [str(s) for s in slugs])
        else:
            project = link_workspace(_serve(request), project_id, body.get("slug") or "")
    except ProjectError as e:
        return _api_error(e)
    return JSONResponse({"ok": True, "project": project_to_dict(project)})


@router.delete("/api/projects/{project_id}/workspaces/{slug}")
def api_project_unlink(request: Request, project_id: str, slug: str) -> JSONResponse:
    _console_only(request)
    from kairo.projects import ProjectError, project_to_dict, unlink_workspace

    try:
        project = unlink_workspace(_serve(request), project_id, slug)
    except ProjectError as e:
        return _api_error(e)
    return JSONResponse({"ok": True, "project": project_to_dict(project)})


@router.get("/api/settings")
def api_settings_get(request: Request) -> JSONResponse:
    _console_only(request)
    from kairo.settings import as_public_dict

    return JSONResponse({"ok": True, "settings": as_public_dict()})


@router.patch("/api/settings")
async def api_settings_patch(request: Request) -> JSONResponse:
    _console_only(request)
    from kairo.settings import SettingsError, as_public_dict, set_dotted

    body = await request.json()
    try:
        set_dotted(body.get("path") or "", body.get("value"))
    except SettingsError as e:
        return _api_error(e)
    return JSONResponse({"ok": True, "settings": as_public_dict()})


@router.post("/api/projects/{project_id}/datasources")
async def api_ds_add(request: Request, project_id: str) -> JSONResponse:
    _console_only(request)
    from kairo.projects import ProjectError, add_datasource

    body = await request.json()
    try:
        ds = add_datasource(
            _serve(request),
            project_id,
            url=body.get("url") or "",
            kind=body.get("kind") or None,
            purpose=body.get("purpose") or "",
        )
    except ProjectError as e:
        return _api_error(e)
    return JSONResponse({"ok": True, "datasource": ds.model_dump()})


@router.post("/api/projects/{project_id}/datasources/{ds_id}/read")
def api_ds_read(request: Request, project_id: str, ds_id: str) -> JSONResponse:
    _console_only(request)
    from kairo.projects import ProjectError, read_project_datasource
    from kairo.readers import ReadError

    try:
        text = read_project_datasource(_serve(request), project_id, ds_id)
    except ReadError as e:
        return JSONResponse({"ok": False, "code": e.code, "error": str(e)}, status_code=400)
    except ProjectError as e:
        return _api_error(e)
    return JSONResponse({"ok": True, "content": text})


@router.delete("/api/projects/{project_id}/datasources/{ds_id}")
def api_ds_rm(request: Request, project_id: str, ds_id: str) -> JSONResponse:
    _console_only(request)
    from kairo.projects import ProjectError, project_to_dict, remove_datasource

    try:
        project = remove_datasource(_serve(request), project_id, ds_id)
    except ProjectError as e:
        return _api_error(e)
    return JSONResponse({"ok": True, "project": project_to_dict(project)})


@router.post("/api/projects/{project_id}/tasks")
async def api_task_create(request: Request, project_id: str) -> JSONResponse:
    _console_only(request)
    from kairo.projects import ProjectError, create_task

    body = await request.json()
    try:
        task = create_task(
            _serve(request),
            project_id,
            name=body.get("name") or "",
            datasource_id=body.get("datasource_id") or "",
            schedule=body.get("schedule") or "once",
            interval_hours=body.get("interval_hours"),
        )
    except ProjectError as e:
        return _api_error(e)
    return JSONResponse({"ok": True, "task": task.model_dump()})


@router.patch("/api/projects/{project_id}/tasks/{task_id}")
async def api_task_edit(request: Request, project_id: str, task_id: str) -> JSONResponse:
    _console_only(request)
    from kairo.projects import ProjectError, edit_task

    body = await request.json()
    try:
        task = edit_task(
            _serve(request),
            project_id,
            task_id,
            name=body.get("name"),
            schedule=body.get("schedule"),
            enabled=body.get("enabled"),
            datasource_id=body.get("datasource_id"),
        )
    except ProjectError as e:
        return _api_error(e)
    return JSONResponse({"ok": True, "task": task.model_dump()})


@router.post("/api/projects/{project_id}/tasks/{task_id}/run")
def api_task_run(request: Request, project_id: str, task_id: str) -> JSONResponse:
    _console_only(request)
    from kairo.projects import ProjectError, run_task

    try:
        record = run_task(_serve(request), project_id, task_id)
    except ProjectError as e:
        return _api_error(e)
    return JSONResponse({"ok": record.status == "succeeded", "run": record.model_dump()})


@router.get("/api/projects/{project_id}/runs/{run_id}")
def api_run_get(request: Request, project_id: str, run_id: str) -> JSONResponse:
    _console_only(request)
    from kairo.projects import ProjectError, get_run, read_artifact

    try:
        run = get_run(_serve(request), project_id, run_id)
        artifact = None
        if run.status == "succeeded":
            artifact = read_artifact(_serve(request), project_id, run_id)
    except ProjectError as e:
        return _api_error(e, 404)
    return JSONResponse({"ok": True, "run": run.model_dump(), "artifact": artifact})

