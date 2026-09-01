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
MAX_DRAFTS_PER_SOURCE = 12


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
    # 旧 URL 仍可能携带 gc-*；只作为解析兼容 id，绝不再写成权威 id。
    legacy_id: str = ""
    # promotion 需携带完整本地条目的出处，而不是把首个出处当成全部事实。
    sources: list[KnowledgeSource] = Field(default_factory=list)
    updated_at: str = ""


class KnowledgeReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[KnowledgeCandidate] = Field(default_factory=list)
    extract_errors: dict[str, str] = Field(default_factory=dict)
    extract_error_versions: dict[str, int] = Field(default_factory=dict)
    extract_error_meta: dict[str, dict[str, str]] = Field(default_factory=dict)


Extractor = Callable[[str, list[KnowledgeEntry], str], list[dict]]


def review_path(workspace_root: Path) -> Path:
    return Path(workspace_root) / ".kairo" / "knowledge_review.yaml"


def _legacy_review_path(workspace_root: Path) -> Path:
    return Path(workspace_root) / ".kairo" / "glossary_review.yaml"


def _safe_path(path: str) -> None:
    value = Path(path)
    if not path or value.is_absolute() or ".." in value.parts:
        raise KnowledgeError(f"候选 path 必须是工作区内相对路径:{path!r}")


def _error_key(source_kind: str, path: str) -> str:
    """提取错误的稳定复合身份；同一路径的 digest/compose 不互相覆盖。"""
    if source_kind not in {"digest", "compose"}:
        raise KnowledgeError(f"候选来源非法:{source_kind}")
    _safe_path(path)
    return f"{source_kind}:{path}"


def extract_error_key(source_kind: str, path: str) -> str:
    """供呈现层/重试端点使用的公开复合身份。"""
    return _error_key(source_kind, path)


def extract_error_meta(review: KnowledgeReview, source_kind: str, path: str) -> dict[str, str]:
    return review.extract_error_meta.get(_error_key(source_kind, path), {})


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
    if candidate.legacy_id and not re.fullmatch(r"gc-[a-zA-Z0-9-]+", candidate.legacy_id):
        raise KnowledgeError(f"旧候选 id 非法:{candidate.legacy_id!r}")
    for source in candidate.sources:
        if source.kind not in {"reference", "digest", "understanding"}:
            raise KnowledgeError(f"候选出处类型非法:{source.kind}")
        _safe_path(source.path)
        if not re.fullmatch(r"[a-f0-9]{64}", source.content_hash):
            raise KnowledgeError("候选出处 content_hash 必须是 SHA-256")
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


def _cleanup_tmp(tmp: Path) -> None:
    try:
        tmp.unlink(missing_ok=True)
    except OSError:
        pass


def _atomic_write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:
        _cleanup_tmp(tmp)
        raise KnowledgeError(f"保存审核队列失败:{exc}", path=path) from exc


