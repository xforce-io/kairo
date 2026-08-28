"""共享 + workspace 真名册合并(#71)。

machine (~/.config/kairo/glossary.yaml) → root (<serve-root|ws.parent>/glossary.yaml)
→ workspace constitution.glossary；同名后者覆盖。

#162: 已存在但非法的文件整表拒绝；写入先校验再原子保存。
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from kairo.models import GlossaryEntry

ALLOWED_SCOPES = frozenset({"workspace", "shared"})


class GlossaryError(ValueError):
    """真名册配置或请求非法。message 含路径(若有),可直接展示。"""

    def __init__(self, message: str, *, path: Path | None = None):
        self.path = path
        if path is not None:
            message = f"{path}: {message}"
        super().__init__(message)


def machine_glossary_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "kairo" / "glossary.yaml"


def root_glossary_path(root: Path) -> Path:
    return Path(root) / "glossary.yaml"


def constitution_path(ws_root: Path) -> Path:
    return Path(ws_root) / "constitution.yaml"


def parse_scope(scope: str | None, *, default: str = "workspace") -> str:
    """未提供 → 公开默认 workspace;显式非法值拒绝。"""
    if scope is None:
        return default
    value = scope.strip()
    if value not in ALLOWED_SCOPES:
        raise GlossaryError(f"未知 scope:{scope!r}(workspace|shared)")
    return value


def _parse_entry_list(items: list, *, path: Path | None = None) -> list[GlossaryEntry]:
    out: list[GlossaryEntry] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise GlossaryError(f"条目[{i}] 必须是 mapping", path=path)
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise GlossaryError(f"条目[{i}] 缺少有效 name", path=path)
        try:
            out.append(GlossaryEntry.model_validate(item))
        except Exception as e:
            raise GlossaryError(f"条目[{i}] 非法:{e}", path=path) from e
    return out


def _parse_glossary_doc(data, *, path: Path | None = None) -> list[GlossaryEntry]:
    if data is None:
        return []
    if isinstance(data, dict):
        if "entries" in data:
            items = data["entries"]
        elif "glossary" in data:
            items = data["glossary"]
        else:
            raise GlossaryError("顶层必须是列表或 {entries|glossary: [...]}", path=path)
        if not isinstance(items, list):
            raise GlossaryError("entries/glossary 必须是列表", path=path)
        return _parse_entry_list(items, path=path)
    if not isinstance(data, list):
        raise GlossaryError("顶层必须是列表或 {entries|glossary: [...]}", path=path)
    return _parse_entry_list(data, path=path)


def load_glossary_file(path: Path) -> list[GlossaryEntry]:
    if not path.is_file():
        return []
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise GlossaryError(f"YAML 无法解析:{e}", path=path) from e
    return _parse_glossary_doc(data, path=path)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def save_glossary_file(path: Path, entries: list[GlossaryEntry]) -> None:
    payload = [e.model_dump() for e in entries]
    _atomic_write_text(
        path, yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    )


def load_workspace_glossary(ws_root: Path) -> list[GlossaryEntry]:
    path = constitution_path(ws_root)
    if not path.is_file():
        return []
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise GlossaryError(f"YAML 无法解析:{e}", path=path) from e
    if data is None:
        return []
    if not isinstance(data, dict):
        raise GlossaryError("constitution 顶层必须是 mapping", path=path)
    if "glossary" not in data:
        return []
    items = data["glossary"]
    if items is None:
        return []
    if not isinstance(items, list):
        raise GlossaryError("glossary 必须是列表", path=path)
    return _parse_entry_list(items, path=path)


def write_workspace_glossary(ws_root: Path, entries: list[GlossaryEntry]) -> None:
    path = constitution_path(ws_root)
    if not path.is_file():
        raise GlossaryError("constitution.yaml 不存在", path=path)
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise GlossaryError(f"YAML 无法解析:{e}", path=path) from e
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise GlossaryError("constitution 顶层必须是 mapping", path=path)
    data["glossary"] = [e.model_dump() for e in entries]
    _atomic_write_text(
        path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    )


def merge_glossary(*layers: list[GlossaryEntry]) -> list[GlossaryEntry]:
    """按层顺序合并,同名后者覆盖;保持首次出现顺序,覆盖时更新值保留位置。"""
    order: list[str] = []
    by_name: dict[str, GlossaryEntry] = {}
    for layer in layers:
        for e in layer:
            if e.name not in by_name:
                order.append(e.name)
            by_name[e.name] = e
    return [by_name[n] for n in order]


def format_glossary_reference(entries: list[GlossaryEntry]) -> str:
    """与 Constitution.glossary_reference 同构;空表 → \"\"。"""
    if not entries:
        return ""
    lines = []
    for e in entries:
        line = f"- {e.name}"
        if e.note:
            line += f" —— {e.note}"
        if e.aka:
            line += f"(亦作:{'/'.join(e.aka)})"
        if e.tags:
            line += f" [{','.join(e.tags)}]"
        lines.append(line)
    return (
        "\n\n[领域真名册](权威参考;下列为本领域规范名,产出时一律用规范名,"
        "勿用变体/别名;遇含糊提及按此锚定)\n" + "\n".join(lines)
    )


def resolve_shared_layers(
    ws_root: Path, *, serve_root: Path | None = None
) -> tuple[list[GlossaryEntry], list[GlossaryEntry]]:
    """返回 (machine_entries, root_entries)。root = serve_root 或 ws 父目录。"""
    machine = load_glossary_file(machine_glossary_path())
    root_path = root_glossary_path(serve_root) if serve_root else root_glossary_path(ws_root.parent)
    root_entries = load_glossary_file(root_path)
    return machine, root_entries


def merged_glossary_entries(
    workspace_entries: list[GlossaryEntry],
    ws_root: Path,
    *,
    serve_root: Path | None = None,
) -> list[GlossaryEntry]:
    machine, root = resolve_shared_layers(ws_root, serve_root=serve_root)
    return merge_glossary(machine, root, workspace_entries)


def add_entry(
    entries: list[GlossaryEntry], name: str, note: str = "", aka: list[str] | None = None,
    tags: list[str] | None = None,
) -> list[GlossaryEntry]:
    name = name.strip()
    if not name:
        raise ValueError("name 不能为空")
    if any(e.name == name for e in entries):
        raise ValueError(f"真名册已有同名条目:{name}")
    aka_list = [a.strip() for a in (aka or []) if a and a.strip()]
    tag_list = [t.strip() for t in (tags or []) if t and t.strip()]
    entries = list(entries)
    entries.append(GlossaryEntry(name=name, note=(note or "").strip(), aka=aka_list, tags=tag_list))
    return entries


def remove_entry(entries: list[GlossaryEntry], index: int) -> list[GlossaryEntry]:
    if not 0 <= index < len(entries):
        raise IndexError(f"glossary 索引越界:{index}")
    entries = list(entries)
    entries.pop(index)
    return entries
