"""#410 Ref markdown notes：旁路盖楼、CLI 契约。不改 digest / folded，不触发 step。"""

from __future__ import annotations

import fcntl
import getpass
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kairo.ref_find import _normalize_home
from kairo.refs import RefError, list_all_refs, parse_ref_key, ref_key, topic_members

NOTE_TYPES = ("insight", "decision", "open-question", "correction")
DEFAULT_WINDOW_HOURS = 48
_STABLE_PREFIX = "note:"
_NOTE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class NotesError(ValueError):
    def __init__(self, message: str, *, code: str):
        self.code = code
        super().__init__(message)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json_home(home: str) -> str:
    return "" if not home else home


def parse_stable_id(stable_id: str) -> tuple[str, str]:
    raw = (stable_id or "").strip()
    if not raw.startswith(_STABLE_PREFIX):
        raise NotesError("稳定键须以 note: 开头", code="invalid_request")
    rest = raw[len(_STABLE_PREFIX) :]
    if "/" not in rest:
        raise NotesError("稳定键格式无效", code="invalid_request")
    ref_key_s, note_id = rest.rsplit("/", 1)
    if not ref_key_s or not note_id or not _NOTE_ID_RE.match(note_id):
        raise NotesError("稳定键格式无效", code="invalid_request")
    return ref_key_s, note_id


def stable_id_for(home: str, ref_id: str, note_id: str) -> str:
    return f"{_STABLE_PREFIX}{ref_key(home, ref_id)}/{note_id}"