def _try_persist_legacy_migration(
    workspace_root: Path, review: KnowledgeReview, legacy_path: Path
) -> None:
    """可写根一次性落盘；只读根保留内存队列、不抛。"""
    try:
        _ensure_legacy_promotion_entries(workspace_root, review)
        save_review(workspace_root, review)
        if legacy_path.is_file():
            legacy_path.replace(legacy_path.with_suffix(".yaml.migrated"))
    except (OSError, KnowledgeError):
        return


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
                    raise KnowledgeError("旧审核候选必须是 mapping", path=legacy_path)
                ref_id = str(old.get("ref_id", "")).strip()
                candidate_path = f"references/{ref_id}/digest.md"
                title = str(old.get("name", "")).strip()
                quote = str(old.get("quote", "")).strip()
                fp = str(old.get("fingerprint") or _fingerprint("digest", candidate_path, title, quote))
                status = {"pending_root": "pending_global", "root_rejected": "rejected_global"}.get(str(old.get("status", "pending")), str(old.get("status", "pending")))
                text_path = Path(workspace_root) / candidate_path
                legacy_hash = str(old.get("digest_hash") or "")
                content_hash = legacy_hash if re.fullmatch(r"[a-f0-9]{64}", legacy_hash) else (_hash(text_path.read_text(encoding="utf-8")) if text_path.is_file() else _hash(""))
                migrated.append(KnowledgeCandidate(id="kc-" + fp[:20], legacy_id=str(old.get("id", "")) if str(old.get("id", "")).startswith("gc-") else "", title=title, aliases=[KnowledgeAlias(value=str(x)) for x in old.get("aka", []) if str(x).strip()], description=str(old.get("note", "")), source_kind="digest", path=candidate_path, quote=quote or "[旧候选缺少引文]", content_hash=content_hash, fingerprint=fp, status=status if status in _CANDIDATE_STATUSES else "stale", merged_into=str(old.get("merged_into", "")), reject_reason=str(old.get("reject_reason", "")), updated_at=_now()))
            review = KnowledgeReview(candidates=migrated, extract_errors={str(k): str(v) for k, v in raw.get("extract_errors", {}).items()})
            _validate_review(review)
            _try_persist_legacy_migration(workspace_root, review, legacy_path)
            return review
        except (OSError, yaml.YAMLError, ValueError) as exc:
            raise KnowledgeError(f"旧审核队列迁移失败:{exc}", path=legacy_path) from exc
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        review = KnowledgeReview.model_validate(data)
        _validate_review(review)
    except (yaml.YAMLError, ValueError) as exc:
        raise KnowledgeError(f"审核队列无法解析:{exc}", path=path) from exc
    # 运行已先创建 v2 文件时，仍把遗留 #165 候选一次性并入，不能让它成为孤岛。
    legacy_path = _legacy_review_path(workspace_root)
    if not legacy_path.is_file():
        return review
    try:
        legacy = yaml.safe_load(legacy_path.read_text(encoding="utf-8")) or {}
        known = {candidate.fingerprint for candidate in review.candidates}
        for old in legacy.get("candidates", []):
            if not isinstance(old, dict):
                raise KnowledgeError("旧审核候选必须是 mapping", path=legacy_path)
            ref_id, title, quote = str(old.get("ref_id", "")).strip(), str(old.get("name", "")).strip(), str(old.get("quote", "")).strip()
            candidate_path = f"references/{ref_id}/digest.md"
            fp = str(old.get("fingerprint") or _fingerprint("digest", candidate_path, title, quote))
            if fp in known or not title or not quote:
                continue
            source = Path(workspace_root) / candidate_path
            review.candidates.append(KnowledgeCandidate(id="kc-" + fp[:20], legacy_id=str(old.get("id", "")) if str(old.get("id", "")).startswith("gc-") else "", title=title, aliases=[KnowledgeAlias(value=str(x)) for x in old.get("aka", []) if str(x).strip()], description=str(old.get("note", "")), source_kind="digest", path=candidate_path, quote=quote, content_hash=_hash(source.read_text(encoding="utf-8")) if source.is_file() else _hash(""), fingerprint=fp, status={"pending_root": "pending_global", "root_rejected": "rejected_global"}.get(str(old.get("status", "pending")), str(old.get("status", "pending"))), merged_into=str(old.get("merged_into", "")), reject_reason=str(old.get("reject_reason", "")), updated_at=_now()))
            known.add(fp)
        _validate_review(review)
    except (yaml.YAMLError, ValueError) as exc:
        raise KnowledgeError(f"审核队列无法解析:{exc}", path=path) from exc
    _try_persist_legacy_migration(workspace_root, review, legacy_path)
    return review


def save_review(workspace_root: Path, review: KnowledgeReview) -> None:
    _validate_review(review)
    _atomic_write(review_path(workspace_root), review.model_dump())


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _fingerprint(source_kind: str, path: str, title: str, quote: str) -> str:
    return _hash(f"{source_kind}\n{path}\n{normalize_term(title)}\n{quote.strip()}")


def _candidate_sources(candidate: KnowledgeCandidate, workspace_root: Path) -> list[KnowledgeSource]:
    """兼容旧单出处字段，并总是向提升/合并提供完整且去重的出处集合。"""
    sources = list(candidate.sources)
    if candidate.path != "review/manual":
        sources = _append_source(sources, _source(candidate, workspace_root))
    return sources


def _source_alive(workspace_root: Path, source: KnowledgeSource) -> bool:
    if source.path == "review/manual":
        return True
    path = Path(workspace_root) / source.path
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return source.quote.strip() in text or _hash(text) == source.content_hash


