"""#396 Timeline 当前视图一次确认推进：只读计划。

确认前零 provider。Web 预览与 CLI 预览共用本函数。
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, replace
from pathlib import Path

from kairo.engine import pending, workspace_run_plan
from kairo.refs import (
    is_global_home,
    list_all_refs,
    ref_key,
    related_topics_for_ref,
    timeline_digest_path,
)
from kairo.review import is_journal_item
from kairo.timeline import (
    TimelineItem,
    collapse_artifacts,
    filter_by_tags,
    filter_range,
    scan_timeline,
)
from kairo.workspace import Workspace, WorkspaceNotFound

_REF_IN_KEY = re.compile(r"(?:^|:)references/([^/]+)/")


@dataclass(frozen=True)
class ViewTopic:
    slug: str
    title: str


@dataclass(frozen=True)
class ViewRunPlan:
    start: dt.date
    end: dt.date
    topic_count: int
    ref_count: int
    visible_undigested: int
    topics: tuple[ViewTopic, ...]
    skipped_attention: tuple[ViewTopic, ...]

    def as_json(self) -> dict:
        return {
            "topic_count": self.topic_count,
            "ref_count": self.ref_count,
            "visible_undigested": self.visible_undigested,
            "topics": [{"slug": t.slug, "title": t.title} for t in self.topics],
            "skipped_attention": [
                {"slug": t.slug, "title": t.title} for t in self.skipped_attention
            ],
        }


def _home_of(it: TimelineItem) -> str:
    if is_global_home(it.workspace):
        return ""
    return it.workspace


def _ws_home_name(name: str) -> str:
    if name in ("", "global", "global-home"):
        return ""
    return name


def digest_exists(root: Path | str, it: TimelineItem) -> bool:
    return timeline_digest_path(Path(root), it.workspace, it.id).is_file()


def annotate_undigested(
    root: Path | str, items: list[TimelineItem]
) -> list[TimelineItem]:
    """未加工只看 digest 文件；不跑 workspace_run_plan。"""
    root = Path(root)

    def one(it: TimelineItem) -> TimelineItem:
        folded = tuple(one(child) for child in it.folded)
        if it.kind != "ref" or not it.fold:
            return replace(it, undigested=False, folded=folded)
        return replace(
            it, undigested=not digest_exists(root, it), folded=folded
        )

    return [one(it) for it in items]


def count_visible_undigested(items: list[TimelineItem]) -> int:
    n = 0
    stack = list(items)
    while stack:
        it = stack.pop()
        if it.kind == "ref" and it.fold and it.undigested:
            n += 1
        stack.extend(it.folded)
    return n


def pane_items(
    root: Path | str,
    items: list[TimelineItem],
    start: dt.date,
    end: dt.date,
) -> list[TimelineItem]:
    """与 GET /timeline 日历 pane 同一集合（含闭区间 journal 藏行）。"""
    root = Path(root)
    if start == end:
        presented = collapse_artifacts(items)
        return [it for it in presented if it.occurred_at == start]
    ranged = [
        it
        for it in filter_range(items, start, end)
        if not is_journal_item(it, root)
    ]
    return collapse_artifacts(ranged)


def _ref_identity_from_work_key(topic_slug: str, key: str) -> str | None:
    m = _REF_IN_KEY.search(key or "")
    if not m:
        return None
    ref_id = m.group(1)
    prefix = key[: m.start()]
    if prefix.endswith(":"):
        home = _ws_home_name(prefix[:-1])
    else:
        home = topic_slug
    return ref_key(home, ref_id)


def _ref_identities_for_topic(ws: Workspace, catalog) -> set[str]:
    slug = _ws_home_name(ws.root.name)
    ids: set[str] = set()
    for item in pending(ws, catalog=catalog):
        ident = _ref_identity_from_work_key(slug, item.key)
        if ident:
            ids.add(ident)
    plan = workspace_run_plan(ws, catalog=catalog)
    for block in plan["blocked_refs"]:
        if not block.get("retryable"):
            continue
        home = block.get("home")
        if home is None:
            home = slug
        ids.add(ref_key(home or "", block["ref_id"]))
    return ids


def _topics_for_undigested_ref(
    root: Path, it: TimelineItem
) -> dict[str, ViewTopic]:
    home = _home_of(it)
    out: dict[str, ViewTopic] = {}
    for row in related_topics_for_ref(root, home, it.id):
        out[row["slug"]] = ViewTopic(slug=row["slug"], title=row["title"])
    if home and home not in out:
        try:
            ws = Workspace.open(root / home)
        except WorkspaceNotFound:
            ws = None
        if ws is not None:
            out[home] = ViewTopic(slug=home, title=ws.constitution.topic)
    return out


def plan_view_run(
    root: Path | str,
    start: dt.date,
    end: dt.date,
    tags: list[str] | None = None,
) -> ViewRunPlan:
    """只读。tags 仅 Web 当前视图；CLI 不传。"""
    root = Path(root).resolve()
    if end < start:
        start, end = end, start
    items = scan_timeline(root)
    if tags:
        items = filter_by_tags(items, list(tags))
    items = annotate_undigested(root, items)

    view_undigested = [
        it
        for it in filter_range(items, start, end)
        if it.kind == "ref" and it.fold and it.undigested
    ]
    visible = count_visible_undigested(pane_items(root, items, start, end))

    related: dict[str, ViewTopic] = {}
    for it in view_undigested:
        related.update(_topics_for_undigested_ref(root, it))

    catalog = list_all_refs(root)
    entered: list[ViewTopic] = []
    skipped: list[ViewTopic] = []
    for slug in sorted(related):
        topic = related[slug]
        try:
            ws = Workspace.open(root / slug)
        except WorkspaceNotFound:
            continue
        mode = workspace_run_plan(ws, catalog=catalog)["mode"]
        if mode == "attention":
            skipped.append(topic)
            continue
        if mode == "clean":
            continue
        entered.append(topic)

    ref_ids: set[str] = set()
    for topic in entered:
        ws = Workspace.open(root / topic.slug)
        ref_ids |= _ref_identities_for_topic(ws, catalog)

    return ViewRunPlan(
        start=start,
        end=end,
        topic_count=len(entered),
        ref_count=len(ref_ids),
        visible_undigested=visible,
        topics=tuple(entered),
        skipped_attention=tuple(skipped),
    )
