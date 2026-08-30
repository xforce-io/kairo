"""Create-time constitution fill. Runtime reads digest.enabled / targets / kind."""

from __future__ import annotations

KIND_TOPIC = "topic"
KIND_JOURNAL = "journal"

PRESETS: dict[str, dict] = {
    KIND_TOPIC: {
        "digest_enabled": True,
        "empty_targets": False,
        "review_input": True,
    },
    KIND_JOURNAL: {
        "digest_enabled": False,
        "empty_targets": True,
        "review_input": False,
    },
}
TOPIC_PRESET = {"总结": KIND_JOURNAL}


def preset_name(kind: str | None, topic: str) -> str:
    raw = (kind or "").strip()
    if raw in PRESETS and raw != KIND_TOPIC:
        return raw
    alias = TOPIC_PRESET.get((topic or "").strip())
    if alias and (not raw or raw == KIND_TOPIC):
        return alias
    return raw or KIND_TOPIC


def resolve_kind(kind: str | None, topic: str) -> str:
    return preset_name(kind, topic)


def fill_at_create(con) -> None:
    """只在建仓时填字段;打开已有 yaml 不得调用。"""
    name = preset_name(getattr(con, "kind", None), getattr(con, "topic", ""))
    spec = PRESETS.get(name)
    if spec is None:
        if not (getattr(con, "kind", None) or "").strip():
            con.kind = KIND_TOPIC
        return
    con.kind = name
    if spec["empty_targets"]:
        con.targets = []
    con.pipeline.digest.enabled = spec["digest_enabled"]
    con.review_input = spec["review_input"]


def effective_kind(ws) -> str:
    """运行时 kind:yaml 为准;缺省 topic 且 slug/topic 为「总结」视为 journal。"""
    con = ws.constitution
    raw = (getattr(con, "kind", None) or "").strip()
    if raw == KIND_JOURNAL:
        return KIND_JOURNAL
    if raw and raw != KIND_TOPIC:
        return raw
    if ws.root.name == "总结" or (con.topic or "").strip() == "总结":
        return KIND_JOURNAL
    return raw or KIND_TOPIC


def is_journal_workspace(ws) -> bool:
    return effective_kind(ws) == KIND_JOURNAL


def stage_enabled(ws, stage: str) -> bool:
    if is_journal_workspace(ws):
        return False
    con = ws.constitution
    if stage == "digest":
        return bool(getattr(con.pipeline.digest, "enabled", True))
    if stage == "compose":
        return bool(con.live_targets())
    raise ValueError(f"unknown agent stage:{stage}")
