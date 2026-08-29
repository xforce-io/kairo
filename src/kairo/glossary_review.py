"""#165 Digest 真名册候选与两级审核。"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Callable

import yaml
from pydantic import BaseModel, Field

from kairo.glossary import (
    GlossaryError,
    add_entry,
    effective_items,
    load_glossary_file,
    load_workspace_glossary,
    resolve_serve_root,
    root_glossary_path,
    save_glossary_file,
    write_workspace_glossary,
)
from kairo.models import GlossaryEntry

STATUS_PENDING = "pending"
STATUS_ACCEPTED = "accepted"
STATUS_MERGED = "merged"
STATUS_IGNORED = "ignored"
STATUS_PENDING_ROOT = "pending_root"
STATUS_ROOT_REJECTED = "root_rejected"

OPEN_STATUSES = {STATUS_PENDING, STATUS_PENDING_ROOT, STATUS_ROOT_REJECTED}


class GlossaryCandidate(BaseModel):
    id: str
    name: str
    note: str = ""
    aka: list[str] = Field(default_factory=list)
    ref_id: str
    quote: str
    digest_hash: str = ""
    status: str = STATUS_PENDING
    fingerprint: str = ""
    merged_into: str = ""
    reject_reason: str = ""


class ReviewStore(BaseModel):
    candidates: list[GlossaryCandidate] = Field(default_factory=list)
    extract_errors: dict[str, str] = Field(default_factory=dict)


Extractor = Callable[[str, list[GlossaryEntry], str], list[dict]]

_EXTRACT_PERSONA = """从一份已完成的 digest 中提取值得人工审核的领域专名候选。

