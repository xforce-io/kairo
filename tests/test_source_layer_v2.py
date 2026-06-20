"""#13 v2:corpus 从「被折叠的源」改为「agent 只读参考层」。

- stream(观测):digest → fold,内容 hash 驱动收敛(v1 不变)。
- corpus(基线):不 digest、不进 fold-delta/staleness;以只读参考目录暴露给 fold agent。
语义由 constitution 的 SourceClass.fold 声明,引擎不硬编码 "corpus"。
"""

from kairo.models import Constitution, State
from kairo.provider import AgentResult, StubProvider, _scan_artifacts
from kairo.rules import ComposeRule, DigestRule
from kairo.workspace import Workspace


class _CaptureProvider:
    """捕获每次 run 的 config(persona/context/read_dirs),echo context 供溯源断言。"""

    name = "capture"
    model = "capture"

    def __init__(self):
        self.calls = []

    def run(self, config, signal=None):
        self.calls.append(config)
        config.artifact_dir.mkdir(parents=True, exist_ok=True)
        (config.artifact_dir / (config.artifact or "output.md")).write_text(
            f"CAPTURE\n\n{config.context}"
        )
        return AgentResult(artifacts=_scan_artifacts(config.artifact_dir))


def _add_stream(ws, tmp_path, name="meeting.txt", body="会议正文"):
    p = tmp_path / name
    p.write_text(body)
    return ws.add([p])


def _add_corpus(ws, tmp_path, name="wp.md", body="白皮书正文"):
    p = tmp_path / name
    p.write_text(body)
    return ws.add([p], source_class="corpus")


def _make_digest(ws, ref_id, content):
    d = ws.root / "references" / ref_id / "digest.md"
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_text(content)
    return f"references/{ref_id}/digest.md"


# ---- Increment 1:SourceClass.fold 声明源是否折叠 ----


def test_corpus_class_defaults_to_reference_not_folded():
    """corpus 默认 fold=False(只读参考层);stream fold=True(折叠事件)。"""
    con = Constitution()
    assert con.source_classes["stream"].fold is True
    assert con.source_classes["corpus"].fold is False


# ---- Increment 2:DigestRule 跳过 fold=False 的源(corpus 不 digest) ----


def test_digest_skips_corpus_reference(tmp_path):
    """corpus(fold=False)不被 digest;stream 仍 digest。"""
    ws = Workspace.init(tmp_path)
    rs = _add_stream(ws, tmp_path)
    rc = _add_corpus(ws, tmp_path)
    keys = [it.key for it in DigestRule(ws, StubProvider()).discover()]
    assert f"references/{rs}/digest.md" in keys  # stream 被 digest
    assert f"references/{rc}/digest.md" not in keys  # corpus 不被 digest


# ---- Increment 3:ComposeRule 把 corpus digest 排除出 delta/folded ----


def test_compose_excludes_corpus_digest_from_fold(tmp_path):
    """即便磁盘存在 corpus digest(v1 遗留),也不进 delta/folded。"""
    ws = Workspace.init(tmp_path)
    rs = _add_stream(ws, tmp_path)
    rc = _add_corpus(ws, tmp_path)
    stream_d = _make_digest(ws, rs, "观测纪要")
    corpus_d = _make_digest(ws, rc, "基线纪要")  # 模拟 v1 遗留
    state = State()
    ComposeRule(ws, StubProvider()).discover(state)[0].run(state)
    folded = state.targets["understanding.md"].folded
    assert stream_d in folded  # stream 计入折叠
    assert corpus_d not in folded  # corpus 不计入折叠


# ---- Increment 4:corpus 作只读参考层(persona 列出 + read_dirs + 不进 context) ----


def test_compose_corpus_as_reference_layer(tmp_path):
    """corpus:persona 注入基线 hint + 列出文件;read_dirs 含其目录;内容不作折叠块进 context。"""
    ws = Workspace.init(tmp_path)
    rs = _add_stream(ws, tmp_path, body="会议观测X")
    cp = tmp_path / "wp.md"
    cp.write_text("白皮书基线内容YYY")
    ws.add([cp], source_class="corpus")
    _make_digest(ws, rs, "观测纪要")
    prov = _CaptureProvider()
    state = State()
    ComposeRule(ws, prov).discover(state)[0].run(state)
    call = prov.calls[0]
    # persona 注入基线 hint(校正) + 列出 corpus 文件供 Read
    assert "校正" in call.persona
    assert "wp.md" in call.persona
    # read_dirs 含 corpus 文件所在目录(供 --add-dir 授读)
    assert str(tmp_path) in [str(p) for p in call.read_dirs]
    # corpus 原文不作折叠块塞进 context(只在参考层,agent 按需 Read)
    assert "白皮书基线内容YYY" not in call.context


def test_compose_labels_stream_observation_when_corpus_present(tmp_path):
    """有 corpus 参考层时,stream 折叠块标 ·观测(提示需对基线校准)。"""
    ws = Workspace.init(tmp_path)
    rs = _add_stream(ws, tmp_path)
    cp = tmp_path / "wp.md"
    cp.write_text("基线")
    ws.add([cp], source_class="corpus")
    _make_digest(ws, rs, "观测纪要")
    prov = _CaptureProvider()
    state = State()
    ComposeRule(ws, prov).discover(state)[0].run(state)
    assert "·观测" in prov.calls[0].context


def test_compose_no_corpus_keeps_today_behavior(tmp_path):
    """纯 stream(无 corpus 参考层):不注入参考段、不标 ·观测、read_dirs 空,与今天一致。"""
    ws = Workspace.init(tmp_path)
    rs = _add_stream(ws, tmp_path)
    _make_digest(ws, rs, "观测纪要")
    prov = _CaptureProvider()
    state = State()
    ComposeRule(ws, prov).discover(state)[0].run(state)
    call = prov.calls[0]
    assert "·观测" not in call.context
    assert "基线参考" not in call.persona
    assert list(call.read_dirs) == []


# ---- Increment 6:corpus 版本戳(advisory,不进 staleness 循环) ----


def test_fold_records_corpus_stamp(tmp_path):
    """折叠时记录 corpus 版本戳;刚折叠 → 无漂移。"""
    ws = Workspace.init(tmp_path)
    rs = _add_stream(ws, tmp_path)
    cp = tmp_path / "wp.md"
    cp.write_text("基线")
    ws.add([cp], source_class="corpus")
    _make_digest(ws, rs, "观测纪要")
    rule = ComposeRule(ws, StubProvider())
    state = State()
    rule.discover(state)[0].run(state)
    assert state.targets["understanding.md"].corpus_stamp  # 非空
    assert not rule.corpus_drifted("understanding.md", state)


def test_corpus_change_does_not_restale_but_is_advisory(tmp_path):
    """改 corpus 不触发自动重 fold(is_stale 不变),但版本戳漂移可检测(advisory)。"""
    ws = Workspace.init(tmp_path)
    rs = _add_stream(ws, tmp_path)
    cp = tmp_path / "wp.md"
    cp.write_text("基线v1")
    ws.add([cp], source_class="corpus")
    _make_digest(ws, rs, "观测纪要")
    rule = ComposeRule(ws, StubProvider())
    state = State()
    for it in rule.discover(state):
        it.run(state)  # 折叠两层
    cp.write_text("基线v2-改了关键内容")  # corpus 关键内容变更
    # 不触发自动重算:无未折叠 stream delta、上游未变
    assert rule.discover(state) == []
    # 但版本戳漂移可检测,供 advisory 提示手动 recompute
    assert rule.corpus_drifted("understanding.md", state)
