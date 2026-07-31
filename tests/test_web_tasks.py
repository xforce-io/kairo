# tests/test_web_tasks.py
import sys
import time

from fastapi.testclient import TestClient

from kairo.web.server import create_app
from kairo.web.tasks import (
    StepTask,
    TaskRegistry,
    classify_task,
    redact_sensitive,
    safe_error_summary,
    stream_events,
)
from kairo.workspace import Workspace


def _wait(task, timeout=10):
    end = time.time() + timeout
    while not task.done and time.time() < end:
        time.sleep(0.02)
    assert task.done, "task did not finish"


def test_start_captures_lines_and_exit(tmp_path):
    reg = TaskRegistry()
    argv = [sys.executable, "-c", "print('a'); print('b')"]
    t = reg.start("ws", tmp_path, argv)
    _wait(t)
    assert t.lines == ["a", "b"]
    assert t.exit_code == 0


def test_serial_lock_rejects_second(tmp_path):
    reg = TaskRegistry()
    slow = [sys.executable, "-c", "import time; time.sleep(2)"]
    reg.start("ws", tmp_path, slow)
    try:
        reg.start("ws", tmp_path, slow)
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


def test_different_slugs_run_concurrently(tmp_path):
    reg = TaskRegistry()
    a = reg.start("a", tmp_path, [sys.executable, "-c", "print('x')"])
    b = reg.start("b", tmp_path, [sys.executable, "-c", "print('y')"])
    _wait(a); _wait(b)
    assert a.lines == ["x"] and b.lines == ["y"]


def test_stream_events_replays_then_done():
    t = StepTask(task_id="t1", slug="ws")
    t.lines = ["line1", "line2"]
    t.done = True
    t.exit_code = 0
    out = list(stream_events(t))
    assert "data: line1\n\n" in out
    assert "data: line2\n\n" in out
    assert out[-1].startswith("event: done\ndata: ")
    assert '"kind": "succeeded"' in out[-1] or '"kind":"succeeded"' in out[-1]


def test_classify_task_exit_code_and_cancel():
    # 成功
    ok = StepTask(task_id="a", slug="ws", done=True, exit_code=0)
    assert classify_task(ok).kind == "succeeded"
    # 失败 + 安全摘要
    bad = StepTask(
        task_id="b",
        slug="ws",
        done=True,
        exit_code=1,
        lines=["info", "Error: Grok request failed status 401"],
    )
    r = classify_task(bad)
    assert r.kind == "failed"
    assert r.exit_code == 1
    assert "401" in r.message or "failed" in r.message.lower()
    # 取消优先于非零退出
    can = StepTask(task_id="c", slug="ws", done=True, exit_code=-15, cancel_requested=True)
    assert classify_task(can).kind == "cancelled"
    # 缺失 / 运行中
    assert classify_task(None).kind == "missing"
    running = StepTask(task_id="d", slug="ws", done=False)
    assert classify_task(running).kind == "running"


def test_safe_error_summary_redacts_and_truncates():
    lines = [
        "Authorization: Bearer super-secret-token-value",
        "api_key=sk-abcdefghijklmnopqrstuvwxyz012345",
        "Error: provider timeout after 30s",
    ]
    s = safe_error_summary(lines)
    assert "super-secret" not in s
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in s or "[redacted]" in s
    assert "timeout" in s.lower() or "Error" in s
    # 截断
    long = ["E: " + ("x" * 500)]
    short = safe_error_summary(long, max_len=40)
    assert len(short) <= 40
    assert short.endswith("…")
    # 直接脱敏
    assert "[redacted]" in redact_sensitive("Bearer abcdefghijklmnop")