只提出 digest 原文中有直接证据、且不在现有生效真名册中的名称；不要猜测、不要自动纠正。
每项 quote 必须逐字出现在 digest 中，长度应尽量短但足以作为证据。输出 YAML 列表，
每项仅可含 name、note、aka、quote；无候选时输出 []。不要输出 Markdown 围栏或解释。"""


def review_path(ws_root: Path) -> Path:
    return Path(ws_root) / ".kairo" / "glossary_review.yaml"


def load_review(ws_root: Path) -> ReviewStore:
    path = review_path(ws_root)
    if not path.is_file():
        return ReviewStore()
    data = yaml.safe_load(path.read_text()) or {}
    return ReviewStore.model_validate(data)


def save_review(ws_root: Path, store: ReviewStore) -> None:
    path = review_path(ws_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(store.model_dump(), allow_unicode=True, sort_keys=False)
    )


def fingerprint(ref_id: str, name: str, quote: str) -> str:
    raw = f"{ref_id}\n{name.strip()}\n{quote.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _digest_path(ws_root: Path, ref_id: str) -> Path:
    return Path(ws_root) / "references" / ref_id / "digest.md"


def _digest_hash(ws_root: Path, ref_id: str) -> str:
    p = _digest_path(ws_root, ref_id)
    if not p.is_file():
        return ""
    return hashlib.sha256(p.read_text().encode("utf-8")).hexdigest()


def invalidate_stale(ws_root: Path) -> ReviewStore:
    """无 digest 或原文证据消失的待审核项失效。"""
    store = load_review(ws_root)
    refs = {p.name for p in (Path(ws_root) / "references").glob("*") if p.is_dir()} if (Path(ws_root) / "references").exists() else set()
    kept: list[GlossaryCandidate] = []
    for c in store.candidates:
        if c.status not in OPEN_STATUSES:
            kept.append(c)
            continue
        if c.ref_id not in refs:
            continue
        digest = _digest_path(ws_root, c.ref_id)
        if not digest.is_file():
            continue
        text = digest.read_text()
        if c.quote.strip() and c.quote.strip() not in text:
            continue
        current = _digest_hash(ws_root, c.ref_id)
        if c.digest_hash and current and c.digest_hash != current and c.quote.strip() not in text:
            continue
        kept.append(c)
    store.candidates = kept
    save_review(ws_root, store)
    return store


def open_candidates(ws_root: Path) -> list[GlossaryCandidate]:
    store = invalidate_stale(ws_root)
    return [c for c in store.candidates if c.status in OPEN_STATUSES]


def default_extractor(
    digest: str, effective: list[GlossaryEntry], ref_id: str
) -> list[dict]:
    """无 provider 时的保守空提取。测试可替换。"""
    return []


def provider_extractor(provider) -> Extractor:
    """将候选提取交给当前 Digest provider；产物仍须经过证据校验。"""

    def extract(digest: str, effective: list[GlossaryEntry], ref_id: str) -> list[dict]:
        from kairo.rules import _run_agent

        glossary = yaml.safe_dump(
            [entry.model_dump() for entry in effective],
            allow_unicode=True,
            sort_keys=False,
        )
        context = (
            f"reference: {ref_id}\n\n"
            "现有生效真名册（只读；其中名称或别称不得再次提出）：\n"
            f"{glossary or '[]'}\n\n"
            "digest：\n"
            f"{digest}"
        )
        text = _run_agent(provider, _EXTRACT_PERSONA, context, "candidates.yaml")
        return parse_extract_yaml(text)

    return extract


def ingest_candidates(
    ws_root: Path,
    ref_id: str,
    drafts: list[dict],
    *,
    effective: list[GlossaryEntry] | None = None,
) -> ReviewStore:
    store = invalidate_stale(ws_root)
    store.extract_errors.pop(ref_id, None)
    effective = effective or []
    known = {e.name for e in effective}
    known_aka = {a for e in effective for a in e.aka}
    ignored = {
        c.fingerprint
        for c in store.candidates
        if c.status == STATUS_IGNORED
    }
    existing_fp = {c.fingerprint for c in store.candidates if c.status in OPEN_STATUSES}
    dhash = _digest_hash(ws_root, ref_id)
    digest_text = _digest_path(ws_root, ref_id).read_text() if _digest_path(ws_root, ref_id).is_file() else ""
    for raw in drafts:
        name = str(raw.get("name") or "").strip()
        quote = str(raw.get("quote") or "").strip()
        if not name or not quote:
            continue
        if quote not in digest_text:
            continue
        if name in known or name in known_aka:
            continue
        fp = fingerprint(ref_id, name, quote)
        if fp in ignored or fp in existing_fp:
            continue
        cid = "gc-" + fp[:12]
        store.candidates.append(
            GlossaryCandidate(
                id=cid,
                name=name,
                note=str(raw.get("note") or "").strip(),
                aka=[a.strip() for a in (raw.get("aka") or []) if str(a).strip()],
                ref_id=ref_id,
                quote=quote,
                digest_hash=dhash,
                status=STATUS_PENDING,
                fingerprint=fp,
            )
        )
        existing_fp.add(fp)
    save_review(ws_root, store)
    return store


def mark_extract_error(ws_root: Path, ref_id: str, message: str) -> None:
    store = load_review(ws_root)
    store.extract_errors[ref_id] = message
    save_review(ws_root, store)


def get_candidate(ws_root: Path, cid: str) -> GlossaryCandidate:
    store = invalidate_stale(ws_root)
    for c in store.candidates:
        if c.id == cid:
            return c
    raise GlossaryError(f"候选不存在:{cid}")


def _set_status(ws_root: Path, cid: str, status: str, **fields) -> GlossaryCandidate:
    store = load_review(ws_root)
    for i, c in enumerate(store.candidates):
        if c.id == cid:
            data = c.model_dump()
            data["status"] = status
            data.update(fields)
            store.candidates[i] = GlossaryCandidate.model_validate(data)
            save_review(ws_root, store)
            return store.candidates[i]
    raise GlossaryError(f"候选不存在:{cid}")


def accept_workspace(ws, cid: str) -> GlossaryCandidate:
    c = get_candidate(ws.root, cid)
    if c.status not in OPEN_STATUSES:
        raise GlossaryError(f"候选状态不可接受:{c.status}")
    ws.add_glossary_entry(c.name, note=c.note, aka=c.aka)
    return _set_status(ws.root, cid, STATUS_ACCEPTED)


def merge_workspace(ws, cid: str, existing_name: str) -> GlossaryCandidate:
    c = get_candidate(ws.root, cid)
    if c.status not in OPEN_STATUSES:
        raise GlossaryError(f"候选状态不可合并:{c.status}")
    entries = load_workspace_glossary(ws.root)
    target = next((e for e in entries if e.name == existing_name), None)
    if target is None:
        raise GlossaryError(f"没有可合并的条目:{existing_name}")
    aka = list(target.aka)
    for a in [c.name, *c.aka]:
        if a and a not in aka and a != target.name:
            aka.append(a)
    note = target.note or c.note
    new_entries = [
        GlossaryEntry(name=e.name, note=note if e.name == existing_name else e.note, aka=aka if e.name == existing_name else e.aka, tags=e.tags)
        if e.name == existing_name
        else e
        for e in entries
    ]
    root = resolve_serve_root(ws_root=ws.root)
    effective_items(load_glossary_file(root_glossary_path(root)), new_entries)
    write_workspace_glossary(ws.root, new_entries)
    ws.stamp_glossary_pending()
    return _set_status(ws.root, cid, STATUS_MERGED, merged_into=existing_name)


def ignore_candidate(ws_root: Path, cid: str) -> GlossaryCandidate:
    c = get_candidate(ws_root, cid)
    if c.status not in OPEN_STATUSES:
        raise GlossaryError(f"候选状态不可忽略:{c.status}")
    return _set_status(ws_root, cid, STATUS_IGNORED)


def promote_candidate(ws_root: Path, cid: str) -> GlossaryCandidate:
    c = get_candidate(ws_root, cid)
    if c.status not in {STATUS_PENDING, STATUS_ROOT_REJECTED}:
        raise GlossaryError(f"候选状态不可提交公共:{c.status}")
    return _set_status(ws_root, cid, STATUS_PENDING_ROOT)


def accept_root(serve_root: Path, slug: str, cid: str) -> GlossaryCandidate:
    ws_root = Path(serve_root) / slug
    c = get_candidate(ws_root, cid)
    if c.status != STATUS_PENDING_ROOT:
        raise GlossaryError(f"候选不在待提升:{c.status}")
    path = root_glossary_path(Path(serve_root))
    entries = add_entry(load_glossary_file(path), c.name, note=c.note, aka=c.aka)
    save_glossary_file(path, entries)
    local = load_workspace_glossary(ws_root)
    local = [e for e in local if e.name != c.name]
    write_workspace_glossary(ws_root, local)
    from kairo.workspace import stamp_serve_workspaces

    stamp_serve_workspaces(serve_root)
    return _set_status(ws_root, cid, STATUS_ACCEPTED)


def merge_root(serve_root: Path, slug: str, cid: str, existing_name: str) -> GlossaryCandidate:
    ws_root = Path(serve_root) / slug
    c = get_candidate(ws_root, cid)
    if c.status != STATUS_PENDING_ROOT:
        raise GlossaryError(f"候选不在待提升:{c.status}")
    path = root_glossary_path(Path(serve_root))
    entries = load_glossary_file(path)
    target = next((e for e in entries if e.name == existing_name), None)
    if target is None:
        raise GlossaryError(f"没有可合并的公共条目:{existing_name}")
    aka = list(target.aka)
    for a in [c.name, *c.aka]:
        if a and a not in aka and a != target.name:
            aka.append(a)
    nxt = []
    for e in entries:
        if e.name == existing_name:
            nxt.append(GlossaryEntry(name=e.name, note=e.note or c.note, aka=aka, tags=e.tags))
        else:
            nxt.append(e)
    save_glossary_file(path, nxt)
    from kairo.workspace import stamp_serve_workspaces

    stamp_serve_workspaces(serve_root)
    return _set_status(ws_root, cid, STATUS_MERGED, merged_into=existing_name)


def reject_root(ws_root: Path, cid: str, reason: str) -> GlossaryCandidate:
    c = get_candidate(ws_root, cid)
    if c.status != STATUS_PENDING_ROOT:
        raise GlossaryError(f"候选不在待提升:{c.status}")
    return _set_status(
        ws_root, cid, STATUS_ROOT_REJECTED, reject_reason=(reason or "").strip()
    )


def parse_extract_yaml(text: str) -> list[dict]:
    m = re.search(r"(\[[\s\S]*\]|entries:\s*[\s\S]*)", text)
    raw = m.group(1) if m else text
    data = yaml.safe_load(raw)
    if data is None:
        return []
    if isinstance(data, dict):
        data = data.get("entries") or []
    if not isinstance(data, list):
        raise GlossaryError("提取结果不是列表")
    return [x for x in data if isinstance(x, dict)]


def extract_after_digest(
    ws,
    ref_id: str,
    digest_text: str,
    *,
    extractor: Extractor | None = None,
    provider=None,
) -> None:
    from kairo.glossary import workspace_effective

    try:
        effective = [i.entry for i in workspace_effective(ws.root)]
        fn = extractor or (provider_extractor(provider) if provider is not None else default_extractor)
        drafts = fn(digest_text, effective, ref_id)
        ingest_candidates(ws.root, ref_id, drafts, effective=effective)
    except Exception as e:
        mark_extract_error(ws.root, ref_id, str(e))
