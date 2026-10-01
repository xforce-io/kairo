"""#436: Ref home 上的自动 note、持久化诊断与只补入口。"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tomllib
from datetime import datetime, timezone
from pathlib import Path

MAX_GENERATED_CHARS = 800
DEFAULT_NOTE_TIMEOUT_CAP_S = 120
_ARTIFACT = "generated-note.txt"
_PERSONA = (
    "根据下面这一份详备纪要写一条短 note，供人以后扫读。"
    "只使用这一份纪要里已有的内容，不要补充别的材料，也不要写成决定、修正或待确认问题。"
    f"正文不超过 {MAX_GENERATED_CHARS} 个字符，标点和空白计入。"
    "直接输出 note 正文，不要加标题。"
)


def prepared_body(text: str) -> str | None:
    body = (text or "").strip()
    return body if body and len(body) <= MAX_GENERATED_CHARS else None


def resolve_note_timeout_cap() -> int:
    from kairo.provider import _config_path
    path = _config_path()
    try:
        raw = (tomllib.loads(path.read_text()).get("agent") or {}).get("note_timeout_s", 120)
        return max(1, int(raw)) if int(raw) > 0 else DEFAULT_NOTE_TIMEOUT_CAP_S
    except (OSError, ValueError, TypeError, tomllib.TOMLDecodeError):
        return DEFAULT_NOTE_TIMEOUT_CAP_S


def select_note_provider(*, runner=None):
    from kairo.provider import GrokProvider, StubProvider
    if os.environ.get("KAIRO_STUB"):
        return StubProvider()
    return GrokProvider(reasoning_effort="low", runner=runner)


def generation_path(ws, ref_id: str) -> Path:
    return ws.references_dir() / ref_id / "generated-note.json"


def read_generation(ws, ref_id: str) -> dict:
    path = generation_path(ws, ref_id)
    if not path.exists():
        return {"status": "not-attempted", "reason": ""}
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError("invalid note generation state")
        return data
    except (OSError, ValueError):
        return {"status": "failed", "reason": "note 生成状态无法读取", "code": "state-unreadable"}


def _write_generation(ws, ref_id: str, status: str, digest_hash: str, **extra) -> dict:
    data = {"schema_version": 1, "status": status, "digest_hash": digest_hash,
            "updated_at": datetime.now(timezone.utc).isoformat(), **extra}
    path = generation_path(ws, ref_id)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return data


def _home(ws) -> str:
    from kairo.refs import global_home_path, serve_root_of
    return "" if ws.root.resolve() == global_home_path(serve_root_of(ws)).resolve() else ws.root.name


def _generated(ws, ref_id: str) -> list[dict]:
    from kairo.notes import show_notes
    from kairo.refs import serve_root_of
    home = _home(ws) or "global"
    return [it for it in show_notes(serve_root_of(ws), ref_id=ref_id, home=home)["items"]
            if it["type"] == "generated"]


def eligibility(ws, ref_id: str, state=None) -> tuple[str, str]:
    from kairo.timeline import is_fold_class
    if not is_fold_class(ws, ws.read_manifest(ref_id).source_class):
        return "corpus", "基线材料不生成 note"
    ps = (state or ws.read_state()).products.get(f"references/{ref_id}/digest.md")
    if ps is not None and ps.status == "blocked":
        return "digest-blocked", ps.reason or "digest blocked"
    path = ws.references_dir() / ref_id / "digest.md"
    if not path.is_file() or not path.read_text().strip():
        return "no-digest", "尚无有效 digest"
    try:
        generated = _generated(ws, ref_id)
    except (ValueError, OSError) as exc:
        from kairo.rules import safe_provider_summary
        return "failed", "notes 无法读取：" + safe_provider_summary(exc)
    if generated:
        return "already-generated", "已有自动 note，保留"
    return "ready", ""


def note_stale(ws, ref_id: str, state=None) -> bool:
    status, _ = eligibility(ws, ref_id, state)
    if status == "already-generated":
        return read_generation(ws, ref_id)["status"] in {"running", "failed"}
    if status != "ready":
        return False
    digest_hash = hashlib.sha256((ws.references_dir() / ref_id / "digest.md").read_bytes()).hexdigest()
    prior = read_generation(ws, ref_id)
    return prior.get("status") != "failed" or prior.get("digest_hash") != digest_hash


def ensure_generated_note(ws, ref_id: str, *, state=None) -> dict:
    """独占生成锁后重新核对，模型等待不占人工 notes 的锁。"""
    from kairo.notes import append_generated_note
    from kairo.refs import serve_root_of
    from kairo.rules import _run_agent, safe_provider_summary
    folder = ws.references_dir() / ref_id
    with (folder / "generated-note.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        status, reason = eligibility(ws, ref_id, state)
        if status == "already-generated" and read_generation(ws, ref_id)["status"] in {"running", "failed"}:
            digest_hash = hashlib.sha256((folder / "digest.md").read_bytes()).hexdigest()
            return _write_generation(ws, ref_id, "succeeded", digest_hash,
                                     note_id=_generated(ws, ref_id)[0]["stable_id"], reason="", recovered=True)
        if status != "ready":
            return {"status": status, "reason": reason}
        digest = (folder / "digest.md").read_text()
        digest_hash = hashlib.sha256(digest.encode()).hexdigest()
        _write_generation(ws, ref_id, "running", digest_hash, provider="grok", reason="")
        try:
            raw = _run_agent(select_note_provider(), _PERSONA, digest, _ARTIFACT,
                             timeout_cap=resolve_note_timeout_cap())
            body = prepared_body(raw)
            if body is None:
                raise ValueError("note 正文为空或超过 800 个字符")
            home = _home(ws) or "global"
            result = append_generated_note(serve_root_of(ws), ref_id=ref_id, content=body, home=home)
            return _write_generation(ws, ref_id, "succeeded", digest_hash,
                                     provider="grok", note_id=result["stable_id"], reason="")
        except Exception as exc:
            reason = safe_provider_summary(exc)
            if "timeout" in reason.lower():
                reason += f"；机器 note 上限 {resolve_note_timeout_cap()} 秒"
            return _write_generation(ws, ref_id, "failed", digest_hash,
                                     provider="grok", reason=reason)


def digest_for_generated_note(ws, ref_id: str):
    from kairo.refs import member_sources
    local = ws.references_dir() / ref_id / "digest.md"
    if ref_id in ws.list_reference_ids() and local.is_file():
        return "ok", local, _home(ws)
    matches = [(source_ws, rec) for source_ws, rid, rec in member_sources(ws) if rid == ref_id]
    if len(matches) != 1:
        return "absent", None, ""
    source_ws, rec = matches[0]
    path = source_ws.references_dir() / ref_id / "digest.md"
    return ("ok", path, rec.home) if path.is_file() else ("absent", None, "")


def maybe_append_generated_note(ws, ref_id: str) -> str:
    """兼容单条调用；处理 actual home，已存在不重复追加。"""
    from kairo.refs import resolve_open, serve_root_of
    status, _, home = digest_for_generated_note(ws, ref_id)
    if status != "ok":
        return "absent"
    actual, _ = resolve_open(serve_root_of(ws), home, ref_id)
    result = ensure_generated_note(actual, ref_id)
    return {"succeeded": "written", "failed": "failed"}.get(result["status"], "absent")


def topic_note_rows(ws) -> list[dict]:
    from kairo.refs import member_sources
    rows = []
    for source_ws, rid, rec in member_sources(ws):
        status, reason = eligibility(source_ws, rid)
        rows.append({"home": rec.home, "ref_id": rid, "title": rec.title,
                     "status": status, "reason": reason,
                     "generation": read_generation(source_ws, rid)})
    return rows


def failed_notes(ws, ref_id=None) -> list[dict]:
    return [row for row in topic_note_rows(ws)
            if (ref_id is None or row["ref_id"] == ref_id)
            and (row["status"] == "failed" or (row["status"] == "ready"
                 and row["generation"]["status"] == "failed"))]
