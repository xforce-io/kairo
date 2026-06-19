"""版本快照(.kairo/history)。每次 step 收敛后存 {综合文档 + state.targets 段}。

只快照文档 + targets;products 段与 references/ 不入快照、不回退(digest 源侧、可重生)。
"""

from __future__ import annotations

import json
from pathlib import Path

from kairo.models import State


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
