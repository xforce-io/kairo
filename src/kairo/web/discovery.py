"""workspace 发现层:扫父目录 → 各 workspace 轻量摘要(dashboard 用,不读正文)。"""

from __future__ import annotations

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
    )


def scan_workspaces(root: Path) -> list[WorkspaceSummary]:
    """扫 root 下一层子目录,凡含 constitution.yaml 且可打开者即 workspace。"""
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
