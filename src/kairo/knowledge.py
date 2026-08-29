"""#182 的权威知识条目仓储。

存储保持本地 YAML：root 沿用 ``glossary.yaml`` 的兼容路径，workspace 写入
``constitution.yaml: knowledge``。旧真名册只在读取/迁移时转换，写入后不再形成
第二事实源。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
import uuid
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class KnowledgeError(ValueError):
    """知识配置、范围或持久化操作非法。"""

    def __init__(self, message: str, *, path: Path | None = None):
        self.path = path
        super().__init__(f"{path}: {message}" if path else message)


def normalize_term(value: str) -> str:
    """匹配及冲突检测使用的稳定规范化形式。"""
    normalized = unicodedata.normalize("NFKC", value)
    normalized = re.sub(r"\s+", " ", normalized.strip())
    # 契约只承诺 ASCII 大小写无关；Unicode casefold 会意外改变非英语术语。
    return "".join(ch.lower() if ch.isascii() else ch for ch in normalized)


def _stable_legacy_id(scope: str, title: str) -> str:
    raw = f"legacy:{scope}:{normalize_term(title)}".encode()
    return "ke-" + hashlib.sha256(raw).hexdigest()[:20]


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class KnowledgeAlias(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    auto_match: bool = True


class KnowledgeSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str  # reference | digest | understanding
    path: str
    quote: str = ""
    content_hash: str = ""
    workspace_slug: str = ""


class KnowledgeEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    aliases: list[KnowledgeAlias] = Field(default_factory=list)
    description: str = ""
    status: str = "confirmed"  # pending | confirmed | obsolete
    scope: str
    tags: list[str] = Field(default_factory=list)
    sources: list[KnowledgeSource] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


class KnowledgeDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 2
    entries: list[KnowledgeEntry] = Field(default_factory=list)


def _validate_source(source: KnowledgeSource) -> None:
    if source.kind not in {"reference", "digest", "understanding"}:
        raise KnowledgeError(f"未知出处类型:{source.kind}")
    p = Path(source.path)
    if not source.path or p.is_absolute() or ".." in p.parts:
        raise KnowledgeError(f"出处 path 必须是安全相对路径:{source.path!r}")
    if source.content_hash and not re.fullmatch(r"[a-f0-9]{64}", source.content_hash):
        raise KnowledgeError("出处 content_hash 必须是 SHA-256")
    if source.workspace_slug and (source.workspace_slug in {".", ".."} or "/" in source.workspace_slug or "\\" in source.workspace_slug or "\x00" in source.workspace_slug):
        raise KnowledgeError(f"出处 workspace_slug 非法:{source.workspace_slug!r}")


def validate_entries(entries: list[KnowledgeEntry], *, scope: str) -> None:
    if scope not in {"workspace", "global"}:
        raise KnowledgeError(f"未知知识范围:{scope}")
    ids: set[str] = set()
    titles: set[str] = set()
    owners: dict[str, str] = {}
    for entry in entries:
        if entry.scope != scope:
            raise KnowledgeError(f"条目 {entry.id} scope 必须是 {scope}")
        if not re.fullmatch(r"ke-[a-zA-Z0-9-]+", entry.id):
            raise KnowledgeError(f"条目 id 非法:{entry.id!r}")
        if entry.id in ids:
            raise KnowledgeError(f"条目 id 重复:{entry.id}")
        ids.add(entry.id)
        title = normalize_term(entry.title)
        if not title:
            raise KnowledgeError("条目 title 不能为空")
        if title in titles:
            raise KnowledgeError(f"规范标题重复:{entry.title!r}")
        titles.add(title)
        if entry.status not in {"pending", "confirmed", "obsolete"}:
            raise KnowledgeError(f"条目状态非法:{entry.status}")
        for label, value in (("created_at", entry.created_at), ("updated_at", entry.updated_at)):
            if value:
                try:
                    if datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None:
                        raise ValueError
                except ValueError as exc:
                    raise KnowledgeError(f"条目 {label} 必须是带时区 ISO-8601") from exc
        seen_aliases: set[str] = set()
        for alias in entry.aliases:
            term = normalize_term(alias.value)
            if not term:
                raise KnowledgeError(f"条目 {entry.title!r} 含空别名")
            if term == title or term in seen_aliases:
                raise KnowledgeError(f"条目 {entry.title!r} 别名重复或等于标题")
            seen_aliases.add(term)
            prev = owners.get(term)
            if prev and prev != entry.id:
                raise KnowledgeError(f"别名 {alias.value!r} 指向多个条目")
            owners[term] = entry.id
        for source in entry.sources:
            _validate_source(source)

    # 标题不能同时作为他人的别名。
    for entry in entries:
        for alias in entry.aliases:
            if normalize_term(alias.value) in titles:
                raise KnowledgeError(f"别名 {alias.value!r} 与规范标题冲突")


def _legacy_entries(items: list[dict], *, scope: str) -> list[KnowledgeEntry]:
    out: list[KnowledgeEntry] = []
    for item in items:
        if not isinstance(item, dict):
            raise KnowledgeError("旧真名册条目必须是 mapping")
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise KnowledgeError("旧真名册条目缺少有效 name")
        aka = item.get("aka") or []
        if not isinstance(aka, list) or not all(isinstance(x, str) for x in aka):
            raise KnowledgeError(f"旧条目 {name!r} 的 aka 必须是字符串列表")
        tags = item.get("tags") or []
        if not isinstance(tags, list) or not all(isinstance(x, str) for x in tags):
            raise KnowledgeError(f"旧条目 {name!r} 的 tags 必须是字符串列表")
        out.append(
            KnowledgeEntry(
                id=_stable_legacy_id(scope, name),
                title=name.strip(),
                aliases=[KnowledgeAlias(value=x.strip()) for x in aka if x.strip()],
                description=str(item.get("note") or "").strip(),
                status="confirmed",
                scope=scope,
                tags=[x.strip() for x in tags if x.strip()],
            )
        )
    validate_entries(out, scope=scope)
    return out


def _parse_document(data, *, scope: str, path: Path) -> tuple[KnowledgeDocument, bool]:
    """返回文档和是否为需迁移的 legacy 格式。"""
    if data is None:
        return KnowledgeDocument(), False
    if isinstance(data, list):
        return KnowledgeDocument(entries=_legacy_entries(data, scope=scope)), True
    if not isinstance(data, dict):
        raise KnowledgeError("知识文档必须是 mapping 或旧列表", path=path)
    if data.get("version") == 2:
        try:
            document = KnowledgeDocument.model_validate(data)
        except Exception as exc:
            raise KnowledgeError(f"知识文档非法:{exc}", path=path) from exc
        validate_entries(document.entries, scope=scope)
        return document, False
    # root glossary.yaml 旧格式允许 {entries:[old glossary]} / {glossary:[...]}。
    if "entries" in data or "glossary" in data:
        items = data.get("entries", data.get("glossary"))
        if not isinstance(items, list):
            raise KnowledgeError("旧 entries/glossary 必须是列表", path=path)
        return KnowledgeDocument(entries=_legacy_entries(items, scope=scope)), True
    raise KnowledgeError("知识文档缺少 version: 2", path=path)


def _read_yaml(path: Path):
    if not path.is_file():
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise KnowledgeError(f"YAML 无法解析:{exc}", path=path) from exc


def _atomic_write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        temp.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        os.replace(temp, path)
    except Exception as exc:
        temp.unlink(missing_ok=True)
        raise KnowledgeError(f"保存失败:{exc}", path=path) from exc


def global_path(serve_root: Path) -> Path:
    return Path(serve_root) / "glossary.yaml"


def load_global(serve_root: Path) -> tuple[KnowledgeDocument, bool]:
    path = global_path(serve_root)
    return _parse_document(_read_yaml(path), scope="global", path=path)


def load_workspace(workspace_root: Path) -> tuple[KnowledgeDocument, bool]:
    path = Path(workspace_root) / "constitution.yaml"
    raw = _read_yaml(path)
    if raw is None:
        raise KnowledgeError("constitution.yaml 不存在", path=path)
    if not isinstance(raw, dict):
        raise KnowledgeError("constitution 顶层必须是 mapping", path=path)
    if "knowledge" in raw:
        return _parse_document(raw["knowledge"], scope="workspace", path=path)
    if "glossary" not in raw or raw["glossary"] is None:
        return KnowledgeDocument(), False
    legacy = raw["glossary"]
    if not isinstance(legacy, list):
        raise KnowledgeError("constitution.glossary 必须是列表", path=path)
    return KnowledgeDocument(entries=_legacy_entries(legacy, scope="workspace")), True


def save_global(serve_root: Path, document: KnowledgeDocument) -> None:
    validate_entries(document.entries, scope="global")
    _atomic_write(global_path(serve_root), document.model_dump())


def save_workspace(workspace_root: Path, document: KnowledgeDocument) -> None:
    validate_entries(document.entries, scope="workspace")
    path = Path(workspace_root) / "constitution.yaml"
    raw = _read_yaml(path)
    if not isinstance(raw, dict):
        raise KnowledgeError("constitution 顶层必须是 mapping", path=path)
    raw["knowledge"] = document.model_dump()
    # 此次原子写完成后，legacy 绝不再参与读取。
    raw.pop("glossary", None)
    _atomic_write(path, raw)


def migrate_workspace(workspace_root: Path) -> KnowledgeDocument:
    document, legacy = load_workspace(workspace_root)
    if legacy:
        save_workspace(workspace_root, document)
    return document


def migrate_global(serve_root: Path) -> KnowledgeDocument:
    document, legacy = load_global(serve_root)
    if legacy:
        save_global(serve_root, document)
    return document


def effective_entries(serve_root: Path, workspace_root: Path) -> list[KnowledgeEntry]:
    global_doc, _ = load_global(serve_root)
    workspace_doc, _ = load_workspace(workspace_root)
    by_title: dict[str, KnowledgeEntry] = {}
    order: list[str] = []
    for entry in global_doc.entries:
        key = normalize_term(entry.title)
        by_title[key] = entry
        order.append(key)
    for entry in workspace_doc.entries:
        key = normalize_term(entry.title)
        if key not in by_title:
            order.append(key)
        by_title[key] = entry
    entries = [by_title[key] for key in order]
    # 跨 authority 的同词由 matcher 局部报告 ambiguity；不能因此关闭整个知识上下文。
    return entries


def semantic_hash(entries: list[KnowledgeEntry]) -> str:
    payload = [
        {
            "id": entry.id,
            "title": entry.title,
            "aliases": [a.model_dump() for a in entry.aliases],
            "description": entry.description,
            "status": entry.status,
            "scope": entry.scope,
            "tags": sorted(entry.tags),
        }
        for entry in entries
        if entry.status == "confirmed"
    ]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def current_hash(serve_root: Path, workspace_root: Path) -> str:
    return semantic_hash(effective_entries(serve_root, workspace_root))


def new_entry(
    *,
    title: str,
    scope: str,
    aliases: list[KnowledgeAlias] | None = None,
    description: str = "",
    status: str = "confirmed",
    tags: list[str] | None = None,
    sources: list[KnowledgeSource] | None = None,
) -> KnowledgeEntry:
    now = _now()
    return KnowledgeEntry(
        id="ke-" + str(uuid.uuid4()),
        title=title.strip(),
        aliases=aliases or [],
        description=description.strip(),
        status=status,
        scope=scope,
        tags=tags or [],
        sources=sources or [],
        created_at=now,
        updated_at=now,
    )
