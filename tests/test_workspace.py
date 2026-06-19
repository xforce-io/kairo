from kairo.models import ProductState
from kairo.workspace import Workspace


def test_init_creates_workspace_files(tmp_path):
    Workspace.init(tmp_path, topic="main")
    assert (tmp_path / "constitution.yaml").is_file()
    assert (tmp_path / ".kairo" / "state.json").is_file()


def test_init_default_constitution_has_digest_prompt_and_two_layer_targets(tmp_path):
    ws = Workspace.init(tmp_path)
    con = ws.constitution
    assert con.topic == "main"
    assert con.pipeline.digest.prompt.strip()  # 非空默认 prompt
    assert [t.path for t in con.targets] == ["understanding.md", "assessment.md"]
    assert con.targets[0].layer == "fact"
    assert con.targets[1].layer == "judgment"
    assert con.targets[1].depends_on == ["understanding.md"]  # 判断依赖事实


def test_constitution_reloads_from_disk(tmp_path):
    Workspace.init(tmp_path, topic="kidney")
    # 重新打开同一目录,读到的 topic 一致
    assert Workspace(tmp_path).constitution.topic == "kidney"


def test_state_roundtrip(tmp_path):
    ws = Workspace.init(tmp_path)
    state = ws.read_state()
    assert state.products == {}
    assert state.targets == {}
    state.products["references/x/digest.md"] = ProductState(input_hash="abc")
    ws.write_state(state)
    again = Workspace(tmp_path).read_state()
    assert again.products["references/x/digest.md"].input_hash == "abc"
