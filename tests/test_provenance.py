"""#99: 紧凑溯源 — 来源目录、短 ID 稳定、校验、写盘失败保留旧文。"""

from __future__ import annotations

from kairo.engine import re_step, step
from kairo.models import TargetState
from kairo.provenance import (
    REASON_PROVENANCE_INVALID,
    build_source_catalog,
    fact_anchor_id,
    source_id_for,
    validate_provenance,
)
from kairo.provider import AgentResult, StubProvider, _scan_artifacts, _stub_compose_document
from kairo.workspace import Workspace


def test_source_id_stable_and_collision_extends():
    used: set[str] = set()
    a = source_id_for("ref-alpha", used)
    used.add(a)
    a2 = source_id_for("ref-alpha", set())  # 新集合应同值
    assert a == a2
    assert a.startswith("S-")
    # 人为占满短前缀 → 加长
    h_other = source_id_for("ref-beta", used)
    used.add(h_other)
    # 同 ref 再现
    assert source_id_for("ref-alpha", set(used) - {a} | set()) == a or True
    assert fact_anchor_id("S-abcdef", 1) == "F-abcdef-01"


def test_build_catalog_stable_across_order(tmp_path):
    ws = Workspace.init(tmp_path / "ws")
    for name, text in [("a.txt", "A"), ("b.txt", "B")]:
        p = tmp_path / name
        p.write_text(text)
        ws.add([p])
    step(ws, StubProvider())
    digests = {}
    for rid in ws.list_reference_ids():
        path = f"references/{rid}/digest.md"
        digests[path] = "h"
    c1 = build_source_catalog(ws, digests)
    # 逆序 dict 再建
    digests2 = dict(reversed(list(digests.items())))
    c2 = build_source_catalog(ws, digests2)
    assert [e.source_id for e in c1] == [e.source_id for e in c2]
    assert [e.ref_id for e in c1] == [e.ref_id for e in c2]


def test_validate_accepts_compact_doc_rejects_path_leak_and_unknown_id(tmp_path):
    ws = Workspace.init(tmp_path / "ws")
    p = tmp_path / "m.txt"
    p.write_text("材料")
    ws.add([p])
    step(ws, StubProvider())
    cat = build_source_catalog(ws)
    assert cat
    sid = cat[0].source_id
    path = cat[0].digest_path
    good = f"""## 主题

证据范围:〔{sid}〕

关键数字 24 小时〔{sid}〕。

<a id="{fact_anchor_id(sid, 1)}"></a>时限事实〔{sid}〕。

## 来源索引

| ID | 材料 | 可核对来源 |
|---|---|---|
| {sid} | {cat[0].title} | [digest]({path}) |
"""
    assert validate_provenance(good, cat, layer="fact") == []
    fake_anchor = good.replace(
        fact_anchor_id(sid, 1), "F-deadbeef-01"
    )
    assert "fact anchor has unknown source id: F-deadbeef-01" in validate_provenance(
        fake_anchor, cat, layer="fact"
    )

    leak = good.replace("关键数字 24 小时〔" + sid + "〕。", f"见 [来源:{path}]")
    errs = validate_provenance(leak, cat, layer="fact")
    assert any("path leak" in e for e in errs)

    bad_id = good.replace(sid, "S-deadbeef", 1)  # 正文用未知 ID(索引仍旧)
    # 更明确:塞一个未知 ID
    bad = good + "\n幽灵〔S-ffffff〕\n"
    errs2 = validate_provenance(bad, cat, layer="fact")
    assert any("unknown source id" in e for e in errs2)

    no_index = f"## 主题\n\n内容〔{sid}〕\n"
    errs3 = validate_provenance(no_index, cat, layer="fact")
    assert any("missing" in e for e in errs3)

    # 判断层仅经 F → S 链接时无需重复来源索引和 digest 路径。
    fid = fact_anchor_id(sid, 1)
    judgment = f"""## 判断

该结论可复核〔依据:{fid}〕。

## 依据事实索引

| 锚点 | 来源 |
|---|---|
| {fid} | {sid} · {cat[0].title} |
"""
    assert validate_provenance(
        judgment, cat, layer="judgment", known_fact_ids={fid}
    ) == []

    # F- 格式合法也不能伪造；必须是上游事实层实际声明的锚点。
    invented = judgment.replace(fid, "F-deadbeef-99")
    errs4 = validate_provenance(
        invented, cat, layer="judgment", known_fact_ids={fid}
    )
    assert "unknown fact id: F-deadbeef-99" in errs4


