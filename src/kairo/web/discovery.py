"""workspace 发现层:扫父目录 → 各 workspace 轻量摘要(dashboard 用,不读正文)。"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path

from kairo.engine import pending
from kairo.workspace import Workspace, WorkspaceNotFound


@dataclass
class WorkspaceSummary:
    slug: str
    topic: str
    path: str
    ref_count: int
    stream_count: int
    corpus_count: int
    blocked_count: int
    stale_count: int
    last_activity: datetime.datetime


def last_activity(ws: Workspace) -> datetime.datetime:
    """有界 kairo 自有路径的 max mtime(aware,本机偏移)。constitution.yaml 必存在。"""
    root = ws.root
    paths: list[Path] = [root / "constitution.yaml"]
    state = root / ".kairo" / "state.json"
    if state.is_file():
        paths.append(state)
    for target in ws.constitution.targets:
        body = root / target.path
        if body.is_file():
            paths.append(body)
    meetings = root / "MEETINGS.md"
    if meetings.is_file():
        paths.append(meetings)
    refs = root / "references"
    if refs.is_dir():
        for child in refs.iterdir():
            man = child / "manifest.yaml"
            if man.is_file():
                paths.append(man)
    latest = max(p.stat().st_mtime for p in paths if p.is_file())
    return datetime.datetime.fromtimestamp(latest).astimezone()


def activity_label(
    when: datetime.datetime,
    *,
    now: datetime.datetime,
    today: str,
    yesterday: str,
) -> str:
    """本机日历相对文案:今天 HH:MM / 昨天 / YYYY-MM-DD。"""
    local = when.astimezone()
    now_local = now.astimezone()
    day = local.date()
    today_d = now_local.date()
    if day == today_d:
        return today.format(t=local.strftime("%H:%M"))
    if day == today_d - datetime.timedelta(days=1):
        return yesterday
    return day.isoformat()


def summarize(ws: Workspace) -> WorkspaceSummary:
    con = ws.constitution
    state = ws.read_state()
    stream = corpus = 0
    for ref_id in ws.list_reference_ids():
        cls = ws.read_manifest(ref_id).source_class
        sc = con.source_classes.get(cls)
        if sc is not None and not sc.fold:
            corpus += 1
        else:
            stream += 1
    blocked = sum(1 for p in state.products.values() if p.status == "blocked")
    blocked += sum(1 for t in state.targets.values() if t.status == "blocked")
    return WorkspaceSummary(
        slug=ws.root.name,
        topic=con.topic,
        path=str(ws.root),
        ref_count=stream + corpus,
        stream_count=stream,
        corpus_count=corpus,
        blocked_count=blocked,
        stale_count=len(pending(ws)),
        last_activity=last_activity(ws),
    )


def scan_workspaces(root: Path) -> list[WorkspaceSummary]:
    """扫 root 下一层子目录,凡含 constitution.yaml 且可打开者即 workspace。

    返回 slug 字母序(CLI list 稳定)。last_activity 只供 Web 再排。
    """
    root = Path(root)
    out: list[WorkspaceSummary] = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        if not (d / "constitution.yaml").exists():
            continue
        try:
            ws = Workspace.open(d)
        except WorkspaceNotFound:
            continue
        out.append(summarize(ws))
    return out
