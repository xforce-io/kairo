# tests/test_web_tasks.py
import sys
import time

from fastapi.testclient import TestClient

from kairo.web.server import create_app
from kairo.web.i18n import translator
from kairo.web.tasks import (
    StepTask,
    TaskRegistry,
    classify_task,
    is_transport_line,
    phrase_for_work_key,
    redact_sensitive,
    render_progress_html,
    resolve_progress_phrase,
    safe_error_summary,
    stream_events,
    wrap_log_line,
)
from kairo.models import KnowledgeDiagnostic, ProductState, TargetState
from kairo.rules import (
    REASON_COMPOSE_MIGRATION_REQUIRED,
    UNDERSTANDING_MAX_CHARS,
    _hash,
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
    _wait(a)
    _wait(b)
    assert a.lines == ["x"] and b.lines == ["y"]


def test_stream_events_replays_then_done():
    t = StepTask(task_id="t1", slug="ws")
    t.lines = ["line1", "line2"]
    t.done = True
    t.exit_code = 0
    out = list(stream_events(t))
    assert out[0].startswith("event: progress\ndata: ")
    assert "log-line" in out[1] and "line1" in out[1]
    assert "log-line" in out[2] and "line2" in out[2]
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


def _failing_run_task(app, ws):
    fail_argv = [
        sys.executable,
        "-c",
        "import sys; print('Error: Grok request failed status 502'); sys.exit(1)",
    ]
    task = app.state.registry.start("ws", ws.root, fail_argv)
    _wait(task)
    assert task.exit_code == 1
    return task


def test_run_summary_failed_nonzero_exit(tmp_path, monkeypatch):
    """#97 S1: 子进程非零退出 → 失败摘要,绝无成功/无剩余阻塞措辞;有待办时按钮可再点。"""
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = Workspace.init(tmp_path / "ws", topic="t")
    (tmp_path / "m.txt").write_text("会议内容")
    ws.add([tmp_path / "m.txt"])
    app = create_app(tmp_path)
    c = TestClient(app)
    # 可控非零退出(不写 blocked state)——驱动真实 TaskRegistry + run-summary
    task = _failing_run_task(app, ws)
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


def test_run_summary_failed_empty_workspace_stays_clean(tmp_path, monkeypatch):
    """#97 失败摘要 + #134 空仓:失败后主按钮仍是 disabled Up to date。"""
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = Workspace.init(tmp_path / "ws", topic="t")
    app = create_app(tmp_path)
    c = TestClient(app)
    task = _failing_run_task(app, ws)
    r = c.get(f"/w/ws/run-summary?task_id={task.task_id}")
    assert r.status_code == 200
    body = r.text
    assert "Run failed" in body or "运行失败" in body
    assert "No remaining blocks" not in body
    assert "无剩余阻塞" not in body
    assert app.state.registry.is_running("ws") is False
    assert 'id="run-btn-wrap"' in body
    assert "Up to date" in body or "已是最新" in body
    assert "disabled" in body
    assert "hx-post=" not in body


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


def _finished_ok_task(app, ws):
    task = app.state.registry.start(
        "ws", ws.root, [sys.executable, "-c", "print('ok')"]
    )
    _wait(task)
    assert task.exit_code == 0
    return task


def test_run_summary_clean_oob_refreshes_meta_and_nav(tmp_path, monkeypatch):
    """#180 S1: plan=clean 的成功 run-summary OOB 刷新元信息与左栏圆点。"""
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = Workspace.init(tmp_path / "ws", topic="t")
    body = "已收敛的理解正文"
    (ws.root / "understanding.md").write_text(body)
    state = ws.read_state()
    state.targets["understanding.md"] = TargetState(
        output_hash=_hash(body),
        status="ok",
        reason=None,
    )
    ws.write_state(state)
    app = create_app(tmp_path)
    c = TestClient(app)
    task = _finished_ok_task(app, ws)
    r = c.get(f"/w/ws/run-summary?task_id={task.task_id}")
    assert r.status_code == 200
    text = r.text
    assert "Up to date" in text or "已是最新" in text
    assert "No remaining blocks" in text or "无剩余阻塞" in text
    assert 'id="run-btn-wrap"' in text and "hx-swap-oob" in text
    assert 'id="targets-list"' in text and "hx-swap-oob" in text
    assert 'id="meta"' in text and "hx-swap-oob" in text
    assert "compose-migration-required" not in text
    assert "compose-degraded" not in text
    assert "dot blocked" not in text
    assert "meta-st blocked" not in text


def test_run_summary_still_blocked_keeps_meta_and_nav(tmp_path, monkeypatch):
    """#180 S2: 仍终态 blocked 时 run-summary 不得把 target 画成 ok。"""
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = Workspace.init(tmp_path / "ws", topic="t")
    old = "旧历史" * (UNDERSTANDING_MAX_CHARS // 3 + 1)
    (ws.root / "understanding.md").write_text(old)
    state = ws.read_state()
    state.targets["understanding.md"] = TargetState(
        output_hash=_hash(old),
        status="blocked",
        reason=REASON_COMPOSE_MIGRATION_REQUIRED,
    )
    ws.write_state(state)
    app = create_app(tmp_path)
    c = TestClient(app)
    task = _finished_ok_task(app, ws)
    r = c.get(f"/w/ws/run-summary?task_id={task.task_id}")
    assert r.status_code == 200
    text = r.text
    assert REASON_COMPOSE_MIGRATION_REQUIRED in text
    assert "dot blocked" in text
    assert "meta-st blocked" in text
    assert "Needs re-step" in text or "需要 re-step" in text
    assert "No remaining blocks" not in text
    assert "无剩余阻塞" not in text


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




def test_wrap_log_line_escapes_and_is_block():
    html = wrap_log_line('<script>x</script>')
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert html.startswith('<div class="log-line">')
    assert "\n" not in html
    empty = wrap_log_line("")
    assert empty == '<div class="log-line"></div>'
    cr = wrap_log_line("a\r\nb")
    assert "\n" not in cr and "\r" not in cr


def test_is_transport_line_and_not_fatal():
    assert is_transport_line("ERROR: Reconnecting… 5/5")
    assert is_transport_line("warning: Falling back from WebSockets to HTTPS transport")
    assert is_transport_line("request timed out")
    assert not is_transport_line("INFO: step progressed")
    assert not is_transport_line("")
    assert not is_transport_line("hook: SessionStart")
    running = StepTask(task_id="d", slug="ws", done=False, lines=["request timed out"])
    assert classify_task(running).kind == "running"


def test_phrase_transcript_not_source_text():
    t = translator("zh")
    a = phrase_for_work_key("references/x/transcript.md", "R", t)
    b = phrase_for_work_key("references/x/source_text.md", "R", t)
    assert "转写" in a and "提取" not in a
    assert "提取" in b and "转写" not in b


def test_resolve_phrase_finishing_only_after_pending():
    t = translator("zh")
    task = StepTask(task_id="a", slug="ws", job_kind="reconcile")
    assert "启动" in resolve_progress_phrase(task, t, pending_fn=lambda: [])
    class Item:
        key = "references/aaa/digest.md"
    task2 = StepTask(task_id="b", slug="ws", job_kind="reconcile")
    resolve_progress_phrase(
        task2, t, pending_fn=lambda: [Item()], title_fn=lambda i: "Alpha"
    )
    assert task2.saw_pending
    assert "收尾" in resolve_progress_phrase(task2, t, pending_fn=lambda: [])
    never = StepTask(task_id="c", slug="ws", job_kind="reconcile")
    assert "收尾" not in resolve_progress_phrase(never, t, pending_fn=lambda: [])


def test_progress_html_escapes_title():
    t = translator("en")
    task = StepTask(task_id="a", slug="ws", job_kind="prose", object_title="<x>")
    html = render_progress_html(task, t, now=task.created_at)
    assert "<x>" not in html
    assert "&lt;x&gt;" in html
    assert "\n" not in html


def test_stream_health_before_message_replay():
    t = StepTask(task_id="t1", slug="ws", done=True, exit_code=0)
    t.lines = ["ok", "ERROR: Reconnecting… 5/5"]
    t.transport_seen = True
    out = list(stream_events(t, t=translator("en")))
    kinds = []
    for chunk in out:
        if chunk.startswith("event: progress"):
            kinds.append("progress")
        elif chunk.startswith("event: health"):
            kinds.append("health")
        elif chunk.startswith("event: done"):
            kinds.append("done")
        elif chunk.startswith("data:"):
            kinds.append("message")
    assert kinds[0] == "progress"
    assert kinds[1] == "health"
    assert "message" in kinds
    assert kinds.index("health") < kinds.index("message")
    assert classify_task(StepTask(task_id="r", slug="ws", done=False)).kind == "running"


def test_progress_first_stale_not_second():
    class Item:
        def __init__(self, key):
            self.key = key
    task = StepTask(task_id="t", slug="ws", job_kind="reconcile", done=True, exit_code=0)
    items = [Item("references/aaa/digest.md"), Item("references/bbb/digest.md")]
    out = list(
        stream_events(
            task,
            t=translator("zh"),
            pending_fn=lambda: items,
            title_fn=lambda i: i.key.split("/")[1],
        )
    )
    prog = next(c for c in out if c.startswith("event: progress"))
    assert "aaa" in prog
    assert "bbb" not in prog


def test_pending_failure_still_reaches_done():
    task = StepTask(task_id="t", slug="ws", job_kind="reconcile", done=True, exit_code=0)
    task.lines = ["x"]

    def boom():
        raise RuntimeError("discover failed")

    out = list(stream_events(task, t=translator("en"), pending_fn=boom))
    assert any(c.startswith("event: done") for c in out)
    assert any("log-line" in c and "x" in c for c in out)
    prog = next(c for c in out if c.startswith("event: progress"))
    assert "Starting" in prog or "启动" in prog


def test_prose_progress_ignores_other_pending():
    class Item:
        key = "references/ccc/digest.md"
    task = StepTask(
        task_id="t",
        slug="ws",
        job_kind="prose",
        object_title="Memo",
        done=True,
        exit_code=0,
    )
    out = list(
        stream_events(
            task,
            t=translator("zh"),
            pending_fn=lambda: [Item()],
            title_fn=lambda i: "ccc",
        )
    )
    prog = next(c for c in out if c.startswith("event: progress"))
    assert "Memo" in prog
    assert "ccc" not in prog
    assert "消化" not in prog


def test_run_first_paint_has_progress_and_collapsed_log(tmp_path, monkeypatch):
    """#157 S3:POST /run 首屏人话+时长,原始日志折叠,无 Running… 状态句。"""
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = Workspace.init(tmp_path / "ws", topic="t")
    (tmp_path / "m.txt").write_text("会议内容")
    ws.add([tmp_path / "m.txt"])
    c = TestClient(create_app(tmp_path))
    r = c.post("/w/ws/run")
    assert r.status_code == 200
    body = r.text
    assert 'id="run-progress"' in body
    assert "run-progress-text" in body
    assert "秒" in body or "s" in body or "Starting" in body or "启动" in body
    assert 'class="run-log-details"' in body
    assert "<details" in body
    assert "open" not in body.split("run-log-details", 1)[1].split(">", 1)[0]
    assert 'sse-swap="done"' not in body
    # 状态区禁止无修饰 step.running;主按钮仍可用 Running…
    assert "Running…" not in body.split('id="run-progress"')[1].split("run-btn-wrap")[0]
    import re
    m = re.search(r"/w/ws/step/([0-9a-f]+)/stream", body)
    assert m
    stream = c.get(f"/w/ws/step/{m.group(1)}/stream").text
    assert "event: progress" in stream
    assert "event: done" in stream
    assert '<div class="log-line">' in stream


def test_stream_table_lines_are_separate_blocks(tmp_path):
    """#157 S1:目录表每行一块级节点。"""
    rows = [
        "| 标题 | 类型 | digest |",
        "|---|---|---|",
        "| a | 观测 | d1 |",
        "| b | 观测 | d2 |",
        "| c | 观测 | d3 |",
        "one",
        "two",
        "three",
        "four",
        "five",
    ]
    task = StepTask(task_id="t", slug="ws", done=True, exit_code=0, lines=rows)
    out = list(stream_events(task, t=translator("en")))
    msgs = [c for c in out if c.startswith("data: ") and "log-line" in c]
    assert len(msgs) >= 10
    assert sum(1 for c in msgs if "| 标题 |" in c or "|---|" in c or "| a |" in c) >= 3


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


def test_run_summary_isolates_this_task_knowledge_diagnostics(tmp_path):
    """历史 pending 不能出现在另一次 Run 的结果；两个 task 用各自启动边界。"""
    ws = Workspace.init(tmp_path / "ws", topic="t")
    app = create_app(tmp_path)
    c = TestClient(app)
    old = StepTask(task_id="old", slug="ws", done=True, exit_code=0)
    new = StepTask(task_id="new", slug="ws", done=True, exit_code=0)
    # 当前 state 是 new 后写入；old 的边界已包含同一 hash，new 的没有。
    st = ws.read_state()
    st.products["references/r/digest.md"] = ProductState(
        input_hash="x", knowledge_hash="new-hash", knowledge_generation="run-new",
        knowledge_diagnostic=KnowledgeDiagnostic(matched_entry_ids=["ke-a"], ambiguities=1, truncated=2, skipped=3),
    )
    ws.write_state(st)
    old.knowledge_before_products = {"references/r/digest.md": "run-new"}
    new.knowledge_before_products = {}
    app.state.registry._tasks.update({"old": old, "new": new})
    assert "Knowledge context: 0 matched" not in c.get("/w/ws/run-summary?task_id=old").text
    page = c.get("/w/ws/run-summary?task_id=new")
    assert "Knowledge context: 1 matched, 1 ambiguous, 2 truncated, 3 skipped" in page.text


def test_task_stream_and_cancel_reject_cross_workspace_task_id(tmp_path):
    Workspace.init(tmp_path / "a", topic="a")
    Workspace.init(tmp_path / "b", topic="b")
    app = create_app(tmp_path)
    task = StepTask(task_id="only-a", slug="a", done=False)
    app.state.registry._tasks[task.task_id] = task
    client = TestClient(app)
    assert client.get("/w/b/step/only-a/stream").status_code == 404
    assert client.post("/w/b/step/only-a/cancel").status_code == 404
