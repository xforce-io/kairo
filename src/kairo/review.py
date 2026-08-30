"""#144 时段回顾:按发生日闭区间收集 digest,一次生成,落成 stream reference。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from kairo.provider import AgentConfig, select_provider
from kairo.timeline import MAX_RANGE_DAYS, TimelineItem, filter_range, range_day_count
from kairo.workspace import Workspace, WorkspaceNotFound

JOURNAL_NAME = "总结"


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


def build_context(
    with_d: list[tuple[TimelineItem, str]], without: list[TimelineItem]
) -> str:
    parts: list[str] = []
    for it, text in with_d:
        parts.append(
            f"## {it.occurred_at.isoformat() if it.occurred_at else '-'} "
            f"· {it.topic} · {it.title}\n\n{text.strip()}"
        )
    if without:
        lines = "\n".join(f"- {it.topic} / {it.title} ({it.id})" for it in without)
        parts.append("无纪要:\n" + lines)
    return "\n\n".join(parts)


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
    cfg = AgentConfig(
        persona=_PERSONA,
        context=build_context(with_d, without),
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
    if not body.strip():
        raise ReviewError("empty")
    return body


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


def write_review_reference(ws, start: dt.date, end: dt.date, body: str) -> str:
    uploads = ws.root / ".kairo" / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    src = uploads / f"review-{start.isoformat()}-{end.isoformat()}.md"
    src.write_text(body, encoding="utf-8")
    return ws.add(
        [src],
        title=review_title(start, end),
        occurred_at=end.isoformat(),
        copy=True,
        source_class="stream",
        role="source_text",
    )


def prepare_range(
    items: list[TimelineItem], start: dt.date, end: dt.date
) -> list[TimelineItem]:
    if range_day_count(start, end) > MAX_RANGE_DAYS:
        raise ReviewError("range-too-long")
    found = filter_range(items, start, end)
    if not found:
        raise ReviewError("empty-range")
    return found
