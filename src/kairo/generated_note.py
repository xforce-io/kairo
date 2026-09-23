"""#423：`kairo run --ref` 在纪要就绪后追加一条机器 note。失败不改变 run 的退出码。

#425: machine notes pin grok + reasoning effort low, with a short timeout_cap.
They do not follow digest/compose auto-select or `[agent] timeout_s`.
"""

from __future__ import annotations

import os
import sys
import tomllib

MAX_GENERATED_CHARS = 800
DEFAULT_NOTE_TIMEOUT_CAP_S = 120
_ARTIFACT = "generated-note.txt"
_PERSONA = (
    "根据下面这一份详备纪要写一条短 note，供人以后扫读。"
    "只使用这一份纪要里已有的内容，不要补充别的材料，也不要写成决定、修正或待确认问题。"
    f"正文不超过 {MAX_GENERATED_CHARS} 个字符，标点和空白计入。"
    "直接输出 note 正文，不要加标题。"
)


def _warn(ref_id: str, reason: str) -> None:
    print(f"机器 note 未写入 {ref_id}: {reason}", file=sys.stderr, flush=True)


def prepared_body(text: str) -> str | None:
    """去掉首尾空白后的正文。空或超过 800 个 Unicode 标量值则不能落盘。"""
    body = (text or "").strip()
    if not body or len(body) > MAX_GENERATED_CHARS:
        return None
    return body


def resolve_note_timeout_cap() -> int:
    """#425: note-only CLI cap. Default 120s. Does not change `[agent] timeout_s`."""
    from kairo.provider import _config_path

    path = _config_path()
    if not path.is_file():
        return DEFAULT_NOTE_TIMEOUT_CAP_S
    try:
        section = tomllib.loads(path.read_text()).get("agent") or {}
    except (OSError, tomllib.TOMLDecodeError):
        return DEFAULT_NOTE_TIMEOUT_CAP_S
    raw = section.get("note_timeout_s")
    if raw is None:
        return DEFAULT_NOTE_TIMEOUT_CAP_S
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_NOTE_TIMEOUT_CAP_S
    if value <= 0:
        return DEFAULT_NOTE_TIMEOUT_CAP_S
    return value


def select_note_provider(*, runner=None):
    """Machine notes are grok + low. Ignore auto / KAIRO_PROVIDER / digest settings."""
    from kairo.provider import GrokProvider, StubProvider

    if os.environ.get("KAIRO_STUB"):
        return StubProvider()
    return GrokProvider(reasoning_effort="low", runner=runner)


def maybe_append_generated_note(ws, ref_id: str) -> None:
    """纪要已就绪时调用一轮模型。不合契约或调用失败则不落半条。"""
    from kairo.notes import NotesError, append_generated_note
    from kairo.refs import serve_root_of
    from kairo.rules import _run_agent

    path = ws.references_dir() / ref_id / "digest.md"
    if not path.is_file():
        return
    digest = path.read_text(encoding="utf-8")
    if not digest.strip():
        return
    provider = select_note_provider()
    timeout_cap = resolve_note_timeout_cap()
    try:
        raw = _run_agent(
            provider, _PERSONA, digest, _ARTIFACT, timeout_cap=timeout_cap
        )
    except Exception as exc:
        _warn(ref_id, str(exc))
        return
    body = prepared_body(raw)
    if body is None:
        _warn(ref_id, "正文为空或超过 800 个字符")
        return
    try:
        append_generated_note(
            serve_root_of(ws),
            ref_id=ref_id,
            content=body,
            home=ws.root.name,
        )
    except NotesError as exc:
        _warn(ref_id, str(exc))
