"""#182 统一知识候选审核队列。"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Callable

import yaml
from pydantic import BaseModel, ConfigDict, Field

from kairo.knowledge import (
    KnowledgeAlias,
    KnowledgeDocument,
    KnowledgeEntry,
    KnowledgeError,
    KnowledgeSource,
    effective_entries,
    load_global,
    load_workspace,
    new_entry,
    normalize_term,
    save_global,
    save_workspace,
    validate_entries,
)
from kairo.knowledge_matcher import KnowledgeMatcher


OPEN = frozenset({"pending", "pending_global"})


class KnowledgeCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    aliases: list[KnowledgeAlias] = Field(default_factory=list)
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    source_kind: str  # digest | compose
    path: str
    quote: str
    content_hash: str
    fingerprint: str
    status: str = "pending"
    merged_into: str = ""
    reject_reason: str = ""


class KnowledgeReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[KnowledgeCandidate] = Field(default_factory=list)
    extract_errors: dict[str, str] = Field(default_factory=dict)


Extractor = Callable[[str, list[KnowledgeEntry], str], list[dict]]


def review_path(workspace_root: Path) -> Path:
    return Path(workspace_root) / ".kairo" / "knowledge_review.yaml"


def _atomic_write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise KnowledgeError(f"保存审核队列失败:{exc}", path=path) from exc


def load_review(workspace_root: Path) -> KnowledgeReview:
    path = review_path(workspace_root)
    if not path.is_file():
        return KnowledgeReview()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return KnowledgeReview.model_validate(data)
    except (yaml.YAMLError, ValueError) as exc:
        raise KnowledgeError(f"审核队列无法解析:{exc}", path=path) from exc


def save_review(workspace_root: Path, review: KnowledgeReview) -> None:
    _atomic_write(review_path(workspace_root), review.model_dump())


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _fingerprint(source_kind: str, path: str, title: str, quote: str) -> str:
    return _hash(f"{source_kind}\n{path}\n{normalize_term(title)}\n{quote.strip()}")


def invalidate_stale(workspace_root: Path) -> KnowledgeReview:
    review = load_review(workspace_root)
    changed = False
    root = Path(workspace_root)
    for index, candidate in enumerate(review.candidates):
        if candidate.status not in OPEN:
            continue
        source = root / candidate.path
        alive = source.is_file()
        if alive:
            text = source.read_text(encoding="utf-8")
            alive = candidate.quote.strip() in text or _hash(text) == candidate.content_hash
        if not alive:
            review.candidates[index] = candidate.model_copy(update={"status": "stale"})
            changed = True
    if changed:
        save_review(workspace_root, review)
    return review


def open_candidates(workspace_root: Path) -> list[KnowledgeCandidate]:
    return [c for c in invalidate_stale(workspace_root).candidates if c.status in OPEN]


def todo_count(workspace_root: Path) -> int:
    review = load_review(workspace_root)
    return sum(c.status in OPEN for c in review.candidates) + len(review.extract_errors)


def ingest_candidates(
    workspace_root: Path,
    *,
    source_kind: str,
    path: str,
    source_text: str,
    drafts: list[dict],
    matcher: KnowledgeMatcher | None = None,
) -> KnowledgeReview:
    if source_kind not in {"digest", "compose"}:
        raise KnowledgeError(f"未知候选来源:{source_kind}")
    review = invalidate_stale(workspace_root)
    review.extract_errors.pop(path, None)
    existing = {c.fingerprint for c in review.candidates}
    source_hash = _hash(source_text)
    for draft in drafts:
        title = str(draft.get("title", draft.get("name", ""))).strip()
        quote = str(draft.get("quote", "")).strip()
        if not title or not quote or quote not in source_text:
            continue
        fp = _fingerprint(source_kind, path, title, quote)
        if fp in existing:
            continue
        aliases = [
            KnowledgeAlias(value=str(value).strip())
            for value in draft.get("aliases", draft.get("aka", [])) or []
            if str(value).strip()
        ]
        candidate = KnowledgeCandidate(
            id="kc-" + fp[:20],
            title=title,
            aliases=aliases,
            description=str(draft.get("description", draft.get("note", ""))).strip(),
            tags=[str(x).strip() for x in draft.get("tags", []) or [] if str(x).strip()],
            source_kind=source_kind,
            path=path,
            quote=quote,
            content_hash=source_hash,
            fingerprint=fp,
        )
        # 建议只影响 UI，不自动确认或覆盖。
        if matcher is not None:
            _ = matcher.suggest([candidate.title, *(a.value for a in aliases)])
        review.candidates.append(candidate)
        existing.add(fp)
    save_review(workspace_root, review)
    return review


def mark_extract_error(workspace_root: Path, path: str, message: str) -> None:
    review = load_review(workspace_root)
    review.extract_errors[path] = message
    save_review(workspace_root, review)


def parse_extract_yaml(text: str) -> list[dict]:
    match = re.search(r"(\[[\s\S]*\]|entries:\s*[\s\S]*)", text)
    data = yaml.safe_load(match.group(1) if match else text)
    if data is None:
        return []
    if isinstance(data, dict):
        data = data.get("entries", [])
    if not isinstance(data, list):
        raise KnowledgeError("知识候选提取结果必须是列表")
    return [item for item in data if isinstance(item, dict)]


_PERSONA = """从已完成产物中提取值得人工审核的领域知识候选。
只能提出原文有直接证据的标题、简短说明和别名；quote 必须逐字出现在原文。
输出 YAML 列表，每项仅含 title、description、aliases、tags、quote；无候选时输出 []。
不要自动确认、不要猜测、不要输出 Markdown 围栏。"""


def provider_extractor(provider) -> Extractor:
    def extract(text: str, entries: list[KnowledgeEntry], path: str) -> list[dict]:
        from kairo.rules import _run_agent

        known = yaml.safe_dump(
            [{"title": e.title, "aliases": [a.value for a in e.aliases]} for e in entries],
            allow_unicode=True,
            sort_keys=False,
        )
        response = _run_agent(
            provider,
            _PERSONA,
            f"来源:{path}\n\n已确认知识（只读，不重复提出）：\n{known or '[]'}\n\n正文：\n{text}",
            "knowledge-candidates.yaml",
        )
        return parse_extract_yaml(response)

    return extract


def extract_after_success(
    workspace_root: Path,
    serve_root: Path,
    *,
    source_kind: str,
    path: str,
    text: str,
    provider=None,
    extractor: Extractor | None = None,
) -> None:
    try:
        matcher = KnowledgeMatcher(effective_entries(serve_root, workspace_root))
        drafts = (extractor or provider_extractor(provider))(text, list(matcher.entries), path) if (extractor or provider) else []
        ingest_candidates(workspace_root, source_kind=source_kind, path=path, source_text=text, drafts=drafts, matcher=matcher)
    except Exception as exc:
        mark_extract_error(workspace_root, path, str(exc))


def _candidate(workspace_root: Path, candidate_id: str) -> tuple[KnowledgeReview, int, KnowledgeCandidate]:
    review = invalidate_stale(workspace_root)
    for index, candidate in enumerate(review.candidates):
        if candidate.id == candidate_id:
            return review, index, candidate
    raise KnowledgeError(f"知识候选不存在:{candidate_id}")


def accept_workspace(workspace_root: Path, candidate_id: str) -> KnowledgeEntry:
    review, index, candidate = _candidate(workspace_root, candidate_id)
    if candidate.status != "pending":
        raise KnowledgeError(f"候选不可采纳:{candidate.status}")
    document, _ = load_workspace(workspace_root)
    entry = new_entry(
        title=candidate.title,
        scope="workspace",
        aliases=candidate.aliases,
        description=candidate.description,
        tags=candidate.tags,
        sources=[KnowledgeSource(kind="digest" if candidate.source_kind == "digest" else "understanding", path=candidate.path, quote=candidate.quote, content_hash=candidate.content_hash)],
    )
    validate_entries([*document.entries, entry], scope="workspace")
    document.entries.append(entry)
    save_workspace(workspace_root, document)
    review.candidates[index] = candidate.model_copy(update={"status": "accepted", "merged_into": entry.id})
    save_review(workspace_root, review)
    return entry


def ignore(workspace_root: Path, candidate_id: str) -> None:
    review, index, candidate = _candidate(workspace_root, candidate_id)
    if candidate.status != "pending":
        raise KnowledgeError(f"候选不可忽略:{candidate.status}")
    review.candidates[index] = candidate.model_copy(update={"status": "ignored"})
    save_review(workspace_root, review)


def promote(workspace_root: Path, candidate_id: str) -> None:
    review, index, candidate = _candidate(workspace_root, candidate_id)
    if candidate.status != "pending":
        raise KnowledgeError(f"候选不可提升:{candidate.status}")
    review.candidates[index] = candidate.model_copy(update={"status": "pending_global"})
    save_review(workspace_root, review)


def accept_global(serve_root: Path, workspace_root: Path, candidate_id: str) -> KnowledgeEntry:
    review, index, candidate = _candidate(workspace_root, candidate_id)
    if candidate.status != "pending_global":
        raise KnowledgeError(f"候选不在全局待审核:{candidate.status}")
    document, _ = load_global(serve_root)
    entry = new_entry(
        title=candidate.title,
        scope="global",
        aliases=candidate.aliases,
        description=candidate.description,
        tags=candidate.tags,
        sources=[KnowledgeSource(kind="digest" if candidate.source_kind == "digest" else "understanding", path=candidate.path, quote=candidate.quote, content_hash=candidate.content_hash)],
    )
    validate_entries([*document.entries, entry], scope="global")
    document.entries.append(entry)
    save_global(serve_root, document)
    review.candidates[index] = candidate.model_copy(update={"status": "accepted", "merged_into": entry.id})
    save_review(workspace_root, review)
    return entry


def reject_global(workspace_root: Path, candidate_id: str, reason: str) -> None:
    review, index, candidate = _candidate(workspace_root, candidate_id)
    if candidate.status != "pending_global":
        raise KnowledgeError(f"候选不在全局待审核:{candidate.status}")
    review.candidates[index] = candidate.model_copy(update={"status": "rejected", "reject_reason": reason.strip()})
    save_review(workspace_root, review)


def merge_workspace(workspace_root: Path, candidate_id: str, entry_id: str) -> None:
    review, index, candidate = _candidate(workspace_root, candidate_id)
    if candidate.status != "pending":
        raise KnowledgeError(f"候选不可合并:{candidate.status}")
    document, _ = load_workspace(workspace_root)
    target = next((entry for entry in document.entries if entry.id == entry_id), None)
    if target is None:
        raise KnowledgeError(f"本地知识条目不存在:{entry_id}")
    aliases = list(target.aliases)
    seen = {normalize_term(alias.value) for alias in aliases}
    for value in [candidate.title, *(alias.value for alias in candidate.aliases)]:
        if normalize_term(value) not in seen and normalize_term(value) != normalize_term(target.title):
            aliases.append(KnowledgeAlias(value=value))
            seen.add(normalize_term(value))
    sources = [*target.sources, KnowledgeSource(kind="digest" if candidate.source_kind == "digest" else "understanding", path=candidate.path, quote=candidate.quote, content_hash=candidate.content_hash)]
    replacement = target.model_copy(update={"aliases": aliases, "sources": sources})
    document.entries = [replacement if entry.id == entry_id else entry for entry in document.entries]
    validate_entries(document.entries, scope="workspace")
    save_workspace(workspace_root, document)
    review.candidates[index] = candidate.model_copy(update={"status": "merged", "merged_into": entry_id})
    save_review(workspace_root, review)


def merge_global(serve_root: Path, workspace_root: Path, candidate_id: str, entry_id: str) -> None:
    review, index, candidate = _candidate(workspace_root, candidate_id)
    if candidate.status != "pending_global":
        raise KnowledgeError(f"候选不可合并:{candidate.status}")
    document, _ = load_global(serve_root)
    target = next((entry for entry in document.entries if entry.id == entry_id), None)
    if target is None:
        raise KnowledgeError(f"公共知识条目不存在:{entry_id}")
    aliases = list(target.aliases)
    seen = {normalize_term(alias.value) for alias in aliases}
    for value in [candidate.title, *(alias.value for alias in candidate.aliases)]:
        if normalize_term(value) not in seen and normalize_term(value) != normalize_term(target.title):
            aliases.append(KnowledgeAlias(value=value))
            seen.add(normalize_term(value))
    replacement = target.model_copy(update={"aliases": aliases})
    document.entries = [replacement if entry.id == entry_id else entry for entry in document.entries]
    validate_entries(document.entries, scope="global")
    save_global(serve_root, document)
    review.candidates[index] = candidate.model_copy(update={"status": "merged", "merged_into": entry_id})
    save_review(workspace_root, review)


def set_obsolete(workspace_root: Path, entry_id: str) -> None:
    document, _ = load_workspace(workspace_root)
    found = False
    entries: list[KnowledgeEntry] = []
    for entry in document.entries:
        if entry.id == entry_id:
            entries.append(entry.model_copy(update={"status": "obsolete"}))
            found = True
        else:
            entries.append(entry)
    if not found:
        raise KnowledgeError(f"本地知识条目不存在:{entry_id}")
    document.entries = entries
    save_workspace(workspace_root, document)


def update_workspace_entry(
    workspace_root: Path, entry_id: str, *, title: str, description: str, aliases: list[KnowledgeAlias], tags: list[str]
) -> None:
    document, _ = load_workspace(workspace_root)
    found = False
    entries: list[KnowledgeEntry] = []
    for entry in document.entries:
        if entry.id == entry_id:
            entries.append(entry.model_copy(update={"title": title.strip(), "description": description.strip(), "aliases": aliases, "tags": tags}))
            found = True
        else:
            entries.append(entry)
    if not found:
        raise KnowledgeError(f"本地知识条目不存在:{entry_id}")
    validate_entries(entries, scope="workspace")
    document.entries = entries
    save_workspace(workspace_root, document)
