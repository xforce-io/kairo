"""#75: 主按钮状态机 + run 自动重试 blocked + 运行中添加 toast。"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from kairo.engine import _run_plan_mode, run_workspace, step, workspace_run_plan
from kairo.models import ProductState, TargetState
from kairo.provider import StubProvider
from kairo.rules import REASON_COMPOSE_MIGRATION_REQUIRED, UNDERSTANDING_MAX_CHARS, _hash
from kairo.web.server import create_app
from kairo.workspace import Workspace


def _ws_audio(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    os.environ["KAIRO_STUB"] = "1"
    ws = Workspace.init(tmp_path / "ws")
    a = tmp_path / "a.m4a"
    a.write_bytes(b"x")
    rid = ws.add([a])
    return ws, rid


@pytest.mark.parametrize(
    ("pending", "retryable", "non_retryable", "expected"),
    [
        (0, 0, 0, "clean"),
        (1, 0, 1, "run"),
        (0, 1, 1, "retry"),
        (1, 1, 1, "run_and_retry"),
        (0, 0, 1, "attention"),
    ],
)
def test_run_plan_mode_truth_table(
    pending, retryable, non_retryable, expected
):
    assert _run_plan_mode(pending, retryable, non_retryable) == expected


def test_empty_workspace_plan_is_clean(tmp_path):
    """#134:空 workspace 主按钮状态机为 clean,不把 assessment 当待办。"""
    ws = Workspace.init(tmp_path / "ws", topic="未分类")
    plan = workspace_run_plan(ws)
    assert plan["mode"] == "clean"
    assert plan["pending_count"] == 0
    assert plan["blocked_count"] == 0


def test_plan_attention_for_non_retryable_target_block(tmp_path):
    ws = Workspace.init(tmp_path / "ws", topic="t")
    state = ws.read_state()
    state.targets["understanding.md"] = TargetState(
        status="blocked", reason=REASON_COMPOSE_MIGRATION_REQUIRED
    )
    ws.write_state(state)

    plan = workspace_run_plan(ws)

    assert plan["mode"] == "attention"
    assert plan["blocked_count"] == 1
    assert plan["retryable_blocked_count"] == 0
    assert plan["blocked_targets"][0]["retryable"] is False


def test_empty_workspace_step_does_not_invoke_provider(tmp_path):
    """#134:空 workspace step 无推进、不调 provider、不写 target。"""
    ws = Workspace.init(tmp_path / "ws", topic="t")

    class CountingProvider(StubProvider):
        def __init__(self):
            self.calls = 0

        def run(self, config, signal=None):
            self.calls += 1
            return super().run(config, signal)

    provider = CountingProvider()
    assert step(ws, provider) is False
    assert provider.calls == 0
    assert not (ws.root / "understanding.md").exists()
    assert not (ws.root / "assessment.md").exists()


def test_corpus_only_workspace_plan_is_clean(tmp_path):
    """#134:仅 corpus(不 fold)也不应让 assessment 因未记录上游而 stale。"""
    ws = Workspace.init(tmp_path / "ws", topic="t")
    base = tmp_path / "base.md"
    base.write_text("基线材料")
    ws.add([base], source_class="corpus")
    plan = workspace_run_plan(ws)
    assert plan["mode"] == "clean"
    assert plan["pending_count"] == 0
    assert step(ws, StubProvider()) is False
    assert not (ws.root / "assessment.md").exists()


def test_plan_clean_after_full_step(tmp_path, monkeypatch):
    ws, rid = _ws_audio(tmp_path, monkeypatch)
    step(ws, StubProvider())
    plan = workspace_run_plan(ws)
    assert plan["mode"] == "clean"
    assert plan["pending_count"] == 0
    assert plan["blocked_count"] == 0


def test_plan_retry_when_only_asr_failed(tmp_path, monkeypatch):
    ws, rid = _ws_audio(tmp_path, monkeypatch)
    src_hash = ws.read_manifest(rid).forms[0].hash
    key = f"references/{rid}/transcript.md"
    st = ws.read_state()
    st.products[key] = ProductState(
        input_hash=src_hash, status="blocked", reason="asr-failed"
    )
    ws.write_state(st)
    plan = workspace_run_plan(ws)
    assert plan["blocked_count"] == 1
    assert plan["mode"] in ("retry", "run_and_retry")  # 可能另有 target 待办
    # 普通 step 不推进 asr-failed 终态
    step(ws, StubProvider())
    assert ws.read_state().products[key].status == "blocked"
    # run 自动清 blocked 并推进
    assert run_workspace(ws, StubProvider()) is True
    assert (ws.root / key).is_file()
    assert ws.read_state().products[key].status != "blocked"


def test_web_run_button_retry_label(tmp_path, monkeypatch):
    ws, rid = _ws_audio(tmp_path, monkeypatch)
    src_hash = ws.read_manifest(rid).forms[0].hash
    key = f"references/{rid}/transcript.md"
    st = ws.read_state()
    st.products[key] = ProductState(
        input_hash=src_hash, status="blocked", reason="asr-failed"
    )
    ws.write_state(st)
    r = TestClient(create_app(tmp_path)).get("/w/ws")
    assert r.status_code == 200
    assert 'hx-post="/w/ws/run"' in r.text
    # retry 或 run_and_retry 文案
    assert (
        "Retry failures" in r.text
        or "重试失败" in r.text
        or "retry" in r.text.lower()
        or "重试" in r.text
    )


