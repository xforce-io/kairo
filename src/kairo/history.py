"""版本快照(.kairo/history)。每次 step 收敛后存 {综合文档 + state.targets 段}。

只快照文档 + targets;products 段与 references/ 不入快照、不回退(digest 源侧、可重生)。
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path

from kairo.models import State, TargetState


def snapshot(ws, state: State) -> Path:
    hist = ws.root / ".kairo" / "history"
    hist.mkdir(parents=True, exist_ok=True)
    seq = len([p for p in hist.iterdir() if p.is_dir()])
    snap = hist / f"{seq:04d}"
    snap.mkdir()
    for target_path in state.targets:
        src = ws.root / target_path
        if src.exists():
            (snap / Path(target_path).name).write_text(src.read_text())
    targets_dump = {k: v.model_dump() for k, v in state.targets.items()}
    (snap / "state.targets.json").write_text(
        json.dumps(targets_dump, ensure_ascii=False, indent=2)
    )
    return snap


def _history_dir(ws) -> Path:
    return ws.root / ".kairo" / "history"


def list_snapshots(ws) -> list[str]:
    hist = _history_dir(ws)
    if not hist.exists():
        return []
    return sorted(p.name for p in hist.iterdir() if p.is_dir())


def rollback(ws, seq: str) -> None:
    """恢复文档 + state.targets 段到某版本;不动 references/ 与 products 段。"""
    snap = _history_dir(ws) / seq
    targets = json.loads((snap / "state.targets.json").read_text())
    state = ws.read_state()
    state.targets = {k: TargetState.model_validate(v) for k, v in targets.items()}
    for target_path in targets:
        src = snap / Path(target_path).name
        if src.exists():
            (ws.root / target_path).write_text(src.read_text())
    ws.write_state(state)


def diff_worktree(ws, seq: str | None = None) -> str:
    """工作态文档 vs 某快照(默认最近)的 unified diff —— 自带,不依赖 git。"""
    snaps = list_snapshots(ws)
    if not snaps:
        return ""
    seq = seq or snaps[-1]
    snap = _history_dir(ws) / seq
    out: list[str] = []
    for target in ws.constitution.targets:
        cur_f = ws.root / target.path
        old_f = snap / Path(target.path).name
        cur = cur_f.read_text().splitlines() if cur_f.exists() else []
        old = old_f.read_text().splitlines() if old_f.exists() else []
        if cur != old:
            out.append(
                "\n".join(
                    difflib.unified_diff(
                        old,
                        cur,
                        fromfile=f"{seq}/{target.path}",
                        tofile=f"work/{target.path}",
                        lineterm="",
                    )
                )
            )
    return "\n".join(out)
