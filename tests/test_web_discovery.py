from kairo.web.discovery import scan_workspaces
from kairo.workspace import Workspace


def _mk(root, name, topic):
    ws = Workspace.init(root / name, topic=topic)
    return ws


def test_scan_finds_workspaces_sorted(tmp_path):
    _mk(tmp_path, "b-ws", "beta")
    _mk(tmp_path, "a-ws", "alpha")
    (tmp_path / "not-a-ws").mkdir()  # 无 constitution.yaml,跳过
    out = scan_workspaces(tmp_path)
    assert [s.slug for s in out] == ["a-ws", "b-ws"]
    assert out[0].topic == "alpha"


def test_summary_counts_refs_and_classes(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = _mk(tmp_path, "ws", "t")
    (tmp_path / "m.txt").write_text("会议")
    cdir = tmp_path / "corpus_src"
    cdir.mkdir()
    (cdir / "x.md").write_text("基线")
    ws.add([tmp_path / "m.txt"])              # stream
    ws.add([cdir], source_class="corpus")     # corpus tree
    out = scan_workspaces(tmp_path)
    s = next(x for x in out if x.slug == "ws")
    assert s.ref_count == 2
    assert s.stream_count == 1 and s.corpus_count == 1
    assert s.stale_count > 0  # step 前有待办


def test_summary_stale_zero_after_step(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    from kairo.engine import step
    from kairo.provider import select_provider
    ws = _mk(tmp_path, "ws", "t")
    (tmp_path / "m.txt").write_text("x")
    ws.add([tmp_path / "m.txt"])
    step(ws, select_provider())
    s = scan_workspaces(tmp_path)[0]
    assert s.stale_count == 0
