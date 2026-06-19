from kairo.models import State
from kairo.provider import StubProvider
from kairo.rules import AsrRule, ComposeRule, DigestRule
from kairo.workspace import Workspace


def _make_digest(ws, ref_id, content):
    d = ws.root / "references" / ref_id / "digest.md"
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_text(content)
    return f"references/{ref_id}/digest.md"


def _add_audio(ws, tmp_path, name="rec.m4a"):
    a = tmp_path / name
    a.write_bytes(b"fake audio bytes")
    return ws.add([a])


# ---- ASR ----


def test_asr_discovers_audio_without_transcript(tmp_path):
    ws = Workspace.init(tmp_path)
    rid = _add_audio(ws, tmp_path)
    items = AsrRule(ws).discover()
    assert [it.key for it in items] == [f"references/{rid}/transcript.md"]


def test_asr_run_produces_marked_stub_transcript_and_appends_form(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")  # stub 模式才产占位转写
    ws = Workspace.init(tmp_path)
    rid = _add_audio(ws, tmp_path)
    state = State()
    AsrRule(ws).discover()[0].run(state)
    transcript = ws.root / "references" / rid / "transcript.md"
    assert transcript.is_file()
    assert "STUB TRANSCRIPT" in transcript.read_text()
    # manifest 追加了 transcript form
    roles = [f.role for f in ws.read_manifest(rid).forms]
    assert "transcript" in roles
    # products 记账
    assert f"references/{rid}/transcript.md" in state.products


def test_asr_skips_when_transcript_already_present(tmp_path):
    ws = Workspace.init(tmp_path)
    a = tmp_path / "rec.m4a"
    a.write_bytes(b"audio")
    t = tmp_path / "rec.txt"
    t.write_text("用户给的真实转写稿")
    ws.add([a, t])  # roles: audio, transcript
    assert AsrRule(ws).discover() == []


def test_asr_blocks_no_asr_in_real_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("KAIRO_STUB", raising=False)  # 真实模式无 ASR 后端
    ws = Workspace.init(tmp_path)
    rid = _add_audio(ws, tmp_path)
    state = State()
    AsrRule(ws).discover()[0].run(state)
    ps = state.products[f"references/{rid}/transcript.md"]
    assert ps.status == "blocked" and ps.reason == "no-asr"
    assert not (ws.root / "references" / rid / "transcript.md").exists()


def test_asr_blocks_missing_source_when_audio_gone(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = Workspace.init(tmp_path)
    a = tmp_path / "rec.m4a"
    a.write_bytes(b"audio")
    rid = ws.add([a])
    a.unlink()  # 源丢失
    state = State()
    AsrRule(ws).discover()[0].run(state)
    ps = state.products[f"references/{rid}/transcript.md"]
    assert ps.status == "blocked" and ps.reason == "missing-source"


# ---- Digest ----


def test_digest_discovers_reference_with_body_no_digest(tmp_path):
    ws = Workspace.init(tmp_path)
    t = tmp_path / "meeting.txt"
    t.write_text("会议正文内容")
    rid = ws.add([t])  # role transcript
    items = DigestRule(ws, StubProvider()).discover()
    assert [it.key for it in items] == [f"references/{rid}/digest.md"]


def test_digest_run_produces_digest_carrying_body(tmp_path):
    ws = Workspace.init(tmp_path)
    t = tmp_path / "meeting.txt"
    t.write_text("会议正文内容ABC")
    rid = ws.add([t])
    state = State()
    DigestRule(ws, StubProvider()).discover()[0].run(state)
    digest = ws.root / "references" / rid / "digest.md"
    assert digest.is_file()
    assert "会议正文内容ABC" in digest.read_text()  # 正文流过 digest
    assert f"references/{rid}/digest.md" in state.products


def test_digest_skips_when_no_body_available(tmp_path):
    ws = Workspace.init(tmp_path)
    a = tmp_path / "rec.m4a"
    a.write_bytes(b"audio")
    ws.add([a])  # 只有 audio,尚无 transcript → Digest 无正文可用
    assert DigestRule(ws, StubProvider()).discover() == []


# ---- Compose ----


def test_compose_discovers_target_with_unfolded_digest(tmp_path):
    ws = Workspace.init(tmp_path)
    t = tmp_path / "m.txt"
    t.write_text("x")
    rid = ws.add([t])
    _make_digest(ws, rid, "纪要内容")
    state = State()
    items = ComposeRule(ws, StubProvider()).discover(state)
    assert [it.key for it in items] == ["understanding.md", "assessment.md"]


def test_compose_run_folds_digest_into_understanding_with_source(tmp_path):
    ws = Workspace.init(tmp_path)
    t = tmp_path / "m.txt"
    t.write_text("x")
    rid = ws.add([t])
    digest_path = _make_digest(ws, rid, "关键纪要XYZ")
    state = State()
    ComposeRule(ws, StubProvider()).discover(state)[0].run(state)
    u = ws.root / "understanding.md"
    assert u.is_file()
    assert "关键纪要XYZ" in u.read_text()  # digest 流入文档
    assert digest_path in u.read_text()  # 挂源(来源标注)
    assert digest_path in state.targets["understanding.md"].folded  # 记账


def test_compose_converges_after_folding(tmp_path):
    ws = Workspace.init(tmp_path)
    t = tmp_path / "m.txt"
    t.write_text("x")
    rid = ws.add([t])
    _make_digest(ws, rid, "纪要")
    state = State()
    rule = ComposeRule(ws, StubProvider())
    for item in rule.discover(state):  # 两层都融(understanding → assessment)
        item.run(state)
    # 融完后无未融入 Δ、上游未变 → 收敛
    assert rule.discover(state) == []