def _primary_source(candidate: KnowledgeCandidate, workspace_root: Path) -> KnowledgeSource | None:
    sources = _candidate_sources(candidate, workspace_root)
    return next((source for source in sources if _source_alive(workspace_root, source)), sources[0] if sources else None)


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
        # 多出处候选只要仍有一个可定位材料就可继续审核；不固定 sources[0]。
        sources = _candidate_sources(candidate, root)
        alive = any(_source_alive(root, source) for source in sources)
        if not sources:
            alive = candidate.path == "review/manual"
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
    error_key = _error_key(source_kind, path)
    review.extract_errors.pop(error_key, None)
    review.extract_error_versions.pop(error_key, None)
    review.extract_error_meta.pop(error_key, None)
    # 同材料重抽：丢掉该 path 上未审 pending，不叠历史。已终态保留。
    review.candidates = [
        c
        for c in review.candidates
        if not (
            c.path == path
            and c.source_kind == source_kind
            and c.status == "pending"
        )
    ]
    existing = {c.fingerprint for c in review.candidates}
    source_hash = _hash(source_text)
    added = 0
    for draft in drafts:
        title = str(draft.get("title", draft.get("name", ""))).strip()
        quote = str(draft.get("quote", "")).strip()
        if not title or not quote or quote not in source_text:
            continue
        fp = _fingerprint(source_kind, path, title, quote)
        if fp in existing:
            continue
        if added >= MAX_DRAFTS_PER_SOURCE:
            break
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
            sources=[KnowledgeSource(
                kind="understanding" if source_kind == "compose" and path == "understanding.md" else "digest",
                path=path,
                quote=quote,
                content_hash=source_hash,
                workspace_slug=Path(workspace_root).name,
            )],
            updated_at=_now(),
        )
        # 建议只影响 UI，不自动确认或覆盖。
        if matcher is not None:
            candidate = candidate.model_copy(update={"suggestion": matcher.suggest([candidate.title, *(a.value for a in aliases)])})
        review.candidates.append(candidate)
        existing.add(fp)
        added += 1
    save_review(workspace_root, review)
    return review


def mark_extract_error(workspace_root: Path, path: str, message: str, *, source_kind: str = "digest") -> None:
    review = load_review(workspace_root)
    key = _error_key(source_kind, path)
    review.extract_errors[key] = message
    review.extract_error_versions[key] = review.extract_error_versions.get(key, 0) + 1
    review.extract_error_meta[key] = {"source_kind": source_kind, "path": path, "version": str(review.extract_error_versions[key])}
    save_review(workspace_root, review)


_EXTRACT_FENCE = re.compile(r"^```(?:ya?ml)?\s*\n(.*)\n```\s*$", re.S | re.I)


def _load_candidate_doc(raw: str):
    """整篇 YAML 优先。禁止用第一个 `[` 截到最后一个 `]`（会吃掉 tags/aliases）。"""
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError:
        pass
    start = re.search(r"(?m)^-\s+\S", raw)
    if start:
        try:
            return yaml.safe_load(raw[start.start() :])
        except yaml.YAMLError:
            pass
    entries = re.search(r"(?ms)^entries:\s*\n", raw)
    if entries:
        try:
            return yaml.safe_load(raw[entries.start() :])
        except yaml.YAMLError:
            pass
    return None


def parse_extract_yaml(text: str) -> list[dict]:
    raw = (text or "").strip().lstrip("\ufeff")
    if not raw:
        return []
    fenced = _EXTRACT_FENCE.match(raw)
    if fenced:
        raw = fenced.group(1).strip()
    data = _load_candidate_doc(raw)
    if data is None:
        return []
    if isinstance(data, dict):
        data = data.get("entries", [])
    if not isinstance(data, list):
        raise KnowledgeError("知识候选提取结果必须是列表")
    return [item for item in data if isinstance(item, dict)]


