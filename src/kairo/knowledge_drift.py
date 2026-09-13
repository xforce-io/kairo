"""知识漂移：只指向真受影响的产物（#372）。

一个产物「基于旧知识」当且仅当：
- 它当时匹配到的某条目已变（标题/别名/说明）或已不再 confirmed（changed），或
- 现在的 confirmed 条目能在它的正文里命中、而它当时没有匹配到（new）。

整库哈希不再作为漂移依据：任何一条知识变动都会把全部产物判为漂移，
这个信号等价于「全量重算」，没有信息量。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from kairo.knowledge import KnowledgeEntry, effective_entries, entry_semantic_hash
from kairo.knowledge_matcher import matcher_for
from kairo.models import KnowledgeDiagnostic
from kairo.workspace import Workspace

_PRODUCT_KINDS = {"digest.md": "digest", "prose.md": "prose"}


@dataclass(frozen=True)
class DriftRow:
    path: str  # state key: references/<rid>/digest.md | understanding.md
    target: str  # what re-step accepts: ref id for products, path for live targets
    kind: str  # digest | prose | live
    changed: tuple[str, ...] = field(default=())  # titles of matched entries that changed / vanished
    new: tuple[str, ...] = field(default=())  # titles of entries now matching that were unknown then


def _product_kind(path: str) -> str | None:
    parts = path.split("/")
    if len(parts) == 3 and parts[0] == "references":
        return _PRODUCT_KINDS.get(parts[2])
    return None


def _drift_for(
    text: str,
    diagnostic: KnowledgeDiagnostic | None,
    confirmed: dict[str, KnowledgeEntry],
    matcher,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    matched_ids = list(diagnostic.matched_entry_ids) if diagnostic else []
    hashes = diagnostic.matched_entry_hashes if diagnostic else None
    changed: list[str] = []
    for entry_id in matched_ids:
        entry = confirmed.get(entry_id)
        if entry is None:
            changed.append(entry_id)
        elif hashes and entry_id in hashes and hashes[entry_id] != entry_semantic_hash(entry):
            # Ids without a recorded hash come from before #372: change is undetectable for them.
            changed.append(entry.title)
    now = {hit.entry.id for hit in matcher.match(text).matches}
    new = [confirmed[entry_id].title for entry_id in sorted(now - set(matched_ids)) if entry_id in confirmed]
    return tuple(changed), tuple(new)


def drift_rows(ws: Workspace, serve_root: Path) -> list[DriftRow]:
    """Products and live targets of this Topic whose knowledge basis is out of date."""
    entries = effective_entries(serve_root, ws.root)
    confirmed = {entry.id: entry for entry in entries if entry.status == "confirmed"}
    matcher = matcher_for(entries)
    state = ws.read_state()
    rows: list[DriftRow] = []
    for path, product in state.products.items():
        kind = _product_kind(path)
        if kind is None or product.status == "blocked":
            continue
        file = ws.root / path
        if not file.is_file():
            continue
        changed, new = _drift_for(file.read_text(errors="replace"), product.knowledge_diagnostic, confirmed, matcher)
        if changed or new:
            rows.append(DriftRow(path=path, target=path.split("/")[1], kind=kind, changed=changed, new=new))
    live = {target.path for target in ws.constitution.live_targets()}
    for path, target_state in state.targets.items():
        if path not in live or target_state.status == "blocked":
            continue
        file = ws.root / path
        if not file.is_file():
            continue
        changed, new = _drift_for(file.read_text(errors="replace"), target_state.knowledge_diagnostic, confirmed, matcher)
        if changed or new:
            rows.append(DriftRow(path=path, target=path, kind="live", changed=changed, new=new))
    return rows


def recompute_drifted(ws: Workspace, provider, serve_root: Path) -> list[str]:
    """Re-step exactly the drifted products, references first so the live target folds fresh digests once.

    Returns the targets that were re-stepped. Nothing else in the Topic is touched.
    """
    from kairo.engine import re_step

    rows = drift_rows(ws, serve_root)
    refs = sorted({row.target for row in rows if row.kind != "live"})
    lives = [row.target for row in rows if row.kind == "live"]
    done: list[str] = []
    for ref_id in refs:
        re_step(ws, provider, ref_id)
        done.append(ref_id)
    # Re-stepping a reference already recomposes live targets that fold it;
    # only an otherwise-untouched live target still needs its own pass.
    still = {row.target for row in drift_rows(ws, serve_root) if row.kind == "live"}
    for path in lives:
        if path in still:
            re_step(ws, provider, path)
            done.append(path)
    return done
