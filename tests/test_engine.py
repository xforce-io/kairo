from kairo.engine import step
from kairo.provider import StubProvider
from kairo.workspace import Workspace


def test_step_runs_text_chain_to_convergence(tmp_path):
    ws = Workspace.init(tmp_path)
    t = tmp_path / "meeting.txt"
    t.write_text("会议关键内容HELLO")
    ws.add([t])
    step(ws, StubProvider())
    rid = ws.list_reference_ids()[0]
    assert (ws.root / f"references/{rid}/digest.md").exists()
    # 正文经 digest 流到 understanding
    assert "会议关键内容HELLO" in (ws.root / "understanding.md").read_text()
    state = ws.read_state()
    assert f"references/{rid}/digest.md" in state.products
    assert "understanding.md" in state.targets


def test_step_audio_chain_asr_digest_compose_in_one_step(tmp_path):
    ws = Workspace.init(tmp_path)
    a = tmp_path / "rec.m4a"
    a.write_bytes(b"audio")
    ws.add([a])
    step(ws, StubProvider())
    rid = ws.list_reference_ids()[0]
    assert (ws.root / f"references/{rid}/transcript.md").exists()  # ASR
    assert (ws.root / f"references/{rid}/digest.md").exists()  # Digest
    # 整条骨牌链:STUB TRANSCRIPT 流到 understanding
    assert "STUB TRANSCRIPT" in (ws.root / "understanding.md").read_text()


def test_step_is_idempotent_after_convergence(tmp_path):
    ws = Workspace.init(tmp_path)
    t = tmp_path / "m.txt"
    t.write_text("内容")
    ws.add([t])
    step(ws, StubProvider())
    u1 = (ws.root / "understanding.md").read_text()
    progressed = step(ws, StubProvider())
    u2 = (ws.root / "understanding.md").read_text()
    assert progressed is False  # 收敛后无推进
    assert u1 == u2  # 不抖动


def test_step_writes_history_snapshot(tmp_path):
    ws = Workspace.init(tmp_path)
    t = tmp_path / "m.txt"
    t.write_text("内容")
    ws.add([t])
    step(ws, StubProvider())
    snaps = sorted((ws.root / ".kairo" / "history").iterdir())
    assert len(snaps) == 1
    assert (snaps[-1] / "understanding.md").exists()
    assert (snaps[-1] / "state.targets.json").exists()