def test_web_attention_button_disabled_and_regen_warning(tmp_path):
    ws = Workspace.init(tmp_path / "ws", topic="t")
    old = ws.root / "understanding.md"
    old.write_text("旧正文")
    state = ws.read_state()
    state.targets["understanding.md"] = TargetState(
        output_hash="old",
        status="blocked",
        reason=REASON_COMPOSE_MIGRATION_REQUIRED,
    )
    ws.write_state(state)

    client = TestClient(create_app(tmp_path))
    page = client.get("/w/ws")
    target = client.get("/w/ws/target?path=understanding.md")

    assert "Needs re-step" in page.text or "需要 re-step" in page.text
    assert 'id="run-btn"' in page.text and "disabled" in page.text
    assert "Blocked: 1" in page.text or "阻塞: 1" in page.text
    assert "compress" in target.text or "压缩历史正文" in target.text
    assert "failures keep" in target.text or "失败保留旧版" in target.text


def test_web_leftover_oversized_degraded_uses_migration_copy(tmp_path):
    ws = Workspace.init(tmp_path / "ws", topic="t")
    old = "旧历史" * (UNDERSTANDING_MAX_CHARS // 3 + 1)
    understanding = ws.root / "understanding.md"
    understanding.write_text(old)
    state = ws.read_state()
    state.targets["understanding.md"] = TargetState(
        output_hash=_hash(old),
        status="blocked",
        reason="compose-degraded",
    )
    ws.write_state(state)

    client = TestClient(create_app(tmp_path))
    page = client.get("/w/ws")
    target = client.get("/w/ws/target?path=understanding.md")

    assert "Needs re-step" in page.text or "需要 re-step" in page.text
    assert 'id="run-btn"' in page.text and "disabled" in page.text
    assert REASON_COMPOSE_MIGRATION_REQUIRED in target.text
    assert "compose-degraded" not in target.text
    assert "compress" in target.text or "压缩历史正文" in target.text
    assert understanding.read_text() == old


def test_web_first_over_budget_target_has_recovery_button(tmp_path):
    ws = Workspace.init(tmp_path / "ws", topic="t")
    state = ws.read_state()
    state.targets["understanding.md"] = TargetState(
        status="blocked", reason="compose-over-budget"
    )
    ws.write_state(state)
    app = create_app(tmp_path)
    target = TestClient(app).get("/w/ws/target?path=understanding.md")

    assert 'hx-vals=\'{"target": "understanding.md"}\'' in target.text
    assert "missing target" in target.text or "未生成产物" in target.text
    assert app.state.registry.current("ws") is None


def test_web_run_clean_disabled(tmp_path, monkeypatch):
    ws, _ = _ws_audio(tmp_path, monkeypatch)
    step(ws, StubProvider())
    r = TestClient(create_app(tmp_path)).get("/w/ws")
    assert "Up to date" in r.text or "已是最新" in r.text
    assert "disabled" in r.text


def test_web_empty_workspace_run_disabled(tmp_path):
    """#134 S1:空 workspace 页主按钮 Up to date 且 disabled。"""
    Workspace.init(tmp_path / "ws", topic="t")
    r = TestClient(create_app(tmp_path)).get("/w/ws")
    assert r.status_code == 200
    assert "Up to date" in r.text or "已是最新" in r.text
    assert 'id="run-btn"' in r.text
    assert "disabled" in r.text


def test_web_empty_workspace_post_run_is_noop(tmp_path):
    """#134:空 workspace POST /run 不启 job。"""
    Workspace.init(tmp_path / "ws", topic="t")
    app = create_app(tmp_path)
    r = TestClient(app).post("/w/ws/run", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "Nothing to do" in r.text or "没有待办" in r.text
    assert app.state.registry.current("ws") is None


def test_web_run_summary_lists_blocks(tmp_path, monkeypatch):
    """#75/#97: 成功终态下 run-summary 才列 plan blocked;需绑定已结束 task。"""
    import sys
    import time

    ws, rid = _ws_audio(tmp_path, monkeypatch)
    src_hash = ws.read_manifest(rid).forms[0].hash
    key = f"references/{rid}/transcript.md"
    st = ws.read_state()
    st.products[key] = ProductState(
        input_hash=src_hash, status="blocked", reason="asr-failed"
    )
    ws.write_state(st)
    app = create_app(tmp_path)
    c = TestClient(app)
    task = app.state.registry.start(
        "ws", ws.root, [sys.executable, "-c", "print('ok')"]
    )
    end = time.time() + 5
    while not task.done and time.time() < end:
        time.sleep(0.02)
    assert task.done
    r = c.get(f"/w/ws/run-summary?task_id={task.task_id}")
    assert r.status_code == 200
    assert "asr-failed" in r.text
    assert rid in r.text


def test_web_add_while_running_sets_toast_header(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    os.environ["KAIRO_STUB"] = "1"
    Workspace.init(tmp_path / "ws")
    app = create_app(tmp_path)
    c = TestClient(app)
    # 占住 running 锁
    from pathlib import Path

    task = app.state.registry.start(
        "ws",
        Path(tmp_path / "ws"),
        [os.environ.get("SHELL", "/bin/bash"), "-c", "sleep 30"],
    )
    try:
        src = tmp_path / "n.txt"
        src.write_text("x")
        r = c.post("/w/ws/ref", data={"path": str(src)})
        assert r.status_code == 200
        assert "kairoToast" in r.headers.get("HX-Trigger", "")
    finally:
        app.state.registry.cancel(task.task_id)