def parse_since(raw: str | None, now: datetime) -> datetime:
    if raw is None:
        return now - timedelta(hours=DEFAULT_WINDOW_HOURS)
    text = raw.strip()
    if not text:
        raise NotesError("非法 --since", code="invalid_request")
    if text.isdigit():
        return now - timedelta(hours=int(text))
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as e:
        raise NotesError("非法 --since", code="invalid_request") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _created(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_created(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _resolve_rec(serve: Path, ref_id: str, home: str | None):
    catalog = list_all_refs(serve)
    matches = [r for r in catalog if r.id == ref_id]
    wanted = _normalize_home(home)
    if wanted is not None:
        matches = [r for r in matches if _json_home(r.home) == wanted]
    if not matches:
        raise NotesError("reference 不存在", code="not_found")
    if len(matches) > 1:
        raise NotesError("reference 不唯一:请加 --home", code="invalid_request")
    rec = matches[0]
    if rec.dir is None:
        raise NotesError("reference 不存在", code="not_found")
    return rec


def _notes_path(rec) -> Path:
    return rec.dir / "notes.jsonl"


def _lock_path(rec) -> Path:
    return rec.dir / "notes.lock"


def _read_records(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def _write_records(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise


def _with_lock(rec, fn):
    rec.dir.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_path(rec)
    with lock_path.open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            return fn()
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _new_note_id(existing: set[str], now: datetime) -> str:
    # Deleted stable IDs must never be assigned to a later note (even in the same second).
    while True:
        candidate = "n" + now.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex
        if candidate not in existing:
            return candidate



def _item(rec, note: dict, *, include_content: bool) -> dict:
    home = rec.home or ""
    sid = stable_id_for(home, rec.id, note["id"])
    row = {
        "stable_id": sid,
        "home": _json_home(home),
        "ref_id": rec.id,
        "type": note["type"],
        "author": note["author"],
        "created_at": note["created_at"],
    }
    body = note.get("content") or ""
    row["excerpt"] = body.splitlines()[0][:80] if body else ""
    if include_content:
        row["content"] = body
    return row


def _provenance(items: list[dict]) -> list[dict]:
    return [{"kind": "note", "id": it["stable_id"]} for it in items]


def add_note(
    serve: Path,
    *,
    ref_id: str,
    content: str,
    note_type: str | None = None,
    home: str | None = None,
    author: str | None = None,
    now: datetime | None = None,
) -> dict:
    body = (content or "").strip()
    if not body:
        raise NotesError("正文为空", code="invalid_request")
    kind = (note_type or "insight").strip() or "insight"
    if kind not in NOTE_TYPES:
        raise NotesError("非法类型", code="invalid_request")
    rec = _resolve_rec(serve, ref_id, home)
    when = now or _now()
    who = (author or getpass.getuser() or "").strip() or "unknown"

    def _commit():
        path = _notes_path(rec)
        records = _read_records(path)
        note_id = _new_note_id({r["id"] for r in records}, when)
        record = {
            "id": note_id,
            "type": kind,
            "author": who,
            "created_at": _created(when),
            "content": body,
        }
        _write_records(path, [*records, record])
        return record

    record = _with_lock(rec, _commit)
    item = _item(rec, record, include_content=True)
    return {
        "ok": True,
        "stable_id": item["stable_id"],
        "home": item["home"],
        "ref_id": item["ref_id"],
        "type": item["type"],
        "author": item["author"],
        "created_at": item["created_at"],
    }


def delete_note(serve: Path, *, stable_id: str) -> None:
    """Remove exactly one note under the same lock used by add_note."""
    key, note_id = parse_stable_id(stable_id)
    home, rid = parse_ref_key(key)
    rec = _resolve_rec(serve, rid, home or "global")

    def _commit():
        path = _notes_path(rec)
        records = _read_records(path)
        remaining = [note for note in records if note["id"] != note_id]
        if len(remaining) == len(records):
            raise NotesError("note 不存在", code="not_found")
        _write_records(path, remaining)

    _with_lock(rec, _commit)


def list_notes(
    serve: Path,
    *,
    ref_id: str | None,
    topic: str | None,
    home: str | None = None,
    since: str | None = None,
    now: datetime | None = None,
) -> dict:
    has_ref = bool(ref_id)
    has_topic = bool(topic)
    if has_ref == has_topic:
        raise NotesError("必须指定 --ref 或 --topic 之一", code="invalid_request")
    if has_topic and home is not None:
        raise NotesError("--topic 不使用 --home", code="invalid_request")
    when = now or _now()
    start = parse_since(since, when)
    catalog = list_all_refs(serve)
    if has_ref:
        recs = [_resolve_rec(serve, ref_id, home)]
    else:
        try:
            members = topic_members(serve, topic, catalog=catalog)
        except RefError as e:
            raise NotesError(str(e) or "Topic 不存在", code="not_found") from e
        recs = [r for r in members if r.dir is not None]
    items: list[dict] = []
    for rec in recs:
        for note in _read_records(_notes_path(rec)):
            created = _parse_created(note["created_at"])
            if created >= start:
                items.append(_item(rec, note, include_content=False))
    items.sort(key=lambda it: (it["created_at"], it["stable_id"]))
    return {
        "ok": True,
        "count": len(items),
        "items": items,
        "provenance": _provenance(items),
    }


def show_notes(
    serve: Path,
    *,
    stable_id: str | None = None,
    ref_id: str | None = None,
    home: str | None = None,
) -> dict:
    has_sid = bool(stable_id)
    has_ref = bool(ref_id)
    if has_sid == has_ref:
        raise NotesError("必须指定稳定键或 --ref 之一", code="invalid_request")
    if has_sid:
        ref_key_s, note_id = parse_stable_id(stable_id)
        home_s, rid = parse_ref_key(ref_key_s)
        rec = _resolve_rec(serve, rid, home_s or "global")
        for note in _read_records(_notes_path(rec)):
            if note["id"] == note_id:
                item = _item(rec, note, include_content=True)
                return {"ok": True, **item}
        raise NotesError("note 不存在", code="not_found")
    rec = _resolve_rec(serve, ref_id, home)
    items = [_item(rec, n, include_content=True) for n in _read_records(_notes_path(rec))]
    return {"ok": True, "count": len(items), "items": items}


def format_list(payload: dict) -> str:
    if payload["count"] == 0:
        return ""
    lines = []
    for it in payload["items"]:
        home = it["home"] or "global"
        lines.append(
            f"{it['stable_id']} {home} {it['ref_id']} {it['type']} {it['author']} {it['created_at']}"
        )
    return "\n".join(lines) + "\n"


def format_show(payload: dict) -> str:
    if "items" in payload:
        if payload["count"] == 0:
            return ""
        blocks = []
        for it in payload["items"]:
            blocks.append(_format_one(it))
        return "\n\n".join(blocks) + "\n"
    return _format_one(payload) + "\n"


def _format_one(it: dict) -> str:
    home = it["home"] or "global"
    header = (
        f"{it['stable_id']}\n"
        f"home={home} ref={it['ref_id']} type={it['type']} "
        f"author={it['author']} created_at={it['created_at']}"
    )
    return header + "\n" + (it.get("content") or "")


def format_add(payload: dict) -> str:
    return payload["stable_id"] + "\n"
