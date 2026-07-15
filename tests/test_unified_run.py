"""#75: 主按钮状态机 + run 自动重试 blocked + 运行中添加 toast。"""

from __future__ import annotations

import os

from fastapi.testclient import TestClient

from kairo.engine import run_workspace, step, workspace_run_plan
from kairo.models import ProductState
from kairo.provider import StubProvider
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


def test_web_run_clean_disabled(tmp_path, monkeypatch):
    ws, _ = _ws_audio(tmp_path, monkeypatch)
    step(ws, StubProvider())
    r = TestClient(create_app(tmp_path)).get("/w/ws")
    assert "Up to date" in r.text or "已是最新" in r.text
    assert "disabled" in r.text


def test_web_run_summary_lists_blocks(tmp_path, monkeypatch):
    ws, rid = _ws_audio(tmp_path, monkeypatch)
    src_hash = ws.read_manifest(rid).forms[0].hash
    key = f"references/{rid}/transcript.md"
    st = ws.read_state()
    st.products[key] = ProductState(
        input_hash=src_hash, status="blocked", reason="asr-failed"
    )
    ws.write_state(st)
    r = TestClient(create_app(tmp_path)).get("/w/ws/run-summary")
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
