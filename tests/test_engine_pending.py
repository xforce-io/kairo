# tests/test_engine_pending.py
import pytest
from kairo.engine import pending, step
from kairo.provider import select_provider
from kairo.workspace import Workspace


def test_pending_counts_stale_then_empty_after_step(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = Workspace.init(tmp_path, topic="t")
    (tmp_path / "m.txt").write_text("会议内容")
    ws.add([tmp_path / "m.txt"])
    # step 前:digest + 两个 target 待办 → 有 stale
    assert len(pending(ws)) > 0
    step(ws, select_provider())
    # 收敛后:无 stale
    assert pending(ws) == []


def test_pending_does_not_mutate_state(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = Workspace.init(tmp_path, topic="t")
    (tmp_path / "m.txt").write_text("x")
    ws.add([tmp_path / "m.txt"])
    before = ws.state_path.read_text()
    pending(ws)
    assert ws.state_path.read_text() == before  # 只读,不落盘
