import yaml

from kairo.engine import accept, re_step, step
from kairo.models import Transform
from kairo.provider import AgentResult, StubProvider, _scan_artifacts
from kairo.workspace import Workspace


class _NonDeterministicProvider:
    """每次 run 输出都不同 —— 验证收敛锚输入指纹、不依赖输出确定性(#4 §5)。"""

    name = "nondet"
    model = "nondet"

    def __init__(self):
        self.n = 0

    def run(self, config, signal=None):
        self.n += 1
        config.artifact_dir.mkdir(parents=True, exist_ok=True)
        content = f"OUTPUT #{self.n}\n{config.context}"  # 每次内容不同
        (config.artifact_dir / (config.artifact or "output.md")).write_text(content)
        return AgentResult(artifacts=_scan_artifacts(config.artifact_dir))


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


def test_step_binary_chain_doc2text_digest_compose_in_one_step(tmp_path):
    """二进制链(#15):docx --markitdown--> source_text → digest → understanding,一步到底。

    transform 走真实 markitdown(进程内,无需 stub);agent 用 StubProvider。
    source_text 不经 Normalize(只规范化机器转写的 transcript)。
    """
    from pathlib import Path

    ws = Workspace.init(tmp_path)
    fixture = Path(__file__).parent / "fixtures" / "sample.docx"
    doc = tmp_path / "sample.docx"
    doc.write_bytes(fixture.read_bytes())
    ws.add([doc])
    step(ws, StubProvider())
    rid = ws.list_reference_ids()[0]
    assert (ws.root / f"references/{rid}/source_text.md").exists()  # doc2text
    assert not (ws.root / f"references/{rid}/prose.md").exists()  # 不规范化
    assert (ws.root / f"references/{rid}/digest.md").exists()  # Digest
    # 正文(docx 标题)流到 understanding
    assert "康医通系统" in (ws.root / "understanding.md").read_text()


