"""#144 时段回顾:按发生日闭区间收集 digest,一次生成,落成 stream reference。

#145:纪要以文件摆盘,prompt 只留清单。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from pathlib import Path

from kairo.provider import AgentConfig, select_provider
from kairo.timeline import (
    MAX_RANGE_DAYS,
    TimelineItem,
    filter_range,
    range_day_count,
    scan_timeline,
)
from kairo.workspace import Workspace, WorkspaceNotFound

JOURNAL_NAME = "总结"
_REVIEW_TITLE = re.compile(r"^\d{4}-\d{2}-\d{2}～\d{4}-\d{2}-\d{2} 回顾$")
_PROCESS = re.compile(r"先读取|提示被截断|完整提示|相关技能")


class ReviewError(ValueError):
    """区间非法、空、无纪要或生成失败。"""


def digest_path(root: Path, it: TimelineItem) -> Path:
    return Path(root) / it.workspace / "references" / it.id / "digest.md"


def collect_digests(
    root: Path, items: list[TimelineItem]
) -> tuple[list[tuple[TimelineItem, str]], list[TimelineItem]]:
    with_d: list[tuple[TimelineItem, str]] = []
    without: list[TimelineItem] = []
    for it in items:
        path = digest_path(root, it)
        if path.is_file():
            with_d.append((it, path.read_text(encoding="utf-8")))
        else:
            without.append(it)
    return with_d, without


def review_title(start: dt.date, end: dt.date) -> str:
    return f"{start.isoformat()}～{end.isoformat()} 回顾"


def occupied_span(items: list[TimelineItem]) -> tuple[dt.date, dt.date] | None:
    dates = [it.occurred_at for it in items if it.occurred_at is not None]
    if not dates:
        return None
    return min(dates), max(dates)


def strip_process_preamble(text: str) -> str:
    raw = (text or "").lstrip("\ufeff").lstrip()
    if not raw:
        return raw
    m = re.search(r"(?m)^#{1,6}\s+\S", raw)
    if m and m.start() > 0 and _PROCESS.search(raw[: m.start()]):
        return raw[m.start() :].lstrip()
    lines = raw.splitlines()
    i = 0
    while i < len(lines) and _PROCESS.search(lines[i]):
        i += 1
    return "\n".join(lines[i:]).lstrip()


def write_materials(root: Path, materials: dict[str, str]) -> None:
    root = root.resolve()
    for rel, text in materials.items():
        dest = (root / rel).resolve()
        if dest != root and root not in dest.parents:
            raise ValueError(f"material path escapes artifact_dir:{rel}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")


def layout_review(
    with_d: list[tuple[TimelineItem, str]], without: list[TimelineItem]
) -> tuple[dict[str, str], str]:
    materials: dict[str, str] = {}
    lines = [
        "纪要已按文件放在 cwd(见下)。请 Read 后写回顾。只输出回顾正文,不要过程句。"
    ]
    for it, text in with_d:
        rel = f"digest/{it.workspace}/{it.id}.md"
        materials[rel] = strip_process_preamble(text)
        occ = it.occurred_at.isoformat() if it.occurred_at else "-"
        lines.append(f"- {occ} · {it.topic} · {it.title} → {rel}")
    if without:
        lines.append("无纪要:")
        lines.extend(f"- {it.topic} / {it.title} ({it.id})" for it in without)
    return materials, "\n".join(lines)


def build_context(
    with_d: list[tuple[TimelineItem, str]], without: list[TimelineItem]
) -> str:
    """清单 context(不含 digest 正文)。"""
    _, context = layout_review(with_d, without)
    return context


_PERSONA = (
    "你在写一份跨主题的时段回顾。只根据给定纪要归纳这段时间发生了什么、"
    "待跟进事项和明显冲突。不要编造纪要里没有的事实。"
    "用简洁中文 Markdown。文末列出无纪要的条目(若有)。"
)


def generate_review_body(
    with_d: list[tuple[TimelineItem, str]],
    without: list[TimelineItem],
    *,
    artifact_dir: Path,
    provider=None,
) -> str:
    if not with_d:
        raise ReviewError("no-digest")
    provider = provider or select_provider()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    materials, context = layout_review(with_d, without)
    write_materials(artifact_dir, materials)
    cfg = AgentConfig(
        persona=_PERSONA,
        context=context,
        artifact_dir=artifact_dir,
        model=provider.model,
        artifact="review.md",
    )
    try:
        result = provider.run(cfg)
    except ReviewError:
        raise
    except Exception as e:
        raise ReviewError("empty") from e
    path = artifact_dir / "review.md"
    if path.is_file():
        body = path.read_text(encoding="utf-8")
    else:
        body = result.result_text or ""
    body = strip_process_preamble(body)
    if not body.strip():
        raise ReviewError("empty")
    return body


def is_journal_item(it: TimelineItem, root: Path | None = None) -> bool:
    if root is not None:
        try:
            from kairo.kind import is_journal_workspace

            ws = Workspace.open(Path(root) / it.workspace)
            if is_journal_workspace(ws):
                return True
            if ws.constitution.review_input is False:
                return True
        except Exception:
            pass
    if it.workspace == JOURNAL_NAME or it.topic == JOURNAL_NAME:
        return True
    return bool(_REVIEW_TITLE.match(it.title or ""))


def existing_review_id(ws: Workspace, start: dt.date, end: dt.date) -> str | None:
    want = review_title(start, end)
    hits = [
        rid for rid in ws.list_reference_ids() if ws.read_manifest(rid).title == want
    ]
    return hits[-1] if hits else None


def _source_text_path(ws: Workspace, rid: str) -> Path:
    man = ws.read_manifest(rid)
    form = next((f for f in man.forms if f.role == "source_text"), None)
    if form is None:
        raise ReviewError("empty")
    loc = Path(form.location)
    return loc if loc.is_absolute() else ws.root / loc


def resolve_review_workspace(root: Path, workspace: str = "") -> Workspace:
    """缺省落入 topic/slug「总结」;有 slug 则打开该仓(不存在则 WorkspaceNotFound)。"""
    root = Path(root)
    slug = (workspace or "").strip()
    if slug:
        return Workspace.open(root / slug)
    by_slug: Workspace | None = None
    by_topic: Workspace | None = None
    if root.is_dir():
        for child in sorted(p for p in root.iterdir() if p.is_dir()):
            if not (child / "constitution.yaml").is_file():
                continue
            try:
                ws = Workspace.open(child)
            except WorkspaceNotFound:
                continue
            if child.name == JOURNAL_NAME:
                by_slug = ws
                break
            if ws.constitution.topic == JOURNAL_NAME and by_topic is None:
                by_topic = ws
    found = by_slug or by_topic
    if found is not None:
        return found
    return Workspace.init(root / JOURNAL_NAME, topic=JOURNAL_NAME)


def write_review_reference(
    ws, start: dt.date, end: dt.date, body: str, *, occurred: dt.date | None = None
) -> str:
    existing = existing_review_id(ws, start, end)
    if existing:
        path = _source_text_path(ws, existing)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        man = ws.read_manifest(existing)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
        man.forms = [
            f.model_copy(update={"hash": digest}) if f.role == "source_text" else f
            for f in man.forms
        ]
        ws.write_manifest(existing, man)
        return existing
    uploads = ws.root / ".kairo" / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    src = uploads / f"review-{start.isoformat()}-{end.isoformat()}.md"
    src.write_text(body, encoding="utf-8")
    return ws.add(
        [src],
        title=review_title(start, end),
        occurred_at=(occurred or end).isoformat(),
        copy=True,
        source_class="stream",
        role="source_text",
    )


def prepare_range(
    items: list[TimelineItem],
    start: dt.date,
    end: dt.date,
    root: Path | None = None,
) -> list[TimelineItem]:
    if range_day_count(start, end) > MAX_RANGE_DAYS:
        raise ReviewError("range-too-long")
    found = [
        it
        for it in filter_range(items, start, end)
        if not is_journal_item(it, root)
    ]
    if not found:
        raise ReviewError("empty-range")
    return found


def produce_review(root: Path, ws, start: dt.date, end: dt.date, *, provider=None) -> str:
    found = prepare_range(scan_timeline(root), start, end, root=root)
    with_d, without = collect_digests(root, found)
    body = generate_review_body(
        with_d,
        without,
        artifact_dir=Path(root) / ".kairo" / "review-work",
        provider=provider,
    )
    occ = occupied_span([it for it, _ in with_d]) or (start, end)
    return write_review_reference(ws, occ[0], occ[1], body, occurred=end)