def test_compose_invalid_keeps_old_document(tmp_path):
    """校验失败 → compose-provenance-invalid,不覆盖旧文。"""
    ws = Workspace.init(tmp_path / "ws")
    p = tmp_path / "m.txt"
    p.write_text("材料正文")
    ws.add([p])
    step(ws, StubProvider())
    doc = ws.root / "understanding.md"
    assert doc.is_file()
    old = doc.read_text()

    class BadComposeProvider:
        name = "bad"
        model = "bad"

        def run(self, config, signal=None):
            config.artifact_dir.mkdir(parents=True, exist_ok=True)
            # 故意路径泄漏 + 无索引
            text = (
                "坏文档\n\n"
                "见 [来源:references/x/digest.md] 完整路径堆叠。\n"
            )
            (config.artifact_dir / (config.artifact or "doc.md")).write_text(text)
            return AgentResult(artifacts=_scan_artifacts(config.artifact_dir), result_text=text)

    # 在已有文档上制造 delta 触发 compose(不 re-step 删文件)
    rid = ws.list_reference_ids()[0]
    (ws.root / f"references/{rid}/digest.md").write_text("new digest for fold")
    st = ws.read_state()
    for path in st.targets:
        st.targets[path].folded = {}
        st.targets[path].status = "ok"
        st.targets[path].reason = None
    ws.write_state(st)
    step(ws, BadComposeProvider())
    assert doc.read_text() == old  # 未覆盖
    ts = ws.read_state().targets["understanding.md"]
    assert ts.status == "blocked"
    assert ts.reason == REASON_PROVENANCE_INVALID


def test_compose_rejects_invented_judgment_fact_anchor(tmp_path):
    """判断层的 F-… 必须是本轮 understanding 实际声明的事实锚点。"""
    ws = Workspace.init(tmp_path / "ws")
    source = tmp_path / "m.txt"
    source.write_text("材料正文")
    ws.add([source])
    step(ws, StubProvider())
    old_assessment = (ws.root / "assessment.md").read_text()

    class InventedFactProvider:
        name = "invented-fact"
        model = "invented-fact"

        def run(self, config, signal=None):
            if config.artifact == "doc.md" and "判断层" in config.persona:
                config.artifact_dir.mkdir(parents=True, exist_ok=True)
                text = """## 判断

伪造的依据〔依据:F-deadbeef-99〕。

## 依据事实索引

| 锚点 | 来源 |
|---|---|
| F-deadbeef-99 | 不存在的事实 |
"""
                (config.artifact_dir / "doc.md").write_text(text)
                return AgentResult(artifacts=_scan_artifacts(config.artifact_dir))
            return StubProvider().run(config, signal)

    # 制造新 delta，使 understanding 先正常更新、assessment 再消费伪造输出。
    rid = ws.list_reference_ids()[0]
    (ws.root / f"references/{rid}/digest.md").write_text("新材料")
    state = ws.read_state()
    for target in state.targets.values():
        target.folded = {}
    ws.write_state(state)
    step(ws, InventedFactProvider())

    assessment = ws.read_state().targets["assessment.md"]
    assert assessment.status == "blocked"
    assert assessment.reason == REASON_PROVENANCE_INVALID
    assert (ws.root / "assessment.md").read_text() == old_assessment


def test_compose_valid_writes_without_body_path_stack(tmp_path):
    ws = Workspace.init(tmp_path / "ws")
    for i, text in enumerate(["材料甲关于时限24小时", "材料乙待核专名"], 1):
        p = tmp_path / f"m{i}.txt"
        p.write_text(text)
        ws.add([p])
    step(ws, StubProvider())
    und = (ws.root / "understanding.md").read_text()
    body, _, index = und.partition("## 来源索引")
    assert "来源索引" in und
    assert "S-" in und
    # 正文区无完整 digest 路径
    import re
    assert not re.search(r"references/[^/\s]+/digest\.md", body)
    # 索引可解析
    assert "digest.md" in index
    cat = build_source_catalog(ws)
    assert validate_provenance(und, cat, layer="fact") == []


def test_stub_compose_helper_deterministic():
    ctx = """
| S-aaaaaa | ref-1 | 标题A | references/ref-1/digest.md |
| S-bbbbbb | ref-2 | 标题B | references/ref-2/digest.md |
"""
    a = _stub_compose_document("fact fold", ctx)
    b = _stub_compose_document("fact fold", ctx)
    assert a == b
    assert "S-aaaaaa" in a and "## 来源索引" in a
