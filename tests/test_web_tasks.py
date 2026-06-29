# tests/test_web_tasks.py
import sys
import time

from fastapi.testclient import TestClient

from kairo.web.server import create_app
from kairo.web.tasks import StepTask, TaskRegistry, stream_events
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
    assert out[-1] == "event: done\ndata: 0\n\n"


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


def test_step_partial_reloads_layout_not_body(tmp_path, monkeypatch):
    # 回归:done 后必须原地替换 .layout(保留 header),不能 outerHTML 整个 <body>;
    # 也不能 sse-swap="done"(否则把退出码"0"灌进页面,整页变成一个"0")。
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = Workspace.init(tmp_path / "ws", topic="t")
    (tmp_path / "m.txt").write_text("会议内容")
    ws.add([tmp_path / "m.txt"])
    c = TestClient(create_app(tmp_path))
    r = c.post("/w/ws/step")
    assert r.status_code == 200
    assert 'sse-swap="done"' not in r.text
    assert 'hx-target="body"' not in r.text
    assert 'hx-target=".layout"' in r.text


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


def test_step_rejects_concurrent(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = Workspace.init(tmp_path / "ws", topic="t")
    (tmp_path / "m.txt").write_text("x")
    ws.add([tmp_path / "m.txt"])
    c = TestClient(create_app(tmp_path))
    # 直接占用该 slug 的串行锁(注入一个慢任务),再请求 step
    import sys
    app = c.app
    app.state.registry.start("ws", tmp_path / "ws", [sys.executable, "-c", "import time; time.sleep(2)"])
    r = c.post("/w/ws/step")
    assert r.status_code == 200
    assert "Running" in r.text


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
