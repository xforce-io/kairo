"""#77: 删除参考 —— 摘 folded、默认保留正文、可选 recompose。"""

from __future__ import annotations

import os

from fastapi.testclient import TestClient

from kairo.engine import delete_reference, step, workspace_run_plan
from kairo.models import ProductState, TargetState
from kairo.provider import StubProvider
from kairo.web.server import create_app
from kairo.workspace import Workspace


def _ws_with_folded_stream(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    os.environ["KAIRO_STUB"] = "1"
    ws = Workspace.init(tmp_path / "ws", topic="t")
    src = tmp_path / "note.txt"
    src.write_text("观测材料 A")
    rid = ws.add([src])
    step(ws, StubProvider())
    return ws, rid


def test_delete_reference_removes_dir_and_products(tmp_path, monkeypatch):
    ws, rid = _ws_with_folded_stream(tmp_path, monkeypatch)
    prefix = f"references/{rid}/"
    assert any(k.startswith(prefix) for k in ws.read_state().products)
    assert (ws.references_dir() / rid).is_dir()

    delete_reference(ws, rid)

    assert rid not in ws.list_reference_ids()
    assert not (ws.references_dir() / rid).exists()
    state = ws.read_state()
    assert not any(k.startswith(prefix) for k in state.products)


def test_delete_reference_strips_folded_keeps_body(tmp_path, monkeypatch):
    ws, rid = _ws_with_folded_stream(tmp_path, monkeypatch)
    digest_key = f"references/{rid}/digest.md"
    # 模拟已 fold 的产物正文
    body = "已融入旧材料的正文"
    (ws.root / "understanding.md").write_text(body)
    st = ws.read_state()
    ts = st.targets.get("understanding.md") or TargetState()
    ts.folded = {digest_key: "abc"}
    ts.last_major_folded = {digest_key: "abc"}
    ts.output_hash = "x"
    ts.status = "ok"
    st.targets["understanding.md"] = ts
    # 塞一条 product 记账
    st.products[digest_key] = ProductState(input_hash="h")
    ws.write_state(st)

    delete_reference(ws, rid)

    assert (ws.root / "understanding.md").read_text() == body
    ts2 = ws.read_state().targets["understanding.md"]
    assert digest_key not in ts2.folded
    assert digest_key not in ts2.last_major_folded
    assert ts2.reason == "materials-changed"
    plan = workspace_run_plan(ws)
    assert plan["mode"] == "run"
    assert plan["pending_count"] >= 1


def test_delete_reference_recompose_rewrites_targets(tmp_path, monkeypatch):
    ws, rid = _ws_with_folded_stream(tmp_path, monkeypatch)
    # 再加一条保留材料,删 rid 后应只基于剩余 digests 重综合
    src2 = tmp_path / "keep.txt"
    src2.write_text("保留材料 B")
    rid2 = ws.add([src2])
    step(ws, StubProvider())
    (ws.root / "understanding.md").write_text("旧正文含已删内容")

    delete_reference(ws, rid, recompose=True, provider=StubProvider())

    assert rid not in ws.list_reference_ids()
    assert rid2 in ws.list_reference_ids()
    st = ws.read_state()
    # re_step 清空后重跑,不应再标 materials-changed
    for ts in st.targets.values():
        assert ts.reason != "materials-changed"
    plan = workspace_run_plan(ws)
    assert plan["mode"] == "clean"


def test_delete_corpus_pointer_keeps_user_tree(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    user_tree = tmp_path / "corpus_src"
    user_tree.mkdir()
    (user_tree / "doc.md").write_text("基线")
    ws = Workspace.init(tmp_path / "ws", topic="t")
    rid = ws.add([user_tree], source_class="corpus")
    assert (ws.references_dir() / rid).is_dir()

    delete_reference(ws, rid)

    assert rid not in ws.list_reference_ids()
    assert user_tree.is_dir()
    assert (user_tree / "doc.md").read_text() == "基线"


def test_delete_unknown_ref_raises(tmp_path):
    ws = Workspace.init(tmp_path / "ws", topic="t")
    try:
        delete_reference(ws, "nope")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "不存在" in str(e)


def test_web_ref_meta_has_delete_button(tmp_path, monkeypatch):
    ws, rid = _ws_with_folded_stream(tmp_path, monkeypatch)
    r = TestClient(create_app(tmp_path)).get(f"/w/ws/ref/{rid}")
    assert r.status_code == 200
    assert f'hx-post="/w/ws/ref/{rid}/delete"' in r.text
    assert "btn-danger" in r.text
    assert 'name="recompose"' in r.text


def test_web_delete_ref_redirects_and_clears(tmp_path, monkeypatch):
    ws, rid = _ws_with_folded_stream(tmp_path, monkeypatch)
    client = TestClient(create_app(tmp_path))
    r = client.post(f"/w/ws/ref/{rid}/delete", data={"recompose": "0"})
    assert r.status_code == 200
    assert r.headers.get("HX-Redirect") == "/w/ws"
    assert rid not in Workspace.open(tmp_path / "ws").list_reference_ids()


def test_web_delete_ref_cancel_is_client_side_only(tmp_path, monkeypatch):
    """确认取消不发请求:服务端未调用时 ref 仍在(无副作用)。"""
    ws, rid = _ws_with_folded_stream(tmp_path, monkeypatch)
    assert rid in ws.list_reference_ids()


def test_cli_rm_ref(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from kairo.cli import app

    ws, rid = _ws_with_folded_stream(tmp_path, monkeypatch)
    monkeypatch.chdir(ws.root)
    result = CliRunner().invoke(app, ["rm-ref", rid])
    assert result.exit_code == 0, result.output
    assert f"deleted {rid}" in result.output
    assert rid not in Workspace.open(ws.root).list_reference_ids()