def test_step_audio_chain_asr_digest_compose_in_one_step(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")  # stub ASR 才走通音频链
    ws = Workspace.init(tmp_path)
    a = tmp_path / "rec.m4a"
    a.write_bytes(b"audio")
    ws.add([a])
    step(ws, StubProvider())
    rid = ws.list_reference_ids()[0]
    assert (ws.root / f"references/{rid}/transcript.md").exists()  # ASR
    assert (ws.root / f"references/{rid}/prose.md").exists()  # Normalize(#30)
    assert (ws.root / f"references/{rid}/digest.md").exists()  # Digest
    # 整条骨牌链:STUB TRANSCRIPT 流到 understanding
    assert "STUB TRANSCRIPT" in (ws.root / "understanding.md").read_text()


def test_step_text_chain_does_not_produce_prose(tmp_path):
    """#30:人给的文本源(origin=added)不规范化,无 prose.md;直接 digest 原文。"""
    ws = Workspace.init(tmp_path)
    t = tmp_path / "meeting.txt"
    t.write_text("人给的原文")
    ws.add([t])
    step(ws, StubProvider())
    rid = ws.list_reference_ids()[0]
    assert not (ws.root / f"references/{rid}/prose.md").exists()
    assert (ws.root / f"references/{rid}/digest.md").exists()


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


def test_step_produces_both_layers_with_upstream_flow(tmp_path):
    ws = Workspace.init(tmp_path)
    t = tmp_path / "m.txt"
    t.write_text("会议要点ZZZ")
    ws.add([t])
    step(ws, StubProvider())
    assert (ws.root / "understanding.md").exists()
    assert (ws.root / "assessment.md").exists()
    # assessment 输入含当前 understanding(上游流入)
    assert "understanding.md" in (ws.root / "assessment.md").read_text()
    # 级联记账:assessment 记了 upstream_hash
    ts = ws.read_state().targets["assessment.md"]
    assert "understanding.md" in ts.upstream_hash


def test_assessment_cascades_when_understanding_changes(tmp_path):
    ws = Workspace.init(tmp_path)
    t = tmp_path / "m.txt"
    t.write_text("初始材料")
    ws.add([t])
    step(ws, StubProvider())
    up1 = ws.read_state().targets["assessment.md"].upstream_hash["understanding.md"]
    # 加新 reference → understanding 变 → assessment 上游变,级联重综合
    t2 = tmp_path / "n.txt"
    t2.write_text("新增材料")
    ws.add([t2])
    step(ws, StubProvider())
    up2 = ws.read_state().targets["assessment.md"].upstream_hash["understanding.md"]
    assert up2 != up1


def test_two_layer_step_is_idempotent(tmp_path):
    ws = Workspace.init(tmp_path)
    t = tmp_path / "m.txt"
    t.write_text("内容")
    ws.add([t])
    step(ws, StubProvider())
    a1 = (ws.root / "assessment.md").read_text()
    assert step(ws, StubProvider()) is False  # 两层都收敛
    assert (ws.root / "assessment.md").read_text() == a1


def test_re_step_document_discards_edit_and_recomposes(tmp_path):
    ws = Workspace.init(tmp_path)
    t = tmp_path / "m.txt"
    t.write_text("内容")
    ws.add([t])
    step(ws, StubProvider())
    canonical = (ws.root / "understanding.md").read_text()
    (ws.root / "understanding.md").write_text("人工乱改")
    re_step(ws, StubProvider(), "understanding.md")
    # 文档级 re-step 丢弃手改、整篇重综合回规范内容
    assert (ws.root / "understanding.md").read_text() == canonical


def test_re_step_reference_recomposes_digest_from_prose(tmp_path, monkeypatch):
    """#30:re-step 单个 audio reference 从现有 prose 重产 digest;prose 是上游产物不重产。"""
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = Workspace.init(tmp_path)
    a = tmp_path / "rec.m4a"
    a.write_bytes(b"audio")
    ws.add([a])
    step(ws, StubProvider())
    rid = ws.list_reference_ids()[0]
    prose_before = (ws.root / f"references/{rid}/prose.md").read_text()
    re_step(ws, StubProvider(), rid)
    assert (ws.root / f"references/{rid}/digest.md").exists()  # digest 重产
    assert (ws.root / f"references/{rid}/prose.md").read_text() == prose_before  # prose 不动


def test_re_step_all_recomposes_every_target(tmp_path):
    ws = Workspace.init(tmp_path)
    t = tmp_path / "m.txt"
    t.write_text("内容")
    ws.add([t])
    step(ws, StubProvider())
    (ws.root / "understanding.md").write_text("乱改1")
    (ws.root / "assessment.md").write_text("乱改2")
    re_step(ws, StubProvider())  # 全量
    assert "乱改" not in (ws.root / "understanding.md").read_text()
    assert "乱改" not in (ws.root / "assessment.md").read_text()


def test_manual_edit_blocks_compose_without_overwriting(tmp_path):
    ws = Workspace.init(tmp_path)
    t = tmp_path / "m.txt"
    t.write_text("内容")
    ws.add([t])
    step(ws, StubProvider())
    (ws.root / "understanding.md").write_text("人工改了事实")
    step(ws, StubProvider())  # 检测手改
    ts = ws.read_state().targets["understanding.md"]
    assert ts.status == "blocked"
    assert ts.reason == "manual-edit"
    assert (ws.root / "understanding.md").read_text() == "人工改了事实"  # 不静默覆盖


def test_accept_pins_edit_as_new_baseline_and_unblocks(tmp_path):
    ws = Workspace.init(tmp_path)
    t = tmp_path / "m.txt"
    t.write_text("内容")
    ws.add([t])
    step(ws, StubProvider())
    (ws.root / "understanding.md").write_text("人工修正")
    step(ws, StubProvider())  # blocked
    accept(ws, "understanding.md")
    assert ws.read_state().targets["understanding.md"].status == "ok"
    step(ws, StubProvider())  # 不再 blocked
    assert (ws.root / "understanding.md").read_text() == "人工修正"  # 手改成新基线


def test_drift_counter_resets_on_full_recompose(tmp_path):
    ws = Workspace.init(tmp_path)
    t = tmp_path / "m.txt"
    t.write_text("初始")
    ws.add([t])
    step(ws, StubProvider())  # A:刷新漂移基线
    ts = ws.read_state().targets["understanding.md"]
    assert len(ts.folded) - len(ts.last_major_folded) == 0
    t2 = tmp_path / "n.txt"
    t2.write_text("新增")
    ws.add([t2])
    step(ws, StubProvider())  # B:增量,漂移 +1
    ts = ws.read_state().targets["understanding.md"]
    assert len(ts.folded) - len(ts.last_major_folded) == 1
    re_step(ws, StubProvider(), "understanding.md")  # A:重置漂移
    ts = ws.read_state().targets["understanding.md"]
    assert len(ts.folded) - len(ts.last_major_folded) == 0


def test_convergence_anchors_input_not_output(tmp_path):
    """#4 §5:provider 非确定时,输入未变 → 不重跑 → 收敛。锚 input_hash,不锚 output。"""
    ws = Workspace.init(tmp_path)
    t = tmp_path / "m.txt"
    t.write_text("内容")
    ws.add([t])
    prov = _NonDeterministicProvider()
    step(ws, prov)
    calls_after_first = prov.n
    u1 = (ws.root / "understanding.md").read_text()
    progressed = step(ws, prov)  # 输入未变
    assert progressed is False  # 收敛:不重跑
    assert prov.n == calls_after_first  # provider 未被再调(锚输入,非输出)
    assert (ws.root / "understanding.md").read_text() == u1  # 文档不抖动


def test_nondeterministic_provider_still_detects_manual_edit(tmp_path):
    """#4 §5:手改检测靠 output_hash 基线,与 provider 是否确定无关。"""
    ws = Workspace.init(tmp_path)
    t = tmp_path / "m.txt"
    t.write_text("内容")
    ws.add([t])
    prov = _NonDeterministicProvider()
    step(ws, prov)
    (ws.root / "understanding.md").write_text("人工改动")
    step(ws, prov)
    ts = ws.read_state().targets["understanding.md"]
    assert ts.status == "blocked" and ts.reason == "manual-edit"
    assert (ws.root / "understanding.md").read_text() == "人工改动"  # 不静默覆盖


def test_new_text_resource_type_zero_code_change(tmp_path):
    """#3 实证:加一种文本资源(.md→source_text)只声明 constitution、不改码,端到端跑通。"""
    ws = Workspace.init(tmp_path)
    con = ws.constitution
    con.roles_by_ext[".md"] = "source_text"  # 声明:.md 是正文资源
    (ws.root / "constitution.yaml").write_text(
        yaml.safe_dump(con.model_dump(), allow_unicode=True, sort_keys=False)
    )
    ws2 = Workspace(ws.root)
    doc = tmp_path / "paper.md"
    doc.write_text("白皮书要点MD")
    ws2.add([doc])  # guess_role → source_text(声明驱动)
    step(ws2, StubProvider())
    # source_text 经 Digest→Compose 流到 understanding,全程零改码
    assert "白皮书要点MD" in (ws2.root / "understanding.md").read_text()


def test_new_audio_like_transform_zero_code_change(tmp_path, monkeypatch):
    """#3 实证:加 video→transcript 转换,只声明 constitution(role + transform),不改码。"""
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = Workspace.init(tmp_path)
    con = ws.constitution
    con.roles_by_ext[".mp4"] = "video"
    con.transforms.append(
        Transform(
            name="video-asr",
            consumes=["video"],
            produces="transcript",
            backend="asr-stub",
        )
    )
    (ws.root / "constitution.yaml").write_text(
        yaml.safe_dump(con.model_dump(), allow_unicode=True, sort_keys=False)
    )
    ws2 = Workspace(ws.root)
    v = tmp_path / "clip.mp4"
    v.write_bytes(b"fake video")
    ws2.add([v])  # role → video(声明驱动)
    step(ws2, StubProvider())
    rid = ws2.list_reference_ids()[0]
    # video→transcript 转换由声明装配并跑通,经 Digest→Compose 流到 understanding
    assert (ws2.root / f"references/{rid}/transcript.md").exists()
    assert "STUB TRANSCRIPT" in (ws2.root / "understanding.md").read_text()


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
