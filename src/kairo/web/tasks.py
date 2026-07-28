"""step 后台任务:子进程跑 step + 逐行缓冲 stdout;单 workspace 串行;SSE 事件流。

任务状态纯内存(server 重启丢运行中任务,本地单用户可接受)。
#97: 以退出码 + 取消意图分类任务终态,并从受限日志生成安全错误摘要。
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# 安全摘要:单行长度上限(字符)
_SUMMARY_MAX_LEN = 240

# 脱敏:凭证/密钥/Authorization 等模式(替换为 [redacted])
_REDACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(authorization\s*:\s*)\S+"),
    re.compile(r"(?i)(bearer\s+)\S+"),
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)\S+"),
    re.compile(r"(?i)(x-api-key\s*[=:]\s*)\S+"),
    re.compile(r"(?i)(token\s*[=:]\s*)\S+"),
    re.compile(r"(?i)(password\s*[=:]\s*)\S+"),
    re.compile(r"(?i)(secret\s*[=:]\s*)\S+"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{12,}\b"),
)

TaskKind = Literal["running", "succeeded", "failed", "cancelled", "missing"]


@dataclass
class TaskResult:
    """一次已结束(或缺失)任务的结构化终态,仅供本次页面呈现。"""

    kind: TaskKind
    exit_code: int | None = None
    message: str = ""


@dataclass
class StepTask:
    task_id: str
    slug: str
    lines: list[str] = field(default_factory=list)
    done: bool = False
    exit_code: int | None = None
    cancel_requested: bool = False
    proc: subprocess.Popen | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


def redact_sensitive(text: str) -> str:
    """脱敏凭证类片段;不承诺完整日志保真。"""
    out = text
    for pat in _REDACT_PATTERNS:
        if pat.groups:
            out = pat.sub(r"\1[redacted]", out)
        else:
            out = pat.sub("[redacted]", out)
    return out


def _pick_error_line(lines: list[str]) -> str:
    """从缓冲末尾挑一条最能说明失败的行。"""
    errorish = re.compile(
        r"(?i)(error|fail|exception|traceback|denied|refused|timeout|unauthorized|status\s*[45]\d\d)"
    )
    candidates = [ln.strip() for ln in lines if ln and ln.strip()]
    if not candidates:
        return ""
    # 优先最后一条 error-like
    for ln in reversed(candidates):
        if errorish.search(ln):
            return ln
    return candidates[-1]


def safe_error_summary(lines: list[str], *, max_len: int = _SUMMARY_MAX_LEN) -> str:
    """从已缓冲日志提炼单行、脱敏、截断的安全错误摘要。"""
    raw = _pick_error_line(lines)
    if not raw:
        return ""
    # 压成单行
    one = re.sub(r"\s+", " ", raw).strip()
    one = redact_sensitive(one)
    if len(one) > max_len:
        one = one[: max_len - 1].rstrip() + "…"
    return one


# #105:Grok/代理/CLI 超时等致命行 — 进程虽可能 exit 0,任务仍应 failed
_FATAL_AGENT_LINE = re.compile(
    r"(?i)("
    r"internal error"
    r"|request error stream"
    r"|error sending request"
    r"|cli-chat-proxy\.grok\.com"
    r"|cli agent timeout"
    r"|CLI agent timeout"
    r")"
)


def is_fatal_agent_line(line: str) -> bool:
    """判定一行日志是否为 agent/代理致命失败(供 classify 与测试)。"""
    if not line or not str(line).strip():
        return False
    return bool(_FATAL_AGENT_LINE.search(str(line)))


def has_fatal_agent_error(lines: list[str]) -> bool:
    return any(is_fatal_agent_line(ln) for ln in lines)


def classify_task(task: StepTask | None) -> TaskResult:
    """退出码 + 取消意图 + 致命日志 → 互斥终态。task 缺失或未结束均非成功。

    #105:已结束且日志含致命 agent 错误时,即使 exit_code==0 也判 failed,
    避免「黑框 Error + 绿勾成功」;hang 由 runner 超时保证会 done。
    """
    if task is None:
        return TaskResult(kind="missing", message="task not found")
    with task.lock:
        done = task.done
        code = task.exit_code
        cancelled = task.cancel_requested
        lines = list(task.lines)
    if not done:
        return TaskResult(kind="running", exit_code=code, message="task still running")
    # 用户取消且进程已结束 → cancelled(优先于非零退出码)
    if cancelled:
        return TaskResult(kind="cancelled", exit_code=code, message="")
    if code is not None and code != 0:
        return TaskResult(
            kind="failed",
            exit_code=code,
            message=safe_error_summary(lines),
        )
    # exit 0 但日志已暴露致命 agent/代理错误 → failed(#105 S2)
    if has_fatal_agent_error(lines):
        return TaskResult(
            kind="failed",
            exit_code=0 if code is None else code,
            message=safe_error_summary(lines),
        )
    return TaskResult(kind="succeeded", exit_code=0 if code is None else code, message="")


def result_payload(result: TaskResult) -> dict:
    """SSE done / 查询共用的精简字段。"""
    return {
        "kind": result.kind,
        "exit_code": result.exit_code,
        "message": result.message,
    }


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
        with task.lock:
            task.cancel_requested = True
        try:
            os.killpg(os.getpgid(task.proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            task.proc.terminate()  # fallback if group lookup fails
        return True


def stream_events(task: StepTask) -> Iterator[str]:
    """SSE:先回放已缓冲行,再 tail 新行,进程结束推 done(结构化终态)。

    客户端断开时生成器继续在 threadpool 线程中轮询直到 task.done(单用户本地可接受;
    _pump 独立线程,无子进程泄漏)。
    """
    idx = 0
    while True:
        with task.lock:
            new = task.lines[idx:]
            done = task.done
        for line in new:
            yield f"data: {line}\n\n"
        idx += len(new)
        if done:
            result = classify_task(task)
            # 兼容旧客户端:data 仍可解析为退出码;同时附带 kind/message JSON
            payload = result_payload(result)
            # 第一字段保持 exit_code 整数可读性:若只要 code 的旧逻辑,JSON 中仍有 exit_code
            yield f"event: done\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            return
        time.sleep(0.1)
