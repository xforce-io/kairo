"""Project 材料目录、Data Source 缓存与 Run 读取记账。"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator

from kairo.projects import (
    DataSource,
    Project,
    ProjectError,
    _assert_no_secrets,
    _ds,
    _project_dir,
    get_project,
    get_run,
)
from kairo.readers import ReadError, read_datasource
from kairo.refs import topic_members
from kairo.settings import get_connection

CACHE_TTL = timedelta(seconds=3600)
MATERIAL_MAX_BYTES = 2 * 1024 * 1024
SOURCE_UNDERSTANDING = "understanding"
SOURCE_DIGEST = "digest"
SOURCE_DATASOURCE = "datasource"
STATE_AVAILABLE = "available"
STATE_UNAVAILABLE = "unavailable"
STATE_UNCACHED = "uncached"
STATE_FRESH = "fresh"
STATE_EXPIRED = "expired"

_SOURCE_UNDERSTANDING_RE = re.compile(r"^topic:([^:]+):understanding$")
_SOURCE_DIGEST_RE = re.compile(r"^topic:([^:]+):digest:([^:]*):(.+)$")
_SOURCE_DS_RE = re.compile(r"^datasource:(.+)$")

_clock: Callable[[], datetime] | None = None


def utcnow() -> datetime:
    if _clock is not None:
        return _clock()
    return datetime.now(UTC)


def set_clock(fn: Callable[[], datetime] | None) -> None:
    global _clock
    _clock = fn


def content_version(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def config_fingerprint(ds: DataSource) -> str:
    raw = f"{ds.url}\n{ds.reader}\n{ds.connection_id}\n{ds.kind}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def is_fresh(now: datetime, expires_at: datetime) -> bool:
    return now < expires_at


def parse_source_id(source_id: str) -> dict[str, str]:
    text = (source_id or "").strip()
    if not text:
        raise ProjectError("source_id 为空", code="invalid_request")
    m = _SOURCE_UNDERSTANDING_RE.fullmatch(text)
    if m:
        return {"kind": SOURCE_UNDERSTANDING, "slug": m.group(1), "source_id": text}
    m = _SOURCE_DIGEST_RE.fullmatch(text)
    if m:
        return {
            "kind": SOURCE_DIGEST,
            "slug": m.group(1),
            "home": m.group(2),
            "ref_id": m.group(3),
            "source_id": text,
        }
    m = _SOURCE_DS_RE.fullmatch(text)
    if m:
        return {"kind": SOURCE_DATASOURCE, "ds_id": m.group(1), "source_id": text}
    raise ProjectError("无法识别的材料标识", code="not_found")


def cache_dir(serve: Path, project_id: str, ds_id: str) -> Path:
    return _project_dir(serve, project_id) / "cache" / ds_id


def scratch_dir(serve: Path, project_id: str, run_id: str) -> Path:
    return _project_dir(serve, project_id) / "scratch" / run_id


def inputs_dir(serve: Path, project_id: str, run_id: str) -> Path:
    return _project_dir(serve, project_id) / "inputs" / run_id


def _dt_to_iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat()


def _parse_iso(value: str) -> datetime:
    text = value.replace("Z", "+00:00")
    return datetime.fromisoformat(text)


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _atomic_json(path: Path, payload: dict) -> None:
    _assert_no_secrets(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)
    except Exception as exc:
        temp.unlink(missing_ok=True)
        raise ProjectError(f"保存失败:{exc}", code="evidence_failed") from exc


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        temp.write_text(content, encoding="utf-8")
        os.replace(temp, path)
    except Exception as exc:
        temp.unlink(missing_ok=True)
        raise ProjectError(f"保存失败:{exc}", code="evidence_failed") from exc


def _resolve_under(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    root_res = root.resolve()
    if resolved != root_res and root_res not in resolved.parents:
        raise ProjectError("材料路径越界", code="not_found")
    if resolved.is_symlink():
        target = resolved.resolve()
        if target != root_res and root_res not in target.parents:
            raise ProjectError("材料路径越界", code="not_found")
    return resolved


@dataclass
class CacheRecord:
    content: str
    fingerprint: str
    version: str
    fetched_at: str
    expires_at: str
    bytes: int

    @property
    def state(self) -> str:
        return STATE_FRESH if is_fresh(utcnow(), _parse_iso(self.expires_at)) else STATE_EXPIRED


def load_cache(serve: Path, project_id: str, ds_id: str) -> CacheRecord | None:
    folder = cache_dir(serve, project_id, ds_id)
    bundle = folder / "cache.json"
    if bundle.is_file():
        try:
            meta = json.loads(bundle.read_text(encoding="utf-8"))
            content = str(meta["content"])
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
            return None
        if content_version(content) != str(meta.get("version") or ""):
            return None
        try:
            return CacheRecord(
                content=content,
                fingerprint=str(meta["fingerprint"]),
                version=str(meta["version"]),
                fetched_at=str(meta["fetched_at"]),
                expires_at=str(meta["expires_at"]),
                bytes=int(meta.get("bytes") or len(content.encode("utf-8"))),
            )
        except (KeyError, TypeError, ValueError):
            return None
    meta_path = folder / "meta.json"
    body_path = folder / "body.txt"
    if not meta_path.is_file() or not body_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        content = body_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if content_version(content) != str(meta.get("version") or ""):
        return None
    try:
        return CacheRecord(
            content=content,
            fingerprint=str(meta["fingerprint"]),
            version=str(meta["version"]),
            fetched_at=str(meta["fetched_at"]),
            expires_at=str(meta["expires_at"]),
            bytes=int(meta.get("bytes") or len(content.encode("utf-8"))),
        )
    except (KeyError, TypeError, ValueError):
        return None


def write_cache(serve: Path, project_id: str, ds: DataSource, content: str) -> CacheRecord:
    now = utcnow()
    record = CacheRecord(
        content=content,
        fingerprint=config_fingerprint(ds),
        version=content_version(content),
        fetched_at=_dt_to_iso(now),
        expires_at=_dt_to_iso(now + CACHE_TTL),
        bytes=len(content.encode("utf-8")),
    )
    folder = cache_dir(serve, project_id, ds.id)
    _atomic_json(
        folder / "cache.json",
        {
            "fingerprint": record.fingerprint,
            "version": record.version,
            "fetched_at": record.fetched_at,
            "expires_at": record.expires_at,
            "bytes": record.bytes,
            "content": record.content,
        },
    )
    return record


def drop_cache(serve: Path, project_id: str, ds_id: str) -> None:
    folder = cache_dir(serve, project_id, ds_id)
    (folder / "cache.json").unlink(missing_ok=True)
    (folder / "body.txt").unlink(missing_ok=True)
    (folder / "meta.json").unlink(missing_ok=True)


def cache_status(serve: Path, project: Project, ds: DataSource) -> dict[str, Any]:
    cached = load_cache(serve, project.id, ds.id)
    if cached is None:
        return {
            "state": STATE_UNCACHED,
            "version": None,
            "fetched_at": None,
            "expires_at": None,
            "bytes": None,
            "authorized": bool(get_connection(ds.connection_id).authorized),
            "content": None,
        }
    usable = cached.fingerprint == config_fingerprint(ds)
    return {
        "state": cached.state if usable else STATE_UNCACHED,
        "version": cached.version if usable else None,
        "fetched_at": cached.fetched_at if usable else None,
        "expires_at": cached.expires_at if usable else None,
        "bytes": cached.bytes if usable else None,
        "authorized": bool(get_connection(ds.connection_id).authorized),
        "content": cached.content if usable else None,
    }


@dataclass
class MaterialRead:
    source_id: str
    content: str
    version: str
    fetched_at: str | None
    expires_at: str | None
    input_id: str | None
    state: str
    title: str = ""
    type: str = SOURCE_DATASOURCE


def read_cached_datasource(
    serve: Path,
    project_id: str,
    ds_id: str,
    *,
    refresh: bool = False,
) -> MaterialRead:
    project = get_project(serve, project_id)
    ds = _ds(project, ds_id)
    source_id = f"datasource:{ds.id}"
    with _exclusive_lock(cache_dir(serve, project.id, ds.id) / "lock"):
        return _read_cached_datasource_locked(serve, project, ds, source_id, refresh=refresh)


def _read_cached_datasource_locked(
    serve: Path,
    project: Project,
    ds: DataSource,
    source_id: str,
    *,
    refresh: bool,
) -> MaterialRead:
    conn = get_connection(ds.connection_id)
    cached = load_cache(serve, project.id, ds.id)
    fingerprint = config_fingerprint(ds)
    usable = cached is not None and cached.fingerprint == fingerprint
    if not refresh and usable and cached.state == STATE_FRESH:
        if not conn.authorized:
            raise ReadError("permission", "连接未授权")
        return MaterialRead(
            source_id=source_id,
            content=cached.content,
            version=cached.version,
            fetched_at=cached.fetched_at,
            expires_at=cached.expires_at,
            input_id=None,
            state=STATE_FRESH,
            title=ds.purpose or ds.url,
            type=SOURCE_DATASOURCE,
        )
    if not conn.authorized:
        raise ReadError("permission", "连接未授权")
    try:
        content = read_datasource(ds.url, ds.kind, ds.reader, conn)
    except ReadError:
        raise
    if not isinstance(content, str):
        raise ReadError("read_failed", "Reader 返回不是正文")
    record = write_cache(serve, project.id, ds, content)
    return MaterialRead(
        source_id=source_id,
        content=record.content,
        version=record.version,
        fetched_at=record.fetched_at,
        expires_at=record.expires_at,
        input_id=None,
        state=STATE_FRESH,
        title=ds.purpose or ds.url,
        type=SOURCE_DATASOURCE,
    )


def peek_datasource_content(serve: Path, project_id: str, ds_id: str) -> dict[str, Any]:
    project = get_project(serve, project_id)
    ds = _ds(project, ds_id)
    status = cache_status(serve, project, ds)
    if status["content"] is None:
        raise ProjectError("尚无缓存正文", code="cache_missing")
    return {
        "ok": True,
        "source_id": f"datasource:{ds.id}",
        "content": status["content"],
        "version": status["version"],
        "fetched_at": status["fetched_at"],
        "expires_at": status["expires_at"],
        "state": status["state"],
        "authorized": status["authorized"],
        "url": ds.url,
        "purpose": ds.purpose,
        "kind": ds.kind,
        "reader": ds.reader,
    }


def _topic_understanding_path(serve: Path, slug: str) -> Path:
    return Path(serve) / slug / "understanding.md"


def _digest_path(serve: Path, home: str, ref_id: str) -> Path:
    from kairo.refs import timeline_digest_path

    return timeline_digest_path(Path(serve), home, ref_id)


def _read_local_file(serve: Path, path: Path) -> str:
    allowed = Path(serve).resolve()
    resolved = _resolve_under(allowed, path)
    if not resolved.is_file():
        raise ProjectError("材料尚未生成", code="material_unavailable")
    try:
        return resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProjectError(f"读取失败:{exc}", code="read_failed") from exc


def list_context(
    serve: Path,
    project_id: str,
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    project = get_project(serve, project_id)
    topics, datasources = _scope(serve, project, run_id)
    items: list[dict[str, Any]] = []
    for slug in topics:
        understanding = _topic_understanding_path(serve, slug)
        exists = understanding.is_file()
        source_id = f"topic:{slug}:understanding"
        body = understanding.read_text(encoding="utf-8") if exists else None
        items.append(
            {
                "source_id": source_id,
                "title": f"{slug} understanding",
                "purpose": "Topic 事实层",
                "type": SOURCE_UNDERSTANDING,
                "state": STATE_AVAILABLE if exists else STATE_UNAVAILABLE,
                "bytes": len(body.encode("utf-8")) if body is not None else None,
                "version": content_version(body) if body is not None else None,
                "read_args": ["project", "read", project_id, source_id],
            }
        )
        try:
            members = topic_members(serve, slug)
        except Exception:
            members = []
        for rec in members:
            source_id = f"topic:{slug}:digest:{rec.home}:{rec.id}"
            digest = rec.digest_path
            exists = digest is not None and digest.is_file()
            body = digest.read_text(encoding="utf-8") if exists and digest is not None else None
            items.append(
                {
                    "source_id": source_id,
                    "title": rec.title,
                    "purpose": "Ref digest",
                    "type": SOURCE_DIGEST,
                    "state": STATE_AVAILABLE if exists else STATE_UNAVAILABLE,
                    "bytes": len(body.encode("utf-8")) if body is not None else None,
                    "version": content_version(body) if body is not None else None,
                    "read_args": ["project", "read", project_id, source_id],
                }
            )
    for ds in datasources:
        source_id = f"datasource:{ds.id}"
        status = cache_status(serve, project, ds)
        items.append(
            {
                "source_id": source_id,
                "title": ds.purpose or ds.url,
                "purpose": ds.purpose,
                "type": SOURCE_DATASOURCE,
                "state": status["state"],
                "bytes": status["bytes"],
                "version": status["version"],
                "read_args": ["project", "read", project_id, source_id],
            }
        )
    return {"ok": True, "project_id": project.id, "items": items}


def _scope(
    serve: Path, project: Project, run_id: str | None
) -> tuple[list[str], list[DataSource]]:
    if not run_id:
        return list(project.topics), list(project.datasources)
    run = _running_run(serve, project.id, run_id)
    if run.scope_topics is None:
        topics = list(project.topics)
    else:
        topics = list(run.scope_topics)
    if run.scope_datasources is None:
        allowed = {d.id for d in project.datasources}
    else:
        allowed = set(run.scope_datasources)
    datasources = [d for d in project.datasources if d.id in allowed]
    # Frozen scope may include ids later removed; catalog only lists current objects in scope.
    return topics, datasources


def _running_run(serve: Path, project_id: str, run_id: str):
    run = get_run(serve, project_id, run_id)
    if run.project_id != project_id:
        raise ProjectError("Run 不属于该 Project", code="not_found")
    if run.status != "running":
        raise ProjectError("Run 已结束，不能再读取", code="run_closed")
    return run


def read_material(
    serve: Path,
    project_id: str,
    source_id: str,
    *,
    run_id: str | None = None,
    refresh: bool = False,
) -> MaterialRead:
    parsed = parse_source_id(source_id)
    project = get_project(serve, project_id)
    topics, datasources = _scope(serve, project, run_id)
    if parsed["kind"] == SOURCE_DATASOURCE:
        if refresh is False:
            pass
        ds_ids = {d.id for d in datasources}
        if parsed["ds_id"] not in ds_ids:
            raise ProjectError("数据源不在该 Project 范围内", code="not_found")
        result = read_cached_datasource(serve, project_id, parsed["ds_id"], refresh=refresh)
    else:
        if refresh:
            raise ProjectError("仅 Data Source 支持刷新", code="invalid_request")
        result = _read_topic_material(serve, project, parsed, topics)
    if len(result.content.encode("utf-8")) > MATERIAL_MAX_BYTES:
        raise ProjectError("材料超过 2 MiB", code="material_too_large")
    if run_id:
        result.input_id = record_run_input(serve, project_id, run_id, result)
    return result


def _read_topic_material(
    serve: Path,
    project: Project,
    parsed: dict[str, str],
    topics: list[str],
) -> MaterialRead:
    slug = parsed["slug"]
    if slug not in topics:
        raise ProjectError("Topic 未关联该 Project", code="not_found")
    if parsed["kind"] == SOURCE_UNDERSTANDING:
        path = _topic_understanding_path(serve, slug)
        content = _read_local_file(serve, path)
        return MaterialRead(
            source_id=parsed["source_id"],
            content=content,
            version=content_version(content),
            fetched_at=None,
            expires_at=None,
            input_id=None,
            state=STATE_AVAILABLE,
            title=f"{slug} understanding",
            type=SOURCE_UNDERSTANDING,
        )
    members = topic_members(serve, slug)
    home = parsed.get("home") or ""
    ref_id = parsed["ref_id"]
    rec = next((m for m in members if m.home == home and m.id == ref_id), None)
    if rec is None:
        raise ProjectError("digest 不在该 Topic 成员中", code="not_found")
    path = rec.digest_path or _digest_path(serve, home, ref_id)
    content = _read_local_file(serve, path)
    return MaterialRead(
        source_id=parsed["source_id"],
        content=content,
        version=content_version(content),
        fetched_at=None,
        expires_at=None,
        input_id=None,
        state=STATE_AVAILABLE,
        title=rec.title,
        type=SOURCE_DIGEST,
    )


def _input_index_path(folder: Path) -> Path:
    return folder / "index.json"


def _load_index(folder: Path) -> list[dict[str, Any]]:
    path = _input_index_path(folder)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, list):
        return data
    return []


def record_run_input(serve: Path, project_id: str, run_id: str, result: MaterialRead) -> str:
    run = _running_run(serve, project_id, run_id)
    folder = Path(run.scratch_dir) if run.scratch_dir else scratch_dir(serve, project_id, run_id)
    folder = folder if folder.is_absolute() else Path(serve) / folder
    folder.mkdir(parents=True, exist_ok=True)
    with _exclusive_lock(folder / "lock"):
        items = _load_index(folder)
        for item in items:
            if item.get("source_id") == result.source_id and item.get("version") == result.version:
                item["read_count"] = int(item.get("read_count") or 1) + 1
                _atomic_json(_input_index_path(folder), items)
                return str(item["input_id"])
        input_id = f"inp-{uuid.uuid4().hex[:12]}"
        body_name = f"{input_id}.md"
        try:
            _atomic_text(folder / body_name, result.content)
        except ProjectError:
            raise
        except OSError as exc:
            raise ProjectError(f"读取记录保存失败:{exc}", code="evidence_failed") from exc
        items.append(
            {
                "input_id": input_id,
                "source_id": result.source_id,
                "type": result.type,
                "title": result.title,
                "url": None if result.type != SOURCE_DATASOURCE else result.source_id,
                "version": result.version,
                "read_at": _dt_to_iso(utcnow()),
                "read_count": 1,
                "body": body_name,
            }
        )
        _atomic_json(_input_index_path(folder), items)
        return input_id


def load_run_inputs(serve: Path, project_id: str, run_id: str, *, scratch: bool = False) -> list[dict[str, Any]]:
    if scratch:
        run = get_run(serve, project_id, run_id)
        folder = Path(run.scratch_dir) if run.scratch_dir else scratch_dir(serve, project_id, run_id)
        folder = folder if folder.is_absolute() else Path(serve) / folder
    else:
        folder = inputs_dir(serve, project_id, run_id)
    return _load_index(folder)


def read_run_input(serve: Path, project_id: str, run_id: str, input_id: str) -> dict[str, Any]:
    from kairo.projects import reap_run

    run = reap_run(serve, project_id, run_id)
    if run.status == "running":
        raise ProjectError("Run 尚未结束", code="not_found")
    folder = inputs_dir(serve, project_id, run_id)
    for item in _load_index(folder):
        if item.get("input_id") == input_id:
            body_path = folder / str(item.get("body") or f"{input_id}.md")
            if not body_path.is_file():
                raise ProjectError("输入正文缺失", code="not_found")
            content = body_path.read_text(encoding="utf-8")
            return {**item, "content": content, "ok": True}
    raise ProjectError("输入记录不存在", code="not_found")


def _source_in_scope(source_id: str, topics: list[str], datasource_ids: set[str]) -> bool:
    try:
        parsed = parse_source_id(source_id)
    except ProjectError:
        return False
    if parsed["kind"] == SOURCE_DATASOURCE:
        return parsed["ds_id"] in datasource_ids
    return parsed.get("slug") in topics


def validate_recorded_inputs(
    serve: Path,
    project_id: str,
    run_id: str,
    items: list[dict[str, Any]],
    folder: Path,
) -> None:
    project = get_project(serve, project_id)
    topics, datasources = _scope(serve, project, run_id)
    allowed_ds = {d.id for d in datasources}
    for item in items:
        iid = str(item.get("input_id") or "")
        name = str(item.get("body") or f"{iid}.md")
        body = folder / name
        if not body.is_file():
            raise ProjectError("输入证据正文缺失", code="evidence_failed")
        actual = body.read_text(encoding="utf-8")
        if content_version(actual) != item.get("version"):
            raise ProjectError("输入证据校验失败", code="evidence_failed")
        if not _source_in_scope(str(item.get("source_id") or ""), topics, allowed_ds):
            raise ProjectError("输入来源越界", code="evidence_failed")


def finalize_inputs(serve: Path, project_id: str, run_id: str) -> list[dict[str, Any]]:
    src = scratch_dir(serve, project_id, run_id)
    run = get_run(serve, project_id, run_id)
    if run.scratch_dir:
        src = Path(run.scratch_dir)
        if not src.is_absolute():
            src = Path(serve) / src
    dest = inputs_dir(serve, project_id, run_id)
    items = _load_index(src)
    validate_recorded_inputs(serve, project_id, run_id, items, src)
    dest.mkdir(parents=True, exist_ok=True)
    for item in items:
        name = str(item.get("body") or f"{item['input_id']}.md")
        body = src / name
        _atomic_text(dest / name, body.read_text(encoding="utf-8"))
        actual = (dest / name).read_text(encoding="utf-8")
        if content_version(actual) != item.get("version"):
            raise ProjectError("输入证据校验失败", code="evidence_failed")
    _atomic_json(_input_index_path(dest), items)
    return items
