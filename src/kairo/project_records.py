"""Project 引用记录：与 Task Run 分开的可核对引用会话。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from kairo.projects import ProjectError, _new_id, _now, _project_dir, get_project

RUN_ID_RE = re.compile(r"^run-[0-9a-f]{12}$")
REC_ID_RE = re.compile(r"^rec-[0-9a-f]{12}$")
INP_ID_RE = re.compile(r"^inp-[0-9a-f]{12}$")

NEXT_ONE_FLAG = "只保留 --run 或 --record 其中一个"
NEXT_USE_RECORD = "引用记录请使用 --record（rec- 前缀），不要传给 --run"
NEXT_USE_RUN = "Task Run 请使用 --run（run- 前缀），不要传给 --record"
NEXT_NOT_FOUND = (
    "只需正文则省略 --run 与 --record；需要可核对引用则执行 kairo project record create PROJECT_ID"
)
NEXT_RUN_CLOSED = (
    "不要再用该 --run。只需正文则省略标志；需要引用则执行 kairo project record create PROJECT_ID"
)
NEXT_RECORD_CLOSED = (
    "查询已记录输入用 kairo project record show；再记账则执行 kairo project record create PROJECT_ID"
)
NEXT_FIX_ARGS = "按 kairo project --help 纠正参数；--run 与 --record 不可同时使用"
NEXT_SKIP_URL = "跳过该 URL，不要扫内部目录或调用私有 Reader"
NEXT_PERMISSION = "检查连接授权后再试；不要扫内部目录"
NEXT_DEFAULT = "按错误码处理后重试；不要扫内部目录或调用私有 Reader"

_CLOSED_CODES = frozenset(
    {
        "run_closed",
        "record_closed",
        "invalid_link",
        "unsupported_reader",
        "material_too_large",
        "evidence_failed",
    }
)
_NEXT_BY_CODE = {
    "not_found": NEXT_NOT_FOUND,
    "run_closed": NEXT_RUN_CLOSED,
    "record_closed": NEXT_RECORD_CLOSED,
    "invalid_request": NEXT_FIX_ARGS,
    "invalid_link": NEXT_SKIP_URL,
    "unsupported_reader": NEXT_SKIP_URL,
    "permission": NEXT_PERMISSION,
}


def failure_guide(exc: Exception) -> tuple[bool, str]:
    code = getattr(exc, "code", None) or "error"
    retryable = getattr(exc, "retryable", None)
    nxt = getattr(exc, "next", None)
    if retryable is None:
        retryable = code not in _CLOSED_CODES
    if not nxt:
        nxt = _NEXT_BY_CODE.get(code, NEXT_DEFAULT)
    return bool(retryable), str(nxt)


def _fail(message: str, *, code: str, retryable: bool, next: str) -> None:
    raise ProjectError(message, code=code, retryable=retryable, next=next)


def resolve_read_target(
    run_id: str | None, record_id: str | None
) -> tuple[str | None, str | None]:
    run_id = (run_id or "").strip() or None
    record_id = (record_id or "").strip() or None
    if run_id and record_id:
        _fail("不能同时使用 --run 与 --record", code="invalid_request", retryable=True, next=NEXT_ONE_FLAG)
    if run_id:
        if REC_ID_RE.fullmatch(run_id) or run_id.startswith("rec-"):
            _fail("--run 不能传入引用记录标识", code="invalid_request", retryable=True, next=NEXT_USE_RECORD)
        if not RUN_ID_RE.fullmatch(run_id) and not run_id.startswith("run-"):
            _fail("Run 不存在", code="not_found", retryable=True, next=NEXT_NOT_FOUND)
    if record_id:
        if RUN_ID_RE.fullmatch(record_id) or record_id.startswith("run-"):
            _fail("--record 不能传入 Task Run 标识", code="invalid_request", retryable=True, next=NEXT_USE_RUN)
        if not REC_ID_RE.fullmatch(record_id):
            _fail("引用记录不存在", code="not_found", retryable=True, next=NEXT_NOT_FOUND)
    return run_id, record_id


def records_dir(serve: Path, project_id: str) -> Path:
    return _project_dir(serve, project_id) / "records"


def record_dir(serve: Path, project_id: str, record_id: str) -> Path:
    return records_dir(serve, project_id) / record_id


def record_inputs_dir(serve: Path, project_id: str, record_id: str) -> Path:
    return record_dir(serve, project_id, record_id) / "inputs"


def _manifest_path(serve: Path, project_id: str, record_id: str) -> Path:
    return record_dir(serve, project_id, record_id) / "manifest.json"


def _public(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "record_id": manifest["id"],
        "project_id": manifest["project_id"],
        "status": manifest["status"],
        "scope_topics": list(manifest.get("scope_topics") or []),
        "scope_datasources": list(manifest.get("scope_datasources") or []),
        "created_at": manifest.get("created_at"),
        "updated_at": manifest.get("updated_at"),
        "closed_at": manifest.get("closed_at"),
    }


def _save_manifest(serve: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    from kairo.project_materials import _atomic_json

    path = _manifest_path(serve, manifest["project_id"], manifest["id"])
    _atomic_json(path, manifest)
    return manifest


def load_record(serve: Path, project_id: str, record_id: str) -> dict[str, Any]:
    _, record_id = resolve_read_target(None, record_id)
    assert record_id is not None
    get_project(serve, project_id)
    path = _manifest_path(serve, project_id, record_id)
    if not path.is_file():
        _fail("引用记录不存在", code="not_found", retryable=True, next=NEXT_NOT_FOUND)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail("引用记录不存在", code="not_found", retryable=True, next=NEXT_NOT_FOUND)
        raise exc
    if raw.get("project_id") != project_id or raw.get("id") != record_id:
        _fail("引用记录不属于该 Project", code="not_found", retryable=True, next=NEXT_NOT_FOUND)
    return raw


def load_open_record(serve: Path, project_id: str, record_id: str) -> dict[str, Any]:
    rec = load_record(serve, project_id, record_id)
    if rec.get("status") != "open":
        _fail("引用记录已结束，不能再读取", code="record_closed", retryable=False, next=NEXT_RECORD_CLOSED)
    return rec


def create_record(serve: Path, project_id: str) -> dict[str, Any]:
    project = get_project(serve, project_id)
    now = _now()
    record_id = _new_id("rec")
    manifest = {
        "id": record_id,
        "project_id": project.id,
        "status": "open",
        "scope_topics": list(project.topics),
        "scope_datasources": [d.id for d in project.datasources],
        "created_at": now,
        "updated_at": now,
        "closed_at": None,
    }
    _save_manifest(serve, manifest)
    return _public(manifest)


def resume_record(serve: Path, project_id: str, record_id: str) -> dict[str, Any]:
    rec = load_open_record(serve, project_id, record_id)
    rec["updated_at"] = _now()
    _save_manifest(serve, rec)
    return _public(rec)


def end_record(serve: Path, project_id: str, record_id: str) -> dict[str, Any]:
    rec = load_record(serve, project_id, record_id)
    now = _now()
    rec["status"] = "closed"
    rec["updated_at"] = now
    rec["closed_at"] = rec.get("closed_at") or now
    _save_manifest(serve, rec)
    return _public(rec)


def list_record_inputs(serve: Path, project_id: str, record_id: str) -> list[dict[str, Any]]:
    from kairo.project_materials import _load_index

    load_record(serve, project_id, record_id)
    items = _load_index(record_inputs_dir(serve, project_id, record_id))
    out = []
    for item in items:
        out.append(
            {
                "input_id": item.get("input_id"),
                "source_id": item.get("source_id"),
                "type": item.get("type"),
                "title": item.get("title"),
                "version": item.get("version"),
                "url": item.get("url"),
            }
        )
    return out


def show_record(serve: Path, project_id: str, record_id: str) -> dict[str, Any]:
    rec = load_record(serve, project_id, record_id)
    payload = _public(rec)
    payload["inputs"] = list_record_inputs(serve, project_id, record_id)
    return payload


def read_record_input(
    serve: Path, project_id: str, record_id: str, input_id: str
) -> dict[str, Any]:
    from kairo.project_materials import _load_index, evidence_body_path

    load_record(serve, project_id, record_id)
    input_id = (input_id or "").strip()
    if not INP_ID_RE.fullmatch(input_id):
        _fail("输入记录不存在", code="not_found", retryable=True, next=NEXT_NOT_FOUND)
    folder = record_inputs_dir(serve, project_id, record_id)
    for item in _load_index(folder):
        if item.get("input_id") == input_id:
            try:
                body_path = evidence_body_path(folder, str(item.get("body") or f"{input_id}.md"))
            except ProjectError as exc:
                if getattr(exc, "code", None) == "evidence_failed":
                    _fail("输入正文缺失", code="not_found", retryable=True, next=NEXT_NOT_FOUND)
                raise
            content = body_path.read_text(encoding="utf-8")
            return {**item, "content": content, "ok": True}
    _fail("输入记录不存在", code="not_found", retryable=True, next=NEXT_NOT_FOUND)
    raise AssertionError("unreachable")
