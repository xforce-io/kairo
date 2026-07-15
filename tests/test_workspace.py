from pathlib import Path

import yaml

from kairo.models import ProductState
from kairo.workspace import Workspace


def _write_constitution(ws, con):
    (ws.root / "constitution.yaml").write_text(
        yaml.safe_dump(con.model_dump(), allow_unicode=True, sort_keys=False)
    )


def test_init_creates_workspace_files(tmp_path):
    Workspace.init(tmp_path, topic="main")
    assert (tmp_path / "constitution.yaml").is_file()
    assert (tmp_path / ".kairo" / "state.json").is_file()


def test_init_default_constitution_has_digest_prompt_and_two_layer_targets(tmp_path):
    ws = Workspace.init(tmp_path)
    con = ws.constitution
    assert con.topic == "main"
    # #58:默认 digest 是高密度记忆纪要,不是一页纸周报
    assert "高密度" in con.pipeline.digest.prompt
    assert "宁详勿略" in con.pipeline.digest.prompt
    assert [t.path for t in con.targets] == ["understanding.md", "assessment.md"]
    assert con.targets[0].layer == "fact"
    assert con.targets[1].layer == "judgment"
    assert con.targets[1].depends_on == ["understanding.md"]  # 判断依赖事实


def test_init_default_asr_transform_uses_whisper_backend(tmp_path):
    """#26:默认 asr 转换声明 backend=whisper(真实意图),命令由本机配置解析。"""
    con = Workspace.init(tmp_path).constitution
    asr = next(t for t in con.transforms if t.name == "asr")
    assert asr.consumes == ["audio"] and asr.produces == "transcript"
    assert asr.backend == "whisper"


def test_constitution_reloads_from_disk(tmp_path):
    Workspace.init(tmp_path, topic="kidney")
    # 重新打开同一目录,读到的 topic 一致
    assert Workspace(tmp_path).constitution.topic == "kidney"


def test_guess_role_data_driven_default(tmp_path):
    ws = Workspace.init(tmp_path)
    assert ws.guess_role(Path("rec.m4a")) == "audio"  # 默认 audio 扩展名
    assert ws.guess_role(Path("note.txt")) == "transcript"  # 默认 fallback


def test_guess_role_honors_custom_ext_mapping(tmp_path):
    """加资源类型(#3):只在 constitution 声明扩展名→role,不改码。"""
    ws = Workspace.init(tmp_path)
    con = ws.constitution
    con.roles_by_ext[".md"] = "source_text"
    _write_constitution(ws, con)
    assert Workspace(ws.root).guess_role(Path("paper.md")) == "source_text"


def test_state_roundtrip(tmp_path):
    ws = Workspace.init(tmp_path)
    state = ws.read_state()
    assert state.products == {}
    assert state.targets == {}
    state.products["references/x/digest.md"] = ProductState(input_hash="abc")
    ws.write_state(state)
    again = Workspace(tmp_path).read_state()
    assert again.products["references/x/digest.md"].input_hash == "abc"


def test_set_title_persists_and_preserves_other_fields(tmp_path):
    # 重命名只改 title:id / source_class / forms 不变(title 是展示名,非身份)
    ws = Workspace.init(tmp_path / "ws", topic="t")
    src = tmp_path / "260629_110439.txt"
    src.write_text("内容")
    rid = ws.add([src])
    before = ws.read_manifest(rid)
    ws.set_title(rid, "数字员工架构对齐")
    man = ws.read_manifest(rid)
    assert man.title == "数字员工架构对齐"
    assert man.id == before.id
    assert man.source_class == before.source_class
    assert man.forms == before.forms


def test_set_title_strips_whitespace(tmp_path):
    ws = Workspace.init(tmp_path / "ws", topic="t")
    src = tmp_path / "a.txt"
    src.write_text("x")
    rid = ws.add([src])
    ws.set_title(rid, "  名字  ")
    assert ws.read_manifest(rid).title == "名字"


def test_set_title_rejects_empty(tmp_path):
    import pytest

    ws = Workspace.init(tmp_path / "ws", topic="t")
    src = tmp_path / "a.txt"
    src.write_text("x")
    rid = ws.add([src])
    with pytest.raises(ValueError):
        ws.set_title(rid, "   ")
