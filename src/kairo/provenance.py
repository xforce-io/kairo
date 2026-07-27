"""#99 紧凑溯源:来源目录、短 ID、写盘前结构校验。

不删改 digest/材料;不判断业务事实真假;只保证短 ID/索引/路径可追溯。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from kairo.models import Manifest

REASON_PROVENANCE_INVALID = "compose-provenance-invalid"

# 短 ID:S- + 由 ref_id 派生的 hex;碰撞时加长
_SID_RE = re.compile(r"\bS-[0-9a-f]+\b")
_FID_RE = re.compile(r"\bF-[0-9a-f]+-\d+\b")
_FACT_ANCHOR_RE = re.compile(
    r'<a\s+[^>]*\bid\s*=\s*["\'](F-[0-9a-f]+-\d+)["\'][^>]*>',
    re.IGNORECASE,
)
# 正文中禁止的完整 digest 路径(索引链接除外)
_DIGEST_PATH_RE = re.compile(r"references/[^/\s\]]+/digest\.md")
_SOURCE_INDEX_HEADINGS = ("## 来源索引", "## Source Index")
_FACT_INDEX_HEADINGS = ("## 依据事实索引", "## Fact Index")
_INDEX_HEADINGS = _SOURCE_INDEX_HEADINGS + _FACT_INDEX_HEADINGS


@dataclass(frozen=True)
class SourceEntry:
    source_id: str
    ref_id: str
    title: str
    digest_path: str
    digest_hash: str


def _hash_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def source_id_for(ref_id: str, used: set[str], *, min_len: int = 6) -> str:
    """由稳定 ref_id 派生 S-…;碰撞时固定加长位数,不按列表序号重排。"""
    h = _hash_hex(ref_id)
    length = min_len
    while length <= len(h):
        sid = f"S-{h[:length]}"
        if sid not in used:
            return sid
        length += 2
    # 理论上不会走到:用完整哈希
    sid = f"S-{h}"
    return sid


def build_source_catalog(ws, digest_paths: dict[str, str] | None = None) -> list[SourceEntry]:
    """构建可 fold digest 的来源目录。

    digest_paths: path → content hash;None 时扫描 workspace 中 fold 类 digest。
    排序按 source_id,保证稳定输出。
    """
    if digest_paths is None:
        digest_paths = {}
        for ref_id in ws.list_reference_ids():
            man = ws.read_manifest(ref_id)
            sc = ws.constitution.source_classes.get(man.source_class)
            if sc is not None and not sc.fold:
                continue
            path = f"references/{ref_id}/digest.md"
            p = ws.root / path
            if p.is_file():
                digest_paths[path] = hashlib.sha256(p.read_text().encode()).hexdigest()[:12]

    used: set[str] = set()
    entries: list[SourceEntry] = []
    # 按 ref_id 稳定序构建,避免遍历顺序影响碰撞扩展
    items: list[tuple[str, str, str]] = []
    for path, dhash in digest_paths.items():
        parts = path.split("/")
        if len(parts) < 3 or parts[0] != "references" or not path.endswith("/digest.md"):
            continue
        ref_id = parts[1]
        try:
            man: Manifest = ws.read_manifest(ref_id)
            title = man.title or ref_id
        except Exception:
            title = ref_id
        items.append((ref_id, path, dhash if isinstance(dhash, str) else str(dhash)))

    for ref_id, path, dhash in sorted(items, key=lambda x: x[0]):
        # title 再取一次(已有)
        try:
            man = ws.read_manifest(ref_id)
            title = man.title or ref_id
        except Exception:
            title = ref_id
        sid = source_id_for(ref_id, used)
        used.add(sid)
        entries.append(
            SourceEntry(
                source_id=sid,
                ref_id=ref_id,
                title=title,
                digest_path=path,
                digest_hash=dhash,
            )
        )
    return sorted(entries, key=lambda e: e.source_id)


def catalog_by_id(catalog: list[SourceEntry]) -> dict[str, SourceEntry]:
    return {e.source_id: e for e in catalog}


def format_source_catalog_block(catalog: list[SourceEntry]) -> str:
    """注入 Compose context 的只读来源目录(模型不得发明 ID)。"""
    if not catalog:
        return "\n\n[来源目录](空 — 本次无 fold digest)\n"
    lines = [
        "\n\n[来源目录 — 只用下列 S-… ID;不得发明新 ID;完整路径仅允许出现在文末索引链接中]",
        "| ID | ref_id | 标题 | digest |",
        "|---|---|---|---|",
    ]
    for e in catalog:
        lines.append(
            f"| {e.source_id} | {e.ref_id} | {e.title} | {e.digest_path} |"
        )
    return "\n".join(lines) + "\n"


_PROVENANCE_PROTOCOL_FACT = """
[溯源输出协议 · 事实层 understanding]
- 章节若引用 digest:章首写「证据范围:〔S-…〕…」,短 ID 去重并按 ID 排序。
- 关键声明(数字、单源事实、待核、冲突)句末标〔S-…〕;普通归纳不必逐句标。
- 可供判断引用的关键事实加 HTML 锚点:<a id="F-<sid去S-后>-NN"></a>。
- 文末必须有「## 来源索引」表:列 ID / 材料 / 可核对来源,链接为相对路径 [digest](references/<ref>/digest.md)。
- 正文(索引表链接除外)禁止出现完整 `references/.../digest.md` 路径;禁止使用 [来源:references/...] 长路径标记。
- 只用来源目录中的 S-…,不得发明。
"""

_PROVENANCE_PROTOCOL_JUDGMENT = """
[溯源输出协议 · 判断层 assessment]
- 每项判断优先用〔依据:F-…〕链到 understanding 的事实锚点;直接〔S-…〕仅作无法经事实层表达的例外,并简述原因。
- 文末输出「## 依据事实索引」(F-… → 事实锚点与其 S-…);若有直接 S-… 例外,再附「## 来源索引」。
- 正文(索引链接除外)禁止完整 `references/.../digest.md` 路径。
- 只用已声明的 F-…/S-…,不得发明来源 ID。
"""


def provenance_protocol_for(layer: str) -> str:
    """layer: fact | judgment(及未知时用 fact 规则作底线)。"""
    if layer == "judgment":
        return _PROVENANCE_PROTOCOL_JUDGMENT
    return _PROVENANCE_PROTOCOL_FACT


def _section_after_heading(content: str, headings: tuple[str, ...]) -> str:
    """返回最早匹配标题及其后内容；无该类索引则为空。"""
    starts = [i for h in headings if (i := content.find(h)) >= 0]
    return content[min(starts) :] if starts else ""


def _split_body_and_index(content: str) -> tuple[str, str]:
    """拆出正文与第一个索引标题及其后内容。"""
    starts = [i for h in _INDEX_HEADINGS if (i := content.find(h)) >= 0]
    if not starts:
        return content, ""
    start = min(starts)
    return content[:start], content[start:]


def fact_anchor_ids(content: str) -> set[str]:
    """提取事实层实际声明的 HTML F- 锚点，供判断层引用校验。"""
    return set(_FACT_ANCHOR_RE.findall(content))


def validate_provenance(
    content: str,
    catalog: list[SourceEntry],
    *,
    layer: str = "fact",
    known_fact_ids: set[str] | None = None,
) -> list[str]:
    """结构校验;返回错误列表(空=通过)。不评价业务结论质量。

    ``known_fact_ids`` 由调用方从上游事实文档提取；判断层传入后，所有 F-…
    必须确实存在，避免伪造锚点写盘。
    """
    errors: list[str] = []
    if not content or not content.strip():
        return ["empty document"]

    by_id = catalog_by_id(catalog)
    body, index = _split_body_and_index(content)
    source_index = _section_after_heading(content, _SOURCE_INDEX_HEADINGS)
    fact_index = _section_after_heading(content, _FACT_INDEX_HEADINGS)

    # 5. 正文路径泄漏(索引区允许 markdown 链接)
    for m in _DIGEST_PATH_RE.finditer(body):
        errors.append(f"body path leak: {m.group(0)}")

    # 收集正文与全文中的 S- / F-
    body_sids = set(_SID_RE.findall(body))
    all_sids = set(_SID_RE.findall(content))
    all_fids = set(_FID_RE.findall(content))
    declared_fact_ids = fact_anchor_ids(content)

    # 1. 每个 S- 必须在目录中
    for sid in sorted(all_sids):
        if sid not in by_id:
            errors.append(f"unknown source id: {sid}")

    # 事实层 F-<source-id>-NN 的 source-id 必须存在，避免伪造锚点成为
    # 判断层的“可引用事实”。判断层的 F- 由 known_fact_ids 精确校验。
    if layer != "judgment":
        for fid in sorted(declared_fact_ids):
            source_id = f"S-{fid[2:].rsplit('-', 1)[0]}"
            if source_id not in by_id:
                errors.append(f"fact anchor has unknown source id: {fid}")

    # 至少有一类索引；事实层总是要求来源索引，判断层的事实索引即可。
    if all_sids or all_fids or (catalog and len(content) > 80):
        if not index.strip():
            errors.append("missing source/fact index section")

    # 事实层的每个 S- 都必须落到来源索引。判断层则只要求正文直接 S- 例外
    # 有来源索引；「依据事实索引」中的 S- 仅描述 F 的来源，不应强迫重复路径。
    required_source_sids = all_sids if layer != "judgment" else body_sids
    if layer != "judgment" and catalog and len(content) > 80 and not source_index:
        errors.append("missing source index section")
    if required_source_sids:
        if not source_index:
            errors.append("missing source index section")
        else:
            for sid in sorted(required_source_sids):
                if sid not in by_id:
                    continue
                ent = by_id[sid]
                if sid not in source_index:
                    errors.append(f"source index missing id: {sid}")
                elif ent.digest_path not in source_index:
                    errors.append(
                        f"source index missing digest link for {sid}: {ent.digest_path}"
                    )

    # 章节证据范围:若出现「证据范围」行,其中 ID 须合法(已在 all_sids 检查)
    for line in body.splitlines():
        if "证据范围" in line or "Evidence" in line:
            for sid in _SID_RE.findall(line):
                if sid not in by_id:
                    errors.append(f"scope unknown id: {sid}")

    if layer == "judgment":
        if all_fids:
            if not fact_index:
                errors.append("missing fact index section")
            if known_fact_ids is not None:
                for fid in sorted(all_fids):
                    if fid not in known_fact_ids:
                        errors.append(f"unknown fact id: {fid}")
        elif body_sids and not source_index:
            errors.append("judgment layer needs fact index or source index")

    return errors


def fact_anchor_id(source_id: str, seq: int) -> str:
    """F-<sid 去掉 S- 前缀>-NN。"""
    core = source_id[2:] if source_id.startswith("S-") else source_id
    return f"F-{core}-{seq:02d}"
