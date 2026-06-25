# tests/test_web_tasks.py
import sys
import time

from kairo.web.tasks import StepTask, TaskRegistry, stream_events


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
