"""#182 统一知识候选审核队列。"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

import yaml
from pydantic import BaseModel, ConfigDict, Field

from kairo.knowledge import (
    KnowledgeAlias,
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
_CANDIDATE_STATUSES = frozenset({"pending", "pending_global", "accepted", "merged", "ignored", "stale", "rejected_global"})


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


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
    suggestion: dict[str, str] = Field(default_factory=dict)
    entry_id: str = ""
    updated_at: str = ""


class KnowledgeReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[KnowledgeCandidate] = Field(default_factory=list)
    extract_errors: dict[str, str] = Field(default_factory=dict)


Extractor = Callable[[str, list[KnowledgeEntry], str], list[dict]]


def review_path(workspace_root: Path) -> Path:
    return Path(workspace_root) / ".kairo" / "knowledge_review.yaml"


def _legacy_review_path(workspace_root: Path) -> Path:
    return Path(workspace_root) / ".kairo" / "glossary_review.yaml"


def _safe_path(path: str) -> None:
    value = Path(path)
    if not path or value.is_absolute() or ".." in value.parts:
        raise KnowledgeError(f"候选 path 必须是工作区内相对路径:{path!r}")


def _validate_candidate(candidate: KnowledgeCandidate) -> None:
    if not re.fullmatch(r"kc-[a-f0-9]{20}", candidate.id):
        raise KnowledgeError(f"候选 id 非法:{candidate.id!r}")
    if candidate.source_kind not in {"digest", "compose"}:
        raise KnowledgeError(f"候选来源非法:{candidate.source_kind}")
    if candidate.status not in _CANDIDATE_STATUSES:
        raise KnowledgeError(f"候选状态非法:{candidate.status}")
    _safe_path(candidate.path)
    if not re.fullmatch(r"[a-f0-9]{64}", candidate.content_hash):
        raise KnowledgeError("候选 content_hash 必须是 SHA-256")
    if not re.fullmatch(r"[a-f0-9]{64}", candidate.fingerprint):
        raise KnowledgeError("候选 fingerprint 必须是 SHA-256")
    if not candidate.title.strip() or not candidate.quote.strip():
        raise KnowledgeError("候选 title 与 quote 不能为空")
    if candidate.entry_id and not re.fullmatch(r"ke-[a-zA-Z0-9-]+", candidate.entry_id):
        raise KnowledgeError(f"候选 entry_id 非法:{candidate.entry_id!r}")
    if candidate.updated_at:
        try:
            if datetime.fromisoformat(candidate.updated_at.replace("Z", "+00:00")).tzinfo is None:
                raise ValueError
        except ValueError as exc:
            raise KnowledgeError("候选 updated_at 必须是带时区 ISO-8601") from exc


def _validate_review(review: KnowledgeReview) -> None:
    ids: set[str] = set()
    for candidate in review.candidates:
        _validate_candidate(candidate)
        if candidate.id in ids:
            raise KnowledgeError(f"候选 id 重复:{candidate.id}")
        ids.add(candidate.id)


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
        legacy_path = _legacy_review_path(workspace_root)
        if not legacy_path.is_file():
            return KnowledgeReview()
        # 一次性、原子地把 #165 队列迁入唯一 knowledge_review，迁完即停止旧提取。
        try:
            raw = yaml.safe_load(legacy_path.read_text(encoding="utf-8")) or {}
            migrated: list[KnowledgeCandidate] = []
            for old in raw.get("candidates", []):
                if not isinstance(old, dict):
                    continue
                ref_id = str(old.get("ref_id", "")).strip()
                candidate_path = f"references/{ref_id}/digest.md"
                title = str(old.get("name", "")).strip()
                quote = str(old.get("quote", "")).strip()
                fp = str(old.get("fingerprint") or _fingerprint("digest", candidate_path, title, quote))
                status = {"pending_root": "pending_global", "root_rejected": "rejected_global"}.get(str(old.get("status", "pending")), str(old.get("status", "pending")))
                text_path = Path(workspace_root) / candidate_path
                legacy_hash = str(old.get("digest_hash") or "")
                content_hash = legacy_hash if re.fullmatch(r"[a-f0-9]{64}", legacy_hash) else (_hash(text_path.read_text(encoding="utf-8")) if text_path.is_file() else _hash(""))
                migrated.append(KnowledgeCandidate(id="kc-" + fp[:20], title=title, aliases=[KnowledgeAlias(value=str(x)) for x in old.get("aka", []) if str(x).strip()], description=str(old.get("note", "")), source_kind="digest", path=candidate_path, quote=quote or "[旧候选缺少引文]", content_hash=content_hash, fingerprint=fp, status=status if status in _CANDIDATE_STATUSES else "stale", merged_into=str(old.get("merged_into", "")), reject_reason=str(old.get("reject_reason", "")), updated_at=_now()))
            review = KnowledgeReview(candidates=migrated, extract_errors={str(k): str(v) for k, v in raw.get("extract_errors", {}).items()})
            _validate_review(review)
            save_review(workspace_root, review)
            legacy_path.replace(legacy_path.with_suffix(".yaml.migrated"))
            return review
        except (OSError, yaml.YAMLError, ValueError) as exc:
            raise KnowledgeError(f"旧审核队列迁移失败:{exc}", path=legacy_path) from exc
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        review = KnowledgeReview.model_validate(data)
        _validate_review(review)
        # 运行已先创建 v2 文件时，仍把遗留 #165 候选一次性并入，不能让它成为孤岛。
        legacy_path = _legacy_review_path(workspace_root)
        if legacy_path.is_file():
            legacy = yaml.safe_load(legacy_path.read_text(encoding="utf-8")) or {}
            known = {candidate.fingerprint for candidate in review.candidates}
            for old in legacy.get("candidates", []):
                if not isinstance(old, dict):
                    continue
                ref_id, title, quote = str(old.get("ref_id", "")).strip(), str(old.get("name", "")).strip(), str(old.get("quote", "")).strip()
                candidate_path = f"references/{ref_id}/digest.md"
                fp = str(old.get("fingerprint") or _fingerprint("digest", candidate_path, title, quote))
                if fp in known or not title or not quote:
                    continue
                source = Path(workspace_root) / candidate_path
                review.candidates.append(KnowledgeCandidate(id="kc-" + fp[:20], title=title, aliases=[KnowledgeAlias(value=str(x)) for x in old.get("aka", []) if str(x).strip()], description=str(old.get("note", "")), source_kind="digest", path=candidate_path, quote=quote, content_hash=_hash(source.read_text(encoding="utf-8")) if source.is_file() else _hash(""), fingerprint=fp, status={"pending_root": "pending_global", "root_rejected": "rejected_global"}.get(str(old.get("status", "pending")), str(old.get("status", "pending"))), updated_at=_now()))
                known.add(fp)
            _validate_review(review)
            save_review(workspace_root, review)
            legacy_path.replace(legacy_path.with_suffix(".yaml.migrated"))
        return review
    except (yaml.YAMLError, ValueError) as exc:
        raise KnowledgeError(f"审核队列无法解析:{exc}", path=path) from exc


def save_review(workspace_root: Path, review: KnowledgeReview) -> None:
    _validate_review(review)
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
        if candidate.entry_id and candidate.path == "review/manual":
            # 人工创建条目可被提升但没有材料出处；不伪造来源，也不立即 stale。
            continue
        source = root / candidate.path
        alive = source.is_file()
        if alive:
            text = source.read_text(encoding="utf-8")
            alive = candidate.quote.strip() in text or _hash(text) == candidate.content_hash
        if not alive:
            review.candidates[index] = candidate.model_copy(update={"status": "stale", "updated_at": _now()})
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
            updated_at=_now(),
        )
        # 建议只影响 UI，不自动确认或覆盖。
        if matcher is not None:
            candidate = candidate.model_copy(update={"suggestion": matcher.suggest([candidate.title, *(a.value for a in aliases)])})
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

        response = _run_agent(
            provider,
            _PERSONA,
            f"来源:{path}\n\n正文：\n{text}",
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
        # 提取永远是旁路：即使审核 YAML 损坏或写诊断也失败，也不能反噬 digest/compose。
        try:
            from kairo.rules import safe_provider_summary

            mark_extract_error(workspace_root, path, safe_provider_summary(exc))
        except Exception:
            pass


def _candidate(workspace_root: Path, candidate_id: str) -> tuple[KnowledgeReview, int, KnowledgeCandidate]:
    review = invalidate_stale(workspace_root)
    for index, candidate in enumerate(review.candidates):
        if candidate.id == candidate_id:
            return review, index, candidate
    raise KnowledgeError(f"知识候选不存在:{candidate_id}")


def _source(candidate: KnowledgeCandidate, workspace_root: Path) -> KnowledgeSource:
    return KnowledgeSource(
        kind="digest" if candidate.source_kind == "digest" else "understanding" if candidate.path == "understanding.md" else "digest",
        path=candidate.path,
        quote=candidate.quote,
        content_hash=candidate.content_hash,
        workspace_slug=Path(workspace_root).name,
    )


def _append_source(sources: list[KnowledgeSource], source: KnowledgeSource) -> list[KnowledgeSource]:
    """同一可定位出处只保留一次，避免幂等重试不断堆积。"""
    key = (source.kind, source.workspace_slug, source.path, source.content_hash, source.quote)
    return list(sources) if any((item.kind, item.workspace_slug, item.path, item.content_hash, item.quote) == key for item in sources) else [*sources, source]


def _transaction_path(workspace_root: Path) -> Path:
    return Path(workspace_root) / ".kairo" / "knowledge_transaction.yaml"


def _write_transaction(workspace_root: Path, payload: dict) -> None:
    _atomic_write(_transaction_path(workspace_root), payload)


def _clear_transaction(workspace_root: Path) -> None:
    _transaction_path(workspace_root).unlink(missing_ok=True)


def _transaction(workspace_root: Path) -> dict:
    path = _transaction_path(workspace_root)
    if not path.is_file():
        return {}
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return value if isinstance(value, dict) else {}
    except yaml.YAMLError:
        return {}


def _set_candidate(review: KnowledgeReview, index: int, candidate: KnowledgeCandidate, **changes) -> KnowledgeCandidate:
    updated = candidate.model_copy(update={**changes, "updated_at": _now()})
    review.candidates[index] = updated
    return updated


def accept_workspace(workspace_root: Path, candidate_id: str) -> KnowledgeEntry:
    review, index, candidate = _candidate(workspace_root, candidate_id)
    if candidate.status != "pending":
        raise KnowledgeError(f"候选不可采纳:{candidate.status}")
    document, _ = load_workspace(workspace_root)
    # 重试收敛：权威文件已写而 review 未落盘时，按 merged_into 补齐状态。
    tx = _transaction(workspace_root)
    replay_id = candidate.merged_into or (str(tx.get("entry_id", "")) if tx.get("kind") == "accept_workspace" and tx.get("candidate_id") == candidate.id else "")
    existing_entry = next((entry for entry in document.entries if entry.id == replay_id), None)
    if existing_entry:
        _set_candidate(review, index, candidate, status="accepted", merged_into=existing_entry.id)
        save_review(workspace_root, review)
        _clear_transaction(workspace_root)
        return existing_entry
    entry = new_entry(
        title=candidate.title,
        scope="workspace",
        aliases=candidate.aliases,
        description=candidate.description,
        tags=candidate.tags,
        sources=[_source(candidate, workspace_root)],
    )
    validate_entries([*document.entries, entry], scope="workspace")
    document.entries.append(entry)
    _write_transaction(workspace_root, {"kind": "accept_workspace", "candidate_id": candidate.id, "entry_id": entry.id})
    save_workspace(workspace_root, document)
    _set_candidate(review, index, candidate, status="accepted", merged_into=entry.id)
    save_review(workspace_root, review)
    _clear_transaction(workspace_root)
    return entry


def ignore(workspace_root: Path, candidate_id: str) -> None:
    review, index, candidate = _candidate(workspace_root, candidate_id)
    if candidate.status != "pending":
        raise KnowledgeError(f"候选不可忽略:{candidate.status}")
    _set_candidate(review, index, candidate, status="ignored")
    save_review(workspace_root, review)


def update_candidate(workspace_root: Path, candidate_id: str, *, title: str, description: str, aliases: list[KnowledgeAlias], tags: list[str]) -> KnowledgeCandidate:
    review, index, candidate = _candidate(workspace_root, candidate_id)
    if candidate.status != "pending":
        raise KnowledgeError(f"候选不可编辑:{candidate.status}")
    updated = _set_candidate(review, index, candidate, title=title.strip(), description=description.strip(), aliases=aliases, tags=tags)
    _validate_candidate(updated)
    save_review(workspace_root, review)
    return updated


def promote_entry(workspace_root: Path, entry_id: str) -> KnowledgeCandidate:
    """仅允许已确认的 workspace 条目进入 global 审核，且沿用 ke-* stable ID。"""
    document, _ = load_workspace(workspace_root)
    entry = next((item for item in document.entries if item.id == entry_id), None)
    if entry is None or entry.status != "confirmed":
        raise KnowledgeError("只能提升已确认的本地知识条目")
    review = load_review(workspace_root)
    existing = next((c for c in review.candidates if c.entry_id == entry_id and c.status in {"pending_global", "rejected_global"}), None)
    if existing:
        if existing.status == "pending_global":
            return existing
        refreshed = existing.model_copy(update={"title": entry.title, "aliases": entry.aliases, "description": entry.description, "tags": entry.tags, "status": "pending_global", "reject_reason": "", "updated_at": _now()})
        review.candidates[review.candidates.index(existing)] = refreshed
        save_review(workspace_root, review)
        return refreshed
    # 无出处的人工条目不伪造 constitution/digest 出处；其审核候选仍可稳定追踪。
    source = entry.sources[0] if entry.sources else None
    source_path = source.path if source else "review/manual"
    source_quote = source.quote if source else entry.title
    source_hash = source.content_hash if source and source.content_hash else _hash(entry.title)
    fp = _fingerprint("compose", source_path, entry.title, source_quote)
    candidate = KnowledgeCandidate(id="kc-" + fp[:20], title=entry.title, aliases=entry.aliases, description=entry.description, tags=entry.tags, source_kind="compose" if source and source.kind == "understanding" else "digest", path=source_path, quote=source_quote, content_hash=source_hash, fingerprint=fp, status="pending_global", entry_id=entry.id, updated_at=_now())
    review.candidates.append(candidate)
    save_review(workspace_root, review)
    return candidate


def promote(workspace_root: Path, entry_id: str) -> KnowledgeCandidate:
    """兼容导出名；参数语义已改为 workspace entry id。"""
    return promote_entry(workspace_root, entry_id)


def accept_global(serve_root: Path, workspace_root: Path, candidate_id: str) -> KnowledgeEntry:
    review, index, candidate = _candidate(workspace_root, candidate_id)
    if candidate.status != "pending_global":
        raise KnowledgeError(f"候选不在全局待审核:{candidate.status}")
    if not candidate.entry_id:
        raise KnowledgeError("公共审核只接受已确认的本地知识条目")
    local_doc, _ = load_workspace(workspace_root)
    local_entry = next((item for item in local_doc.entries if item.id == candidate.entry_id), None)
    document, _ = load_global(serve_root)
    entry = next((item for item in document.entries if item.id == candidate.entry_id), None)
    if entry is None:
        if local_entry is None:
            raise KnowledgeError("待提升的本地知识条目不存在")
        sources = local_entry.sources
        if local_entry.sources:
            sources = _append_source(sources, _source(candidate, workspace_root))
        entry = local_entry.model_copy(update={"scope": "global", "sources": sources, "updated_at": _now()})
        validate_entries([*document.entries, entry], scope="global")
        _write_transaction(workspace_root, {"kind": "accept_global", "candidate_id": candidate.id, "entry_id": entry.id})
        document.entries.append(entry)
        save_global(serve_root, document)
    # 第二步移除 local 独立 authority；重试也会收敛到同一最终状态。
    if local_entry is not None:
        local_doc.entries = [item for item in local_doc.entries if item.id != entry.id]
        save_workspace(workspace_root, local_doc)
    _set_candidate(review, index, candidate, status="accepted", merged_into=entry.id)
    save_review(workspace_root, review)
    _clear_transaction(workspace_root)
    return entry


def reject_global(workspace_root: Path, candidate_id: str, reason: str) -> None:
    review, index, candidate = _candidate(workspace_root, candidate_id)
    if candidate.status != "pending_global":
        raise KnowledgeError(f"候选不在全局待审核:{candidate.status}")
    # 拒绝不改变本地条目权威；候选记录保留理由，用户可编辑后重新提升。
    _set_candidate(review, index, candidate, status="rejected_global", reject_reason=reason.strip() or "未说明")
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
    sources = _append_source(target.sources, _source(candidate, workspace_root))
    replacement = target.model_copy(update={"aliases": aliases, "sources": sources, "updated_at": _now()})
    document.entries = [replacement if entry.id == entry_id else entry for entry in document.entries]
    validate_entries(document.entries, scope="workspace")
    _write_transaction(workspace_root, {"kind": "merge_workspace", "candidate_id": candidate.id, "entry_id": entry_id})
    save_workspace(workspace_root, document)
    _set_candidate(review, index, candidate, status="merged", merged_into=entry_id)
    save_review(workspace_root, review)
    _clear_transaction(workspace_root)


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
    replacement = target.model_copy(update={"aliases": aliases, "description": target.description or candidate.description, "tags": sorted(set([*target.tags, *candidate.tags])), "sources": _append_source(target.sources, _source(candidate, workspace_root)), "updated_at": _now()})
    document.entries = [replacement if entry.id == entry_id else entry for entry in document.entries]
    validate_entries(document.entries, scope="global")
    _write_transaction(workspace_root, {"kind": "merge_global", "candidate_id": candidate.id, "entry_id": entry_id})
    save_global(serve_root, document)
    if candidate.entry_id:
        local_doc, _ = load_workspace(workspace_root)
        if any(item.id == candidate.entry_id for item in local_doc.entries):
            local_doc.entries = [item for item in local_doc.entries if item.id != candidate.entry_id]
            save_workspace(workspace_root, local_doc)
    _set_candidate(review, index, candidate, status="merged", merged_into=entry_id)
    save_review(workspace_root, review)
    _clear_transaction(workspace_root)


def set_obsolete(workspace_root: Path, entry_id: str) -> None:
    document, _ = load_workspace(workspace_root)
    found = False
    entries: list[KnowledgeEntry] = []
    for entry in document.entries:
        if entry.id == entry_id:
            entries.append(entry.model_copy(update={"status": "obsolete", "updated_at": _now()}))
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
            # 表单只传别名文字时保留同名别名的 auto_match；新增别名默认可匹配。
            previous = {normalize_term(alias.value): alias.auto_match for alias in entry.aliases}
            preserved = [alias.model_copy(update={"auto_match": previous.get(normalize_term(alias.value), alias.auto_match)}) for alias in aliases]
            entries.append(entry.model_copy(update={"title": title.strip(), "description": description.strip(), "aliases": preserved, "tags": tags, "updated_at": _now()}))
            found = True
        else:
            entries.append(entry)
    if not found:
        raise KnowledgeError(f"本地知识条目不存在:{entry_id}")
    validate_entries(entries, scope="workspace")
    document.entries = entries
    save_workspace(workspace_root, document)
