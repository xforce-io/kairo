"""#372: knowledge drift points only at products that are really affected, and can be recomputed from the Topic page."""

import re

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from kairo.cli import app
from kairo.engine import step
from kairo.knowledge import load_workspace, new_entry, save_workspace
from kairo.knowledge_drift import drift_rows, recompute_drifted
from kairo.provider import StubProvider
from kairo.web.server import create_app
from kairo.workspace import Workspace

runner = CliRunner()


def _topic_with_digests(tmp_path, texts: dict[str, str]) -> tuple[Workspace, object]:
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws", topic="ws")
    for name, text in texts.items():
        m = tmp_path / f"{name}.txt"
        m.write_text(text)
        ws.add([m], ref_id=name)
    step(ws, StubProvider())
    return ws, root


def _confirm(ws: Workspace, title: str, **kw) -> None:
    doc, _ = load_workspace(ws.root)
    doc.entries.append(new_entry(title=title, scope="workspace", **kw))
    save_workspace(ws.root, doc)


def test_new_entry_only_drifts_products_that_mention_it(tmp_path):
    """S1: three digests, one mentions the new name → exactly one drift row, with the reason."""
    ws, root = _topic_with_digests(
        tmp_path,
        {"a": "高希彬提出了方案。", "b": "预算讨论,无人名。", "c": "排期讨论,无人名。"},
    )
    assert drift_rows(ws, root) == []  # S3: nothing changed → nothing reported
    _confirm(ws, "高希彬")
    rows = drift_rows(ws, root)
    products = [row for row in rows if row.kind == "digest"]
    assert [row.target for row in products] == ["a"]
    assert products[0].new == ("高希彬",) and products[0].changed == ()
    # understanding.md folds digest a, so it mentions the name too and drifts with it; never b/c.
    assert {row.target for row in rows} <= {"a", "understanding.md"}


def test_changed_matched_entry_drifts_only_its_consumers(tmp_path):
    """S1: editing an entry that a product consumed marks that product; unrelated products stay clean."""
    ws, root = _topic_with_digests(tmp_path, {"a": "康医通要上线。", "b": "没有系统名。"})
    _confirm(ws, "康医通", description="医院端产品")
    step(ws, StubProvider())  # nothing pending: state unchanged, drift still shows 'new'
    from kairo.engine import re_step

    re_step(ws, StubProvider(), "a")  # product a now records matched 康医通 + its hash
    assert [row.target for row in drift_rows(ws, root) if row.kind == "digest"] == []
    doc, _ = load_workspace(ws.root)
    doc.entries[0].description = "院内全流程产品(改写)"
    save_workspace(ws.root, doc)
    rows = [row for row in drift_rows(ws, root) if row.kind == "digest"]
    assert [row.target for row in rows] == ["a"]
    assert rows[0].changed == ("康医通",) and rows[0].new == ()


def test_legacy_products_without_hashes_still_detect_new_and_vanished_entries(tmp_path):
    ws, root = _topic_with_digests(tmp_path, {"a": "蒋贻鑫负责数据。"})
    _confirm(ws, "蒋贻鑫")
    state = ws.read_state()
    product = state.products["references/a/digest.md"]
    product.knowledge_diagnostic = None  # produced before #182/#372
    ws.write_state(state)
    rows = [row for row in drift_rows(ws, root) if row.kind == "digest"]
    assert rows and rows[0].new == ("蒋贻鑫",)


def test_topic_page_banner_and_recompute_only_touch_drifted_products(tmp_path, monkeypatch):
    """S2 (E2E): banner shows N, the button re-steps exactly those products via the task runner."""
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws, root = _topic_with_digests(tmp_path, {"a": "胡值彬强调能源优先。", "b": "无关材料。"})
    before_b = ws.read_state().products["references/b/digest.md"].knowledge_generation
    client = TestClient(create_app(root))
    page = client.get("/w/ws", headers={"accept-language": "zh"})
    assert "个产物基于旧知识" not in page.text  # S3: no banner without drift

    _confirm(ws, "胡值彬")
    page = client.get("/w/ws", headers={"accept-language": "zh"})
    n = len(drift_rows(ws, root))
    assert n >= 1
    assert f"{n} 个产物基于旧知识" in page.text and "重算受影响" in page.text
    assert 'hx-post="/w/ws/knowledge/recompute-drift"' in page.text

    response = client.post("/w/ws/knowledge/recompute-drift", headers={"HX-Request": "true"})
    assert response.status_code == 200
    task_id = re.search(r"/w/ws/step/([0-9a-f]+)/stream", response.text)
    assert task_id
    stream = client.get(f"/w/ws/step/{task_id.group(1)}/stream").text
    assert "event: done" in stream

    assert drift_rows(ws, root) == []
    state = ws.read_state()
    assert state.products["references/a/digest.md"].knowledge_diagnostic.matched_entry_ids
    assert state.products["references/b/digest.md"].knowledge_generation == before_b  # untouched
    page = client.get("/w/ws", headers={"accept-language": "zh"})
    assert "个产物基于旧知识" not in page.text


def test_live_target_keeps_union_of_matched_entries_across_incremental_composes(tmp_path):
    """understanding.md accumulates names; an incremental compose for one digest must not forget the others."""
    ws, root = _topic_with_digests(tmp_path, {"a": "胡值彬强调能源优先。", "b": "预算讨论,无人名。"})
    _confirm(ws, "胡值彬")
    recompute_drifted(ws, StubProvider(), root)
    assert drift_rows(ws, root) == []
    _confirm(ws, "预算讨论")
    recompute_drifted(ws, StubProvider(), root)  # re-steps only b → incremental compose with Δ = b
    live = ws.read_state().targets["understanding.md"].knowledge_diagnostic
    assert len(live.matched_entry_ids) == 2 and len(live.matched_entry_hashes) == 2
    assert drift_rows(ws, root) == []
    from kairo.engine import re_step

    re_step(ws, StubProvider(), "understanding.md")  # full recompose resets to what it actually matched
    live = ws.read_state().targets["understanding.md"].knowledge_diagnostic
    assert len(live.matched_entry_ids) == 2
    assert drift_rows(ws, root) == []


def test_recompute_drifted_returns_targets_and_cli_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws, root = _topic_with_digests(tmp_path, {"a": "许鹏主持。", "b": "无关。"})
    _confirm(ws, "许鹏")
    done = recompute_drifted(ws, StubProvider(), root)
    assert done[0] == "a" and "b" not in done
    assert drift_rows(ws, root) == []

    _confirm(ws, "无关")
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(root))
    result = runner.invoke(app, ["re-step", "--knowledge-drift", "--topic", "ws"])
    assert result.exit_code == 0, result.output
    assert "re-stepped" in result.output and "b" in result.output
    assert runner.invoke(app, ["re-step", "a", "--knowledge-drift", "--topic", "ws"]).exit_code == 2
