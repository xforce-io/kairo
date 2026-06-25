"""step 后台任务:子进程跑 step + 逐行缓冲 stdout;单 workspace 串行;SSE 事件流。

任务状态纯内存(server 重启丢运行中任务,本地单用户可接受)。
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class StepTask:
    task_id: str
    slug: str
    lines: list[str] = field(default_factory=list)
    done: bool = False
    exit_code: int | None = None
    proc: subprocess.Popen | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class TaskRegistry:
    """task_id → StepTask;并维护每个 slug 的在跑任务(串行锁)。"""

    def __init__(self, max_lines: int = 2000) -> None:
        self._tasks: dict[str, StepTask] = {}
        self._running_by_slug: dict[str, str] = {}
        self._max_lines = max_lines
        self._guard = threading.Lock()

    def is_running(self, slug: str) -> bool:
        with self._guard:
            tid = self._running_by_slug.get(slug)
            return tid is not None and not self._tasks[tid].done

    def start(self, slug: str, cwd: Path, argv: list[str]) -> StepTask:
        with self._guard:
            tid = self._running_by_slug.get(slug)
            if tid is not None and not self._tasks[tid].done:
                raise RuntimeError(f"step already running for {slug}")
            task_id = uuid.uuid4().hex[:12]
            proc = subprocess.Popen(
                argv,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
            task = StepTask(task_id=task_id, slug=slug, proc=proc)
            self._tasks[task_id] = task
            self._running_by_slug[slug] = task_id
        threading.Thread(target=self._pump, args=(task,), daemon=True).start()
        return task

    def _pump(self, task: StepTask) -> None:
        assert task.proc is not None and task.proc.stdout is not None
        for raw in task.proc.stdout:
            line = raw.rstrip("\n")
            with task.lock:
                task.lines.append(line)
                if len(task.lines) > self._max_lines:
                    del task.lines[: len(task.lines) - self._max_lines]
        task.proc.wait()
        with task.lock:
            task.exit_code = task.proc.returncode
            task.done = True

    def get(self, task_id: str) -> StepTask | None:
        return self._tasks.get(task_id)

    def cancel(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task is None or task.proc is None or task.done:
            return False
        try:
            os.killpg(os.getpgid(task.proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            task.proc.terminate()  # fallback if group lookup fails
        return True


def stream_events(task: StepTask) -> Iterator[str]:
    """SSE:先回放已缓冲行,再 tail 新行,进程结束推 done(exit_code)。

    客户端断开时生成器继续在 threadpool 线程中轮询直到 task.done(单用户本地可接受;
    _pump 独立线程,无子进程泄漏)。
    """
    idx = 0
    while True:
        with task.lock:
            new = task.lines[idx:]
            done = task.done
            code = task.exit_code
        for line in new:
            yield f"data: {line}\n\n"
        idx += len(new)
        if done:
            yield f"event: done\ndata: {code}\n\n"
            return
        time.sleep(0.1)
