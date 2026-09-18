"""#396 Timeline 当前视图一次确认推进：只读计划。

确认前零 provider。Web 预览与 CLI 预览共用本函数。
"""

from __future__ import annotations

import datetime as dt
import re
import threading
import time
from dataclasses import dataclass, field, replace
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


@dataclass(frozen=True)
class ViewRunSnap:
    start: dt.date
    end: dt.date
    tags: tuple[str, ...]
    started_at: float
    finished: bool
    index: int
    topic_n: int
    current_slug: str | None
    current_task_id: str | None
    current_title: str
    ran: tuple[ViewTopic, ...]
    failed: tuple[ViewTopic, ...]
    skipped_attention: tuple[ViewTopic, ...]
    cancelled_running: tuple[ViewTopic, ...]
    cancelled_pending: tuple[ViewTopic, ...]


@dataclass
class ViewRunSession:
    """进程内存中的一次 Timeline 视图推进。不写 state.json。"""

    start: dt.date
    end: dt.date
    tags: tuple[str, ...]
    topics: tuple[ViewTopic, ...]
    skipped_attention: tuple[ViewTopic, ...]
    started_at: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    ready: threading.Event = field(default_factory=threading.Event, repr=False)
    index: int = 0
    current_slug: str | None = None
    current_task_id: str | None = None
    task_ids: list[str] = field(default_factory=list)
    cancel_rest: bool = False
    finished: bool = False
    ran: list[ViewTopic] = field(default_factory=list)
    failed: list[ViewTopic] = field(default_factory=list)
    cancelled_running: list[ViewTopic] = field(default_factory=list)
    cancelled_pending: list[ViewTopic] = field(default_factory=list)

    @classmethod
    def from_plan(
        cls, plan: ViewRunPlan, tags: list[str] | None = None
    ) -> ViewRunSession:
        return cls(
            start=plan.start,
            end=plan.end,
            tags=tuple(tags or ()),
            topics=plan.topics,
            skipped_attention=plan.skipped_attention,
        )

    def matches(
        self, start: dt.date, end: dt.date, tags: list[str] | None
    ) -> bool:
        return (
            self.start == start
            and self.end == end
            and self.tags == tuple(tags or ())
        )

    def topic(self, slug: str) -> ViewTopic | None:
        for item in self.topics:
            if item.slug == slug:
                return item
        return None

    def snapshot(self) -> ViewRunSnap:
        with self.lock:
            title = ""
            if self.current_slug:
                for item in self.topics:
                    if item.slug == self.current_slug:
                        title = item.title
                        break
            return ViewRunSnap(
                start=self.start,
                end=self.end,
                tags=self.tags,
                started_at=self.started_at,
                finished=self.finished,
                index=self.index,
                topic_n=len(self.topics),
                current_slug=self.current_slug,
                current_task_id=self.current_task_id,
                current_title=title,
                ran=tuple(self.ran),
                failed=tuple(self.failed),
                skipped_attention=self.skipped_attention,
                cancelled_running=tuple(self.cancelled_running),
                cancelled_pending=tuple(self.cancelled_pending),
            )

    def set_current(self, index: int, slug: str, task_id: str) -> None:
        with self.lock:
            self.index = index
            self.current_slug = slug
            self.current_task_id = task_id
            if task_id not in self.task_ids:
                self.task_ids.append(task_id)
            self.ready.set()

    def note_cancel(self, task_id: str) -> None:
        with self.lock:
            if self.finished:
                return
            if task_id == self.current_task_id:
                self.cancel_rest = True

    def should_stop(self) -> bool:
        with self.lock:
            return self.cancel_rest or self.finished

    def record(self, slug: str, kind: str) -> None:
        topic = self.topic(slug)
        if topic is None:
            topic = ViewTopic(slug=slug, title=slug)
        with self.lock:
            if kind == "ran":
                self.ran.append(topic)
            elif kind == "failed":
                self.failed.append(topic)
            elif kind == "cancelled":
                self.cancelled_running.append(topic)

    def abort_remaining(self, slugs: list[str]) -> None:
        with self.lock:
            for slug in slugs:
                topic = self.topic(slug)
                if topic is None:
                    topic = ViewTopic(slug=slug, title=slug)
                self.cancelled_pending.append(topic)

    def mark_finished(self) -> None:
        with self.lock:
            self.finished = True
            self.current_slug = None
            self.current_task_id = None
            self.ready.set()


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
