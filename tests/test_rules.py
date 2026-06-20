import yaml

from kairo.models import State
from kairo.provider import AgentResult, StubProvider, _scan_artifacts
from kairo.rules import AsrRule, ComposeRule, DigestRule
from kairo.workspace import Workspace


def _save_constitution(ws, con):
    (ws.root / "constitution.yaml").write_text(
        yaml.safe_dump(con.model_dump(), allow_unicode=True, sort_keys=False)
    )


class _RunOnlyProvider:
    """只实现 run、不实现 complete —— 锁定 rules 走 agent 接口(#4)。"""

    name = "runonly"
    model = "runonly"

    def __init__(self):
        self.calls = []

    def run(self, config, signal=None):
        self.calls.append(config)
        config.artifact_dir.mkdir(parents=True, exist_ok=True)
        content = f"RUN-ONLY\n\n{config.context}"  # echo context 供溯源断言
        (config.artifact_dir / (config.artifact or "output.md")).write_text(content)
        return AgentResult(artifacts=_scan_artifacts(config.artifact_dir))


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


def test_asr_rule_parametrized_consumes_produces(tmp_path, monkeypatch):
    """#3:AsrRule 的 consumes/produces 可参数化(声明驱动,复用 stub/no-asr 占位逻辑)。"""
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = Workspace.init(tmp_path)
    v = tmp_path / "clip.mp4"
    v.write_bytes(b"fake video")
    rid = ws.add([v], role="video")
    state = State()
    rule = AsrRule(ws, consumes=["video"], produces="transcript", backend="asr-stub")
    assert [it.key for it in rule.discover()] == [f"references/{rid}/transcript.md"]
    rule.discover()[0].run(state)
    assert (ws.root / f"references/{rid}/transcript.md").exists()
    assert "transcript" in [f.role for f in ws.read_manifest(rid).forms]


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


# ---- rules 走 agent run 接口(#4),不再依赖 complete ----


def test_digest_uses_agent_run_interface(tmp_path):
    ws = Workspace.init(tmp_path)
    t = tmp_path / "m.txt"
    t.write_text("正文DELTA")
    rid = ws.add([t])
    prov = _RunOnlyProvider()
    state = State()
    DigestRule(ws, prov).discover()[0].run(state)
    assert prov.calls, "DigestRule 应通过 run() 调用 provider"
    assert "正文DELTA" in (ws.root / f"references/{rid}/digest.md").read_text()


def test_digest_body_roles_data_driven(tmp_path):
    """#3:DigestRule 正文来源由 constitution.body_roles 声明,可加新 role 不改码。"""
    ws = Workspace.init(tmp_path)
    con = ws.constitution
    con.body_roles = ["memo"]  # 只认 memo role 作正文
    _save_constitution(ws, con)
    ws2 = Workspace(ws.root)
    src = tmp_path / "x.txt"
    src.write_text("备忘正文MEMO")
    rid = ws2.add([src], role="memo")
    items = DigestRule(ws2, StubProvider()).discover()
    assert [it.key for it in items] == [f"references/{rid}/digest.md"]


def test_compose_uses_agent_run_interface(tmp_path):
    ws = Workspace.init(tmp_path)
    t = tmp_path / "m.txt"
    t.write_text("x")
    rid = ws.add([t])
    _make_digest(ws, rid, "纪要QQQ")
    prov = _RunOnlyProvider()
    state = State()
    ComposeRule(ws, prov).discover(state)[0].run(state)
    assert prov.calls, "ComposeRule 应通过 run() 调用 provider"
    assert "纪要QQQ" in (ws.root / "understanding.md").read_text()


# ---- #10:输出纪律(P1 无旁白 / P2 不内联 / P4 来源是标签 / P6 可疑专名）----


def test_compose_persona_carries_output_discipline(tmp_path):
    ws = Workspace.init(tmp_path)
    t = tmp_path / "m.txt"
    t.write_text("x")
    rid = ws.add([t])
    _make_digest(ws, rid, "纪要")
    prov = _RunOnlyProvider()
    state = State()
    ComposeRule(ws, prov).discover(state)[0].run(state)
    persona = prov.calls[0].persona
    assert "只输出文档正文" in persona  # P1 无旁白/提议
    assert "只产出当前这一个文档" in persona  # P2 不内联其它文档
    assert "溯源标签" in persona  # P4 来源是标签非文件
    assert "待核" in persona  # P6 可疑专名标 ⚠️


def test_digest_persona_carries_output_discipline(tmp_path):
    ws = Workspace.init(tmp_path)
    t = tmp_path / "m.txt"
    t.write_text("正文")
    ws.add([t])
    prov = _RunOnlyProvider()
    state = State()
    DigestRule(ws, prov).discover()[0].run(state)
    persona = prov.calls[0].persona
    assert "只输出文档正文" in persona  # P1
    assert "待核" in persona  # P6


# ---- #13:源分层 corpus(基线)/ stream(观测),prompt 级 ----


def test_compose_corpus_is_reference_layer_not_folded_block(tmp_path):
    """v2:corpus 不再折成 ·基线 块;改为 persona 注入基线 hint + 文件清单,stream 标 ·观测。"""
    ws = Workspace.init(tmp_path)
    sm = tmp_path / "meeting.txt"
    sm.write_text("会议x")
    rs = ws.add([sm])  # stream(默认)
    cp = tmp_path / "wp.md"
    cp.write_text("白皮书基线内容ZZZ")
    ws.add([cp], source_class="corpus")  # corpus → 只读参考层
    _make_digest(ws, rs, "观测纪要")
    prov = _RunOnlyProvider()
    state = State()
    ComposeRule(ws, prov).discover(state)[0].run(state)  # [0]=understanding
    ctx = prov.calls[0].context
    assert "·观测" in ctx  # 有参考层时 stream 块标 ·观测
    assert "白皮书基线内容ZZZ" not in ctx  # corpus 原文不进 context 折叠块
    persona = prov.calls[0].persona
    assert "基线参考" in persona  # 注入了基线参考前言
    assert "校正" in persona  # corpus hint(术语权威/校正)流入
    assert "wp.md" in persona  # 列出 corpus 文件供 Read


def test_compose_single_class_keeps_today_behavior(tmp_path):
    """纯 stream(单类):不打 ·标签、不注入 hint,与今天逐字一致。"""
    ws = Workspace.init(tmp_path)
    sm = tmp_path / "meeting.txt"
    sm.write_text("会议x")
    rs = ws.add([sm])
    _make_digest(ws, rs, "观测纪要")
    prov = _RunOnlyProvider()
    state = State()
    ComposeRule(ws, prov).discover(state)[0].run(state)
    ctx = prov.calls[0].context
    assert "·观测" not in ctx  # 单类不打标签
    persona = prov.calls[0].persona
    assert "源分类" not in persona  # 单类不注入前言