_PERSONA = """从已完成产物中提取值得人工审核的领域知识候选。
只能提出原文有直接证据的标题、简短说明和别名；quote 必须逐字出现在原文。
最多 12 条：只提跨材料仍有用的领域专名、系统名或稳定口径。
不要把一次性口号、待办事项、会议流程拆成词条。
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

            mark_extract_error(workspace_root, path, safe_provider_summary(exc), source_kind=source_kind)
        except Exception:
            pass


def _candidate(workspace_root: Path, candidate_id: str) -> tuple[KnowledgeReview, int, KnowledgeCandidate]:
    _recover_transaction(workspace_root)
    review = invalidate_stale(workspace_root)
    for index, candidate in enumerate(review.candidates):
        if candidate.id == candidate_id or candidate.legacy_id == candidate_id:
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


def _ensure_legacy_promotion_entries(workspace_root: Path, review: KnowledgeReview) -> None:
    """把旧 root 审核候选锚定到一个本地 ke-* 条目，旧 gc URL 仍可继续动作。"""
    needing = [(index, candidate) for index, candidate in enumerate(review.candidates)
               if candidate.status in {"pending_global", "rejected_global"} and not candidate.entry_id]
    if not needing:
        return
    document, _ = load_workspace(workspace_root)
    changed = False
    for index, candidate in needing:
        entry = next((item for item in document.entries if normalize_term(item.title) == normalize_term(candidate.title)), None)
        if entry is None:
            entry = new_entry(
                title=candidate.title,
                scope="workspace",
                aliases=candidate.aliases,
                description=candidate.description,
                tags=candidate.tags,
                sources=_candidate_sources(candidate, workspace_root),
            )
            document.entries.append(entry)
            changed = True
        review.candidates[index] = candidate.model_copy(update={"entry_id": entry.id, "sources": _candidate_sources(candidate, workspace_root), "updated_at": _now()})
    if changed:
        validate_entries(document.entries, scope="workspace")
        save_workspace(workspace_root, document)


def _append_source(sources: list[KnowledgeSource], source: KnowledgeSource) -> list[KnowledgeSource]:
    """同一可定位出处只保留一次，避免幂等重试不断堆积。"""
    key = (source.kind, source.workspace_slug, source.path, source.content_hash, source.quote)
    return list(sources) if any((item.kind, item.workspace_slug, item.path, item.content_hash, item.quote) == key for item in sources) else [*sources, source]


def _append_sources(sources: list[KnowledgeSource], additions: list[KnowledgeSource]) -> list[KnowledgeSource]:
    merged = list(sources)
    for source in additions:
        merged = _append_source(merged, source)
    return merged


def _merged_description(existing: str, incoming: str) -> str:
    """短说明保留双方人工已审内容，避免 merge 静默覆盖。"""
    if not incoming or incoming == existing:
        return existing
    if not existing:
        return incoming
    return f"{existing}\n{incoming}"


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


def _entry_hash(entry: KnowledgeEntry) -> str:
    """journal 的可比对 authority 后态，不依赖可能已消失的候选来源。"""
    payload = yaml.safe_dump(entry.model_dump(), allow_unicode=True, sort_keys=True)
    return _hash(payload)


def _prepared_transaction(*, kind: str, candidate: KnowledgeCandidate, target: KnowledgeEntry, source_entry_id: str, serve_root: Path) -> dict:
    return {
        "kind": kind,
        "candidate_id": candidate.id,
        "target_entry_id": target.id,
        "source_entry_id": source_entry_id,
        "serve_root": str(serve_root),
        "expected_entry_hash": _entry_hash(target),
        "stage": "prepared",
    }


def _recover_transaction(workspace_root: Path) -> None:
    """在任何 stale/status 判断前收敛已落盘的跨 authority 操作。"""
    tx = _transaction(workspace_root)
    kind = str(tx.get("kind", ""))
    if kind not in {"accept_workspace", "merge_workspace", "accept_global", "merge_global"}:
        return
    stage = str(tx.get("stage", ""))
    if stage not in {"prepared", "authority-written", "authority_written", ""}:
        return
    serve_value = str(tx.get("serve_root", ""))
    candidate_id = str(tx.get("candidate_id", ""))
    entry_id = str(tx.get("target_entry_id") or tx.get("entry_id") or "")
    source_entry_id = str(tx.get("source_entry_id") or tx.get("entry_id") or "")
    if not candidate_id or not entry_id:
        return
    try:
        authority_written = False
        if kind in {"accept_workspace", "merge_workspace"}:
            local_doc, _ = load_workspace(workspace_root)
            target = next((item for item in local_doc.entries if item.id == entry_id), None)
            authority_written = target is not None and (
                stage != "prepared" or bool(tx.get("expected_entry_hash")) and _entry_hash(target) == tx["expected_entry_hash"]
            )
        else:
            if not serve_value:
                return
            global_doc, _ = load_global(Path(serve_value))
            target = next((item for item in global_doc.entries if item.id == entry_id), None)
            authority_written = target is not None and (
                stage != "prepared" or bool(tx.get("expected_entry_hash")) and _entry_hash(target) == tx["expected_entry_hash"]
            )
            if not authority_written:
                return
            local_doc, _ = load_workspace(workspace_root)
            if source_entry_id and any(item.id == source_entry_id for item in local_doc.entries):
                local_doc.entries = [item for item in local_doc.entries if item.id != source_entry_id]
                save_workspace(workspace_root, local_doc)
        if not authority_written:
            return
        review = load_review(workspace_root)
        for index, candidate in enumerate(review.candidates):
            if candidate.id == candidate_id or candidate.legacy_id == candidate_id:
                terminal = "accepted" if kind in {"accept_workspace", "accept_global"} else "merged"
                review.candidates[index] = candidate.model_copy(
                    update={"status": terminal, "merged_into": entry_id, "updated_at": _now()}
                )
                save_review(workspace_root, review)
                _clear_transaction(workspace_root)
                return
    except (KnowledgeError, OSError):
        # 保留 journal，下一次显式动作继续恢复；绝不先 stale 掉候选。
        return


def _set_candidate(review: KnowledgeReview, index: int, candidate: KnowledgeCandidate, **changes) -> KnowledgeCandidate:
    updated = candidate.model_copy(update={**changes, "updated_at": _now()})
    review.candidates[index] = updated
    return updated


def accept_workspace(workspace_root: Path, candidate_id: str) -> KnowledgeEntry:
    review, index, candidate = _candidate(workspace_root, candidate_id)
    if candidate.status == "accepted" and candidate.merged_into:
        document, _ = load_workspace(workspace_root)
        prior = next((entry for entry in document.entries if entry.id == candidate.merged_into), None)
        if prior is not None:
            return prior
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
        sources=_candidate_sources(candidate, workspace_root),
    )
    validate_entries([*document.entries, entry], scope="workspace")
    document.entries.append(entry)
    transaction = _prepared_transaction(kind="accept_workspace", candidate=candidate, target=entry, source_entry_id="", serve_root=Path(workspace_root).parent)
    _write_transaction(workspace_root, transaction)
    save_workspace(workspace_root, document)
    _write_transaction(workspace_root, {**transaction, "stage": "authority-written"})
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
    existing = next((c for c in review.candidates if c.entry_id == entry_id and c.status in {"pending_global", "rejected_global", "stale"}), None)
    if existing:
        if existing.status == "pending_global":
            return existing
        source = next((item for item in entry.sources if _source_alive(workspace_root, item)), entry.sources[0] if entry.sources else None)
        refreshed = existing.model_copy(update={
            "title": entry.title,
            "aliases": entry.aliases,
            "description": entry.description,
            "tags": entry.tags,
            "source_kind": "compose" if source and source.kind == "understanding" else "digest",
            "path": source.path if source else "review/manual",
            "quote": source.quote if source else entry.title,
            "content_hash": source.content_hash if source else _hash(entry.title),
            "sources": entry.sources,
            "status": "pending_global",
            "reject_reason": "",
            "updated_at": _now(),
        })
        review.candidates[review.candidates.index(existing)] = refreshed
        save_review(workspace_root, review)
        return refreshed
    # 无出处的人工条目不伪造 constitution/digest 出处；其审核候选仍可稳定追踪。
    source = next((item for item in entry.sources if _source_alive(workspace_root, item)), entry.sources[0] if entry.sources else None)
    source_path = source.path if source else "review/manual"
    source_quote = source.quote if source else entry.title
    source_hash = source.content_hash if source and source.content_hash else _hash(entry.title)
    fp = _fingerprint("compose", source_path, entry.title, source_quote)
    candidate = KnowledgeCandidate(id="kc-" + fp[:20], title=entry.title, aliases=entry.aliases, description=entry.description, tags=entry.tags, source_kind="compose" if source and source.kind == "understanding" else "digest", path=source_path, quote=source_quote, content_hash=source_hash, fingerprint=fp, status="pending_global", entry_id=entry.id, sources=entry.sources, updated_at=_now())
    review.candidates.append(candidate)
    save_review(workspace_root, review)
    return candidate


def promote(workspace_root: Path, entry_id: str) -> KnowledgeCandidate:
    """兼容导出名；参数语义已改为 workspace entry id。"""
    return promote_entry(workspace_root, entry_id)


def accept_global(serve_root: Path, workspace_root: Path, candidate_id: str) -> KnowledgeEntry:
    review, index, candidate = _candidate(workspace_root, candidate_id)
    if candidate.status == "accepted" and candidate.merged_into:
        document, _ = load_global(serve_root)
        prior = next((item for item in document.entries if item.id == candidate.merged_into), None)
        if prior is not None:
            return prior
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
        transaction = _prepared_transaction(kind="accept_global", candidate=candidate, target=entry, source_entry_id=candidate.entry_id, serve_root=serve_root)
        _write_transaction(workspace_root, transaction)
        document.entries.append(entry)
        save_global(serve_root, document)
        _write_transaction(workspace_root, {**transaction, "stage": "authority-written"})
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
    if candidate.status == "merged" and candidate.merged_into == entry_id:
        return
    if candidate.status != "pending":
        raise KnowledgeError(f"候选不可合并:{candidate.status}")
    document, _ = load_workspace(workspace_root)
    target = next((entry for entry in document.entries if entry.id == entry_id), None)
    if target is None:
        raise KnowledgeError(f"本地知识条目不存在:{entry_id}")
    aliases = list(target.aliases)
    seen = {normalize_term(alias.value) for alias in aliases}
    for alias in [KnowledgeAlias(value=candidate.title), *candidate.aliases]:
        if normalize_term(alias.value) not in seen and normalize_term(alias.value) != normalize_term(target.title):
            aliases.append(alias)
            seen.add(normalize_term(alias.value))
    sources = _append_sources(target.sources, _candidate_sources(candidate, workspace_root))
    replacement = target.model_copy(update={
        "aliases": aliases,
        "description": _merged_description(target.description, candidate.description),
        "tags": sorted(set([*target.tags, *candidate.tags])),
        "sources": sources,
        "updated_at": _now(),
    })
    document.entries = [replacement if entry.id == entry_id else entry for entry in document.entries]
    validate_entries(document.entries, scope="workspace")
    transaction = _prepared_transaction(kind="merge_workspace", candidate=candidate, target=replacement, source_entry_id="", serve_root=Path(workspace_root).parent)
    _write_transaction(workspace_root, transaction)
    save_workspace(workspace_root, document)
    _write_transaction(workspace_root, {**transaction, "stage": "authority-written"})
    _set_candidate(review, index, candidate, status="merged", merged_into=entry_id)
    save_review(workspace_root, review)
    _clear_transaction(workspace_root)


def merge_global(serve_root: Path, workspace_root: Path, candidate_id: str, entry_id: str) -> None:
    review, index, candidate = _candidate(workspace_root, candidate_id)
    if candidate.status == "merged" and candidate.merged_into == entry_id:
        return
    if candidate.status != "pending_global":
        raise KnowledgeError(f"候选不可合并:{candidate.status}")
    document, _ = load_global(serve_root)
    target = next((entry for entry in document.entries if entry.id == entry_id), None)
    if target is None:
        raise KnowledgeError(f"公共知识条目不存在:{entry_id}")
    aliases = list(target.aliases)
    seen = {normalize_term(alias.value) for alias in aliases}
    for alias in [KnowledgeAlias(value=candidate.title), *candidate.aliases]:
        if normalize_term(alias.value) not in seen and normalize_term(alias.value) != normalize_term(target.title):
            aliases.append(alias)
            seen.add(normalize_term(alias.value))
    local_sources: list[KnowledgeSource] = []
    if candidate.entry_id:
        local_doc, _ = load_workspace(workspace_root)
        local = next((item for item in local_doc.entries if item.id == candidate.entry_id), None)
        if local is not None:
            local_sources = local.sources
    replacement = target.model_copy(update={
        "aliases": aliases,
        "description": _merged_description(target.description, candidate.description),
        "tags": sorted(set([*target.tags, *candidate.tags])),
        "sources": _append_sources(_append_sources(target.sources, _candidate_sources(candidate, workspace_root)), local_sources),
        "updated_at": _now(),
    })
    document.entries = [replacement if entry.id == entry_id else entry for entry in document.entries]
    validate_entries(document.entries, scope="global")
    transaction = _prepared_transaction(kind="merge_global", candidate=candidate, target=replacement, source_entry_id=candidate.entry_id, serve_root=serve_root)
    _write_transaction(workspace_root, transaction)
    save_global(serve_root, document)
    _write_transaction(workspace_root, {**transaction, "stage": "authority-written"})
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