def test_step_endpoint_runs_and_streams(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = Workspace.init(tmp_path / "ws", topic="t")
    (tmp_path / "m.txt").write_text("会议内容")
    ws.add([tmp_path / "m.txt"])
    c = TestClient(create_app(tmp_path))
    r = c.post("/w/ws/step")
    assert r.status_code == 200
    # 片段含 SSE 容器 + task_id 指向 stream 端点
    assert "/stream" in r.text
    # 拉一次 SSE,应能读到 done 事件
    import re
    m = re.search(r"/w/ws/step/([0-9a-f]+)/stream", r.text)
    assert m
    tid = m.group(1)
    body = c.get(f"/w/ws/step/{tid}/stream").text
    assert "event: done" in body
    # 收敛后产物生成
    assert (tmp_path / "ws" / "understanding.md").is_file()


def test_step_done_loads_run_summary_not_full_body(tmp_path, monkeypatch):
    # #75/#97:done 后进 run-summary(带 task_id),不能灌 body,也不能 sse-swap="done"。
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = Workspace.init(tmp_path / "ws", topic="t")
    (tmp_path / "m.txt").write_text("会议内容")
    ws.add([tmp_path / "m.txt"])
    c = TestClient(create_app(tmp_path))
    r = c.post("/w/ws/step")
    assert r.status_code == 200
    assert 'sse-swap="done"' not in r.text
    assert 'hx-target="body"' not in r.text
    assert "/run-summary" in r.text
    assert "task_id=" in r.text
    assert 'hx-target="#step-area"' in r.text


def test_run_summary_failed_nonzero_exit(tmp_path, monkeypatch):
    """#97 S1: 子进程非零退出 → 失败摘要,绝无成功/无剩余阻塞措辞;按钮可再点。"""
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = Workspace.init(tmp_path / "ws", topic="t")
    app = create_app(tmp_path)
    c = TestClient(app)
    # 可控非零退出(不写 blocked state)——驱动真实 TaskRegistry + run-summary
    fail_argv = [
        sys.executable,
        "-c",
        "import sys; print('Error: Grok request failed status 502'); sys.exit(1)",
    ]
    task = app.state.registry.start("ws", ws.root, fail_argv)
    _wait(task)
    assert task.exit_code == 1
    r = c.get(f"/w/ws/run-summary?task_id={task.task_id}")
    assert r.status_code == 200
    body = r.text
    assert "Run failed" in body or "运行失败" in body
    assert "502" in body or "Grok" in body or "failed" in body.lower()
    # 禁止成功结论
    assert "No remaining blocks" not in body
    assert "无剩余阻塞" not in body
    assert "run.done_ok" not in body
    # 运行锁释放 + OOB 刷新按钮(可再次 Run)
    assert app.state.registry.is_running("ws") is False
    assert 'id="run-btn-wrap"' in body
    assert "hx-post=" in body and "/run" in body


def test_run_summary_cancelled(tmp_path, monkeypatch):
    """#97: 用户取消 → cancelled 终态,非失败伪装。"""
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = Workspace.init(tmp_path / "ws", topic="t")
    app = create_app(tmp_path)
    c = TestClient(app)
    slow = [sys.executable, "-c", "import time; time.sleep(30)"]
    task = app.state.registry.start("ws", ws.root, slow)
    assert app.state.registry.cancel(task.task_id) is True
    _wait(task, timeout=5)
    r = c.get(f"/w/ws/run-summary?task_id={task.task_id}")
    assert r.status_code == 200
    assert "cancelled" in r.text.lower() or "已取消" in r.text
    assert "No remaining blocks" not in r.text
    assert "无剩余阻塞" not in r.text


def test_run_summary_succeeded_shows_plan(tmp_path, monkeypatch):
    """#97: 退出 0 才走 plan;有 blocked 显示数量而非假成功抹平失败。"""
    monkeypatch.setenv("KAIRO_STUB", "1")
    from kairo.models import ProductState

    ws = Workspace.init(tmp_path / "ws", topic="t")
    a = tmp_path / "a.m4a"
    a.write_bytes(b"x")
    rid = ws.add([a])
    src_hash = ws.read_manifest(rid).forms[0].hash
    key = f"references/{rid}/transcript.md"
    st = ws.read_state()
    st.products[key] = ProductState(
        input_hash=src_hash, status="blocked", reason="asr-failed"
    )
    ws.write_state(st)
    app = create_app(tmp_path)
    c = TestClient(app)
    ok_argv = [sys.executable, "-c", "print('ok')"]
    task = app.state.registry.start("ws", ws.root, ok_argv)
    _wait(task)
    r = c.get(f"/w/ws/run-summary?task_id={task.task_id}")
    assert r.status_code == 200
    assert "asr-failed" in r.text
    assert rid in r.text
    # 成功标题可以有,但不得出现失败标题冒充
    assert "Run failed" not in r.text and "运行失败" not in r.text


def test_run_summary_missing_task_not_success(tmp_path, monkeypatch):
    """#97: 无 task_id / 任务不存在 → 不可用提示,非成功。"""
    monkeypatch.setenv("KAIRO_STUB", "1")
    Workspace.init(tmp_path / "ws", topic="t")
    c = TestClient(create_app(tmp_path))
    r = c.get("/w/ws/run-summary")
    assert r.status_code == 200
    assert "No remaining blocks" not in r.text
    assert "无剩余阻塞" not in r.text
    assert "unavailable" in r.text.lower() or "不可用" in r.text
    r2 = c.get("/w/ws/run-summary?task_id=deadbeef0000")
    assert r2.status_code == 200
    assert "No remaining blocks" not in r2.text


def test_cancel_kills_running_task(tmp_path):
    reg = TaskRegistry()
    slow = [sys.executable, "-c", "import time; time.sleep(30)"]
    task = reg.start("ws", tmp_path, slow)
    assert reg.cancel(task.task_id) is True
    # _pump 线程在 EOF 后设 done=True,最多等 5s
    end = time.time() + 5
    while not task.done and time.time() < end:
        time.sleep(0.1)
    assert task.done, "task did not become done after cancel"


def test_registry_current_returns_running_only(tmp_path):
    """#114:current(slug) 仅返回未完成任务。"""
    reg = TaskRegistry()
    assert reg.current("ws") is None
    slow = [sys.executable, "-c", "import time; time.sleep(2)"]
    task = reg.start("ws", tmp_path, slow)
    assert reg.current("ws") is task
    reg.cancel(task.task_id)
    _wait(task, timeout=5)
    assert reg.current("ws") is None


def test_step_attaches_running_task_not_busy_text(tmp_path, monkeypatch):
    """#114 S2:运行中再 POST /step|/run → 附着同一 task_id,非 busy 纯文案。"""
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = Workspace.init(tmp_path / "ws", topic="t")
    (tmp_path / "m.txt").write_text("x")
    ws.add([tmp_path / "m.txt"])
    app = create_app(tmp_path)
    c = TestClient(app)
    slow = [sys.executable, "-c", "import time; time.sleep(3)"]
    task = app.state.registry.start("ws", ws.root, slow)
    r = c.post("/w/ws/step")
    assert r.status_code == 200
    body = r.text
    # 附着运行视图,不是冷 busy 句
    assert f"/step/{task.task_id}/stream" in body
    assert "wait for the current job" not in body
    assert "请等待当前任务结束" not in body
    # OOB 主按钮 running disabled
    assert 'id="run-btn-wrap"' in body
    assert "hx-swap-oob" in body
    assert "disabled" in body
    assert "Running" in body or "运行中" in body
    # 仍是单 job
    assert app.state.registry.current("ws") is task
    app.state.registry.cancel(task.task_id)
    _wait(task, timeout=5)


def test_run_start_oob_disables_button(tmp_path, monkeypatch):
    """#114 S1:POST /run 成功响应含 step 流 + OOB disabled Running…。"""
    import re

    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = Workspace.init(tmp_path / "ws", topic="t")
    (tmp_path / "m.txt").write_text("会议内容")
    ws.add([tmp_path / "m.txt"])
    app = create_app(tmp_path)
    c = TestClient(app)
    r = c.post("/w/ws/run")
    assert r.status_code == 200
    body = r.text
    m = re.search(r"/w/ws/step/([0-9a-f]+)/stream", body)
    assert m, body
    tid = m.group(1)
    assert app.state.registry.current("ws") is not None
    assert app.state.registry.current("ws").task_id == tid
    assert 'id="run-btn-wrap"' in body and "hx-swap-oob" in body
    assert "disabled" in body
    assert "Running" in body or "运行中" in body
    # 耗尽 SSE 以免挂起
    c.get(f"/w/ws/step/{tid}/stream")


def test_workspace_page_prefills_running_step(tmp_path, monkeypatch):
    """#114:整页加载时若 job 在跑,step-area 预填可取消的运行视图。"""
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = Workspace.init(tmp_path / "ws", topic="t")
    app = create_app(tmp_path)
    c = TestClient(app)
    slow = [sys.executable, "-c", "import time; time.sleep(3)"]
    task = app.state.registry.start("ws", ws.root, slow)
    r = c.get("/w/ws")
    assert r.status_code == 200
    assert f"/step/{task.task_id}/stream" in r.text
    assert 'id="run-btn"' in r.text and "disabled" in r.text
    app.state.registry.cancel(task.task_id)
    _wait(task, timeout=5)




def test_step_with_target_triggers_restep(tmp_path, monkeypatch):
    # POST /step 带 target → re-step:整篇重综合该产物(区别于普通 step 对手改的 blocked)
    monkeypatch.setenv("KAIRO_STUB", "1")
    from kairo.engine import step
    from kairo.provider import select_provider
    ws = Workspace.init(tmp_path / "ws", topic="t")
    (tmp_path / "m.txt").write_text("会议内容")
    ws.add([tmp_path / "m.txt"])
    step(ws, select_provider())  # 先产出 understanding.md
    (tmp_path / "ws" / "understanding.md").write_text("STALE-手改")
    c = TestClient(create_app(tmp_path))
    r = c.post("/w/ws/step", data={"target": "understanding.md"})
    assert r.status_code == 200
    import re
    m = re.search(r"/w/ws/step/([0-9a-f]+)/stream", r.text)
    assert m
    c.get(f"/w/ws/step/{m.group(1)}/stream")  # 阻塞到 done
    # re-step 删旧产物 + 重综合 → 覆盖手改内容
    assert (tmp_path / "ws" / "understanding.md").read_text() != "STALE-手改"
