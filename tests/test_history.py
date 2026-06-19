from kairo.engine import step
from kairo.history import diff_worktree, list_snapshots, rollback
from kairo.provider import StubProvider
from kairo.workspace import Workspace


def _ws_with_one_step(tmp_path):
    ws = Workspace.init(tmp_path)
    t = tmp_path / "m.txt"
    t.write_text("内容")
    ws.add([t])
    step(ws, StubProvider())
    return ws


def test_list_snapshots_after_step(tmp_path):
    ws = _ws_with_one_step(tmp_path)
    assert list_snapshots(ws) == ["0000"]


def test_rollback_restores_document_and_targets(tmp_path):
    ws = _ws_with_one_step(tmp_path)
    u_v0 = (ws.root / "understanding.md").read_text()
    n0 = len(ws.read_state().targets["understanding.md"].folded)
    # 加新材料 → snapshot 0001,understanding 变
    t2 = tmp_path / "n.txt"
    t2.write_text("新增材料")
    ws.add([t2])
    step(ws, StubProvider())
    assert (ws.root / "understanding.md").read_text() != u_v0
    # rollback 到 0000:文档 + targets 段恢复
    rollback(ws, "0000")
    assert (ws.root / "understanding.md").read_text() == u_v0
    assert len(ws.read_state().targets["understanding.md"].folded) == n0


def test_diff_worktree_flags_manual_edit(tmp_path):
    ws = _ws_with_one_step(tmp_path)
    (ws.root / "understanding.md").write_text("手改了事实")
    d = diff_worktree(ws)
    assert "understanding.md" in d  # 工作态 vs 最近快照有差异
