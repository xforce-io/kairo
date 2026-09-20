"""#402 kairo ref find / ref read。不扫内部 state 当入口，不触发 step。"""

from __future__ import annotations

from pathlib import Path

from kairo.refs import is_global_home, list_all_refs, list_topic_slugs, topic_members
from kairo.timeline import effective_occurred, parse_calendar_date
from kairo.workspace import Workspace, WorkspaceNotFound


class RefLookupError(ValueError):
    def __init__(self, message: str, *, code: str):
        self.code = code
        super().__init__(message)


def _json_home(home: str) -> str:
    return "" if is_global_home(home) else home


def _normalize_home(home: str | None) -> str | None:
    if home is None:
        return None
    text = home.strip()
    if text in ("", "global"):
        return ""
    return text


def _occurred_iso(rec) -> str | None:
    try:
        ws = _open_home(rec)
        man = ws.read_manifest(rec.id)
        day, _ = effective_occurred(rec.id, man.occurred_at)
    except Exception:
        day, _ = effective_occurred(rec.id, None)
    return day.isoformat() if day is not None else None


def _open_home(rec) -> Workspace:
    if rec.dir is None:
        raise RefLookupError("reference 不可读", code="read_failed")
    root = rec.dir.parent.parent
    return Workspace.open(root)


def _member_topics(serve: Path, rec, catalog) -> list[str]:
    out = []
    for slug in list_topic_slugs(serve):
        members = topic_members(serve, slug, catalog=catalog)
        if any(m.id == rec.id and m.home == rec.home for m in members):
            out.append(slug)
    return out


def _item(serve: Path, rec, catalog) -> dict:
    return {
        "home": _json_home(rec.home),
        "id": rec.id,
        "title": rec.title,
        "occurred_at": _occurred_iso(rec),
        "topics": _member_topics(serve, rec, catalog),
    }


def find_refs(
    serve: Path,
    *,
    title: str | None,
    day: str | None,
    topic: str | None,
) -> dict:
    if not (title or day or topic):
        raise RefLookupError("请指定 --title、--day 或 --topic 之一", code="invalid_request")
    parsed = None
    if day:
        parsed = parse_calendar_date(day)
        if parsed is None:
            raise RefLookupError(f"非法发生时间:{day}", code="invalid_request")
    catalog = list_all_refs(serve)
    rows = list(catalog)
    if title:
        rows = [r for r in rows if title in (r.title or "")]
    if parsed is not None:
        want = parsed.isoformat()
        rows = [r for r in rows if _occurred_iso(r) == want]
    if topic:
        members = topic_members(serve, topic, catalog=catalog)
        keys = {(m.home, m.id) for m in members}
        rows = [r for r in rows if (r.home, r.id) in keys]
    items = [_item(serve, r, catalog) for r in rows]
    return {"ok": True, "count": len(items), "items": items}


def format_find(payload: dict) -> str:
    if payload["count"] == 0:
        return "count 0\n"
    lines = []
    for it in payload["items"]:
        home = it["home"] or "global"
        occ = it["occurred_at"] or "-"
        lines.append(f"{home} {it['id']} {it['title']} {occ}")
    return "\n".join(lines) + "\n"


def _resolve_rec(serve: Path, ref_id: str, home: str | None):
    catalog = list_all_refs(serve)
    matches = [r for r in catalog if r.id == ref_id]
    wanted = _normalize_home(home)
    if wanted is not None:
        matches = [r for r in matches if _json_home(r.home) == wanted]
    if not matches:
        raise RefLookupError("reference 不存在", code="not_found")
    if len(matches) > 1:
        raise RefLookupError("reference 不唯一:请加 --home", code="invalid_request")
    return matches[0]


def _transcript_path(rec) -> tuple[Path, bool]:
    """(path, registered_as_form)."""
    if rec.dir is None:
        raise RefLookupError("transcript 尚未生成", code="material_unavailable")
    try:
        ws = _open_home(rec)
        man = ws.read_manifest(rec.id)
    except (WorkspaceNotFound, OSError) as exc:
        raise RefLookupError("reference 不可读", code="read_failed") from exc
    for item in man.forms:
        if item.role == "transcript":
            loc = Path(item.location)
            path = loc if loc.is_absolute() else ws.root / loc
            return path, True
    return rec.dir / "transcript.md", False


def read_ref(serve: Path, *, ref_id: str, home: str | None, form: str) -> dict:
    form = (form or "").strip()
    if form not in {"digest", "transcript"}:
        raise RefLookupError("非法 --form", code="invalid_request")
    rec = _resolve_rec(serve, ref_id, home)
    if rec.dir is None:
        raise RefLookupError("所请求形态尚未生成", code="material_unavailable")
    registered = False
    if form == "digest":
        path = rec.dir / "digest.md"
    else:
        path, registered = _transcript_path(rec)
    if not path.is_file():
        if form == "transcript" and registered:
            raise RefLookupError("形态文件缺失", code="read_failed")
        raise RefLookupError(f"{form} 尚未生成", code="material_unavailable")
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RefLookupError("形态文件不可读", code="read_failed") from exc
    occ = _occurred_iso(rec)
    payload = {
        "ok": True,
        "home": _json_home(rec.home),
        "id": rec.id,
        "title": rec.title,
        "occurred_at": occ,
        "form": form,
        "content": content,
        "source": {
            "home": _json_home(rec.home),
            "id": rec.id,
            "form": form,
            "title": rec.title,
            "occurred_at": occ,
        },
    }
    return payload
