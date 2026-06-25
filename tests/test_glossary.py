"""glossary 真名册(#20):领域专名跨阶段 grounding。

主机制 = 把真名册作权威参考注入 Digest/Compose 的 persona;
空表零行为变化(向后兼容);aka 仅作参考变体,不做字符串纠错。
"""

import yaml

from kairo.models import Constitution, GlossaryEntry
from kairo.provider import StubProvider
from kairo.workspace import Workspace
from kairo.engine import step


# ---- 单元:数据模型 + 渲染 ----


def test_glossary_defaults_empty():
    assert Constitution().glossary == []


def test_glossary_reference_empty_is_blank():
    # 空 glossary → 不注入任何东西(零行为变化)
    assert Constitution().glossary_reference() == ""


def test_glossary_parsed_from_yaml():
    con = Constitution(
        **yaml.safe_load(
            """
            topic: t
            glossary:
              - name: 灵犀系统
                note: 平台正式名,天溯出品
                aka: [灵西, 灵息]
              - name: 李华
                note: 协和营养科主任
            """
        )
    )
    assert [e.name for e in con.glossary] == ["灵犀系统", "李华"]
    assert con.glossary[0].aka == ["灵西", "灵息"]
    assert con.glossary[1].aka == []  # aka 可选


def test_glossary_reference_contains_name_note_aka():
    con = Constitution(
        glossary=[
            GlossaryEntry(name="灵犀系统", note="平台正式名", aka=["灵西", "灵息"]),
            GlossaryEntry(name="李华", note="协和营养科主任"),
        ]
    )
    block = con.glossary_reference()
    # 真名是主角:都要出现
    assert "灵犀系统" in block and "李华" in block
    # note 给模型 grounding
    assert "平台正式名" in block
    # aka 作参考变体出现
    assert "灵西" in block and "灵息" in block
    # 有一句指令让产出用规范名(grounding,不是字符串纠错)
    assert "规范名" in block


# ---- 集成:真名册注入到 Digest/Compose(StubProvider 把 persona echo 进产物) ----


def _ws_with_glossary(tmp_path) -> Workspace:
    Workspace.init(tmp_path)
    con = (tmp_path / "constitution.yaml").read_text()
    con += (
        "glossary:\n"
        "  - name: 灵犀系统\n"
        "    note: 平台正式名\n"
        "    aka: [灵西]\n"
    )
    (tmp_path / "constitution.yaml").write_text(con)
    return Workspace(tmp_path)


def test_glossary_injected_into_digest_and_compose(tmp_path):
    ws = _ws_with_glossary(tmp_path)
    t = tmp_path / "meeting.txt"
    t.write_text("今天讨论灵西的营养模块")
    ws.add([t])
    step(ws, StubProvider())
    rid = ws.list_reference_ids()[0]
    digest = (ws.root / f"references/{rid}/digest.md").read_text()
    understanding = (ws.root / "understanding.md").read_text()
    # 真名册随 persona 进了 digest 与 compose
    assert "灵犀系统" in digest and "规范名" in digest
    assert "灵犀系统" in understanding


def test_no_glossary_keeps_behavior(tmp_path):
    # 无 glossary:产物里不应出现真名册的标记
    ws = Workspace.init(tmp_path)
    t = tmp_path / "m.txt"
    t.write_text("内容")
    ws.add([t])
    step(ws, StubProvider())
    rid = ws.list_reference_ids()[0]
    digest = (ws.root / f"references/{rid}/digest.md").read_text()
    assert "真名册" not in digest
