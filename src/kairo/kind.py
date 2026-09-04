"""Create-time constitution fill. Runtime reads digest.enabled / targets / preset."""

from __future__ import annotations

# New naming
PRESET_STANDARD = "standard"
PRESET_JOURNAL = "journal"

# Legacy compatibility
KIND_TOPIC = "topic"
KIND_JOURNAL = "journal"

PRESETS: dict[str, dict] = {
    PRESET_STANDARD: {
        "digest_enabled": True,
        "empty_targets": False,
        "review_input": True,
    },
    PRESET_JOURNAL: {
        "digest_enabled": True,
        "empty_targets": True,
        "review_input": False,
    },
    # Legacy compatibility
    KIND_TOPIC: {
        "digest_enabled": True,
        "empty_targets": False,
        "review_input": True,
    },
}
TOPIC_PRESET = {"总结": PRESET_JOURNAL}


def resolve_preset(preset: str | None, kind: str | None, topic: str) -> str:
    """Resolve preset with legacy kind fallback."""
    # Try new preset field first
    if preset:
        p = preset.strip()
        if p in (PRESET_STANDARD, PRESET_JOURNAL):
            return p
    
    # Legacy kind compatibility: kind: topic → standard, kind: journal → journal
    if kind:
        k = kind.strip()
        if k == KIND_JOURNAL:
            return PRESET_JOURNAL
        if k == KIND_TOPIC:
            return PRESET_STANDARD
    
    # Topic-based preset (e.g. "总结" → journal)
    alias = TOPIC_PRESET.get((topic or "").strip())
    if alias:
        return alias
    
    return PRESET_STANDARD


def preset_name(kind: str | None, topic: str) -> str:
    """Legacy function for backward compatibility."""
    return resolve_preset(None, kind, topic)


def resolve_kind(kind: str | None, topic: str) -> str:
    """Legacy function for backward compatibility."""
    return preset_name(kind, topic)


def fill_at_create(con) -> None:
    """只在建仓时填字段;打开已有 yaml 不得调用。"""
    # Determine the preset
    name = resolve_preset(
        getattr(con, "preset", None),
        getattr(con, "kind", None),
        getattr(con, "topic", "")
    )
    
    spec = PRESETS.get(name)
    if spec is None:
        # Fallback to standard
        name = PRESET_STANDARD
        spec = PRESETS[name]
    
    # Set preset field (new way)
    con.preset = name
    # Clear legacy kind field for new workspaces
    con.kind = None
    
    if spec["empty_targets"]:
        con.targets = []
    con.pipeline.digest.enabled = spec["digest_enabled"]
    con.review_input = spec["review_input"]


def effective_preset(ws) -> str:
    """运行时 preset: yaml preset 优先，回退 kind，再看 slug/topic 是否为「总结」。"""
    con = ws.constitution
    return resolve_preset(
        getattr(con, "preset", None),
        getattr(con, "kind", None),
        con.topic if hasattr(con, "topic") else ws.root.name
    )


def effective_kind(ws) -> str:
    """Legacy function for backward compatibility."""
    return effective_preset(ws)


def is_journal_workspace(ws) -> bool:
    return effective_preset(ws) == PRESET_JOURNAL


def stage_enabled(ws, stage: str) -> bool:
    """journal:digest 开(含 leftover yaml 关)、compose 关。课题仓读 yaml / 活 target。"""
    journal = is_journal_workspace(ws)
    if stage == "digest":
        if journal:
            return True
        return bool(getattr(ws.constitution.pipeline.digest, "enabled", True))
    if stage == "compose":
        if journal:
            return False
        return bool(ws.constitution.live_targets())
    raise ValueError(f"unknown agent stage:{stage}")
