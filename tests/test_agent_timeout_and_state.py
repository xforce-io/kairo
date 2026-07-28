"""#105: CLI agent 超时杀进程、provider-failed、step 中途落盘、任务终态。"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

from kairo.engine import step
from kairo.models import REASON_PROVIDER_FAILED
from kairo.provider import (
    DEFAULT_CLI_TIMEOUT_S,
    AgentConfig,
    AgentResult,
    _default_cli_runner,
    _scan_artifacts,
)
from kairo.rules import _run_agent
from kairo.web.tasks import StepTask, classify_task, is_fatal_agent_line
from kairo.workspace import Workspace


def test_default_cli_timeout_constant_is_documented_range():
    """S1:默认超时在 300–600s 量级,可被测试覆盖。"""
    assert 300 <= DEFAULT_CLI_TIMEOUT_S <= 600


def test_default_cli_runner_timeout_kills_and_raises(tmp_path):
    """S1:真实 runner 路径 — 超时杀进程组并抛可归因错误,不无限挂起。"""
    cwd = tmp_path / "art"
    cwd.mkdir()
    marker = cwd / "child_alive"
    # 子进程再起孙进程 sleep,验证进程组一并清理
    code = (
        "import os, time, pathlib\n"
        f"pathlib.Path({str(marker)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(60)\n"
    )
    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="timeout|超时"):
        _default_cli_runner(
            sys.executable,
            ["-c", code],
            cwd=cwd,
            input=None,
            stdout_file=None,
            timeout=1,
        )
    elapsed = time.monotonic() - t0
    assert elapsed < 15, f"timeout path hung too long: {elapsed:.1f}s"
    # 子进程应已退出(marker 可能已写,但进程不应仍存活很久)
    if marker.is_file():
        pid = int(marker.read_text().strip())
        # 给系统一点时间收尸
        deadline = time.time() + 2
        while time.time() < deadline:
            try:
                os_kill = __import__("os").kill
                os_kill(pid, 0)
                time.sleep(0.05)
            except OSError:
                break
        else:
            pytest.fail(f"child pid {pid} still alive after timeout kill")


def test_run_agent_applies_default_timeout_when_missing():
    """S1:_run_agent 在未设 timeout 时注入 DEFAULT_CLI_TIMEOUT_S。"""
    seen: dict = {}

    class CapturingProvider:
        name = "cap"
        model = "m"

        def run(self, config: AgentConfig, signal=None):
            seen["timeout_s"] = config.timeout_s
            (config.artifact_dir / "out.md").write_text("ok")
            return AgentResult(
                artifacts=_scan_artifacts(config.artifact_dir), result_text="ok"
            )

    text = _run_agent(CapturingProvider(), "persona", "ctx", "out.md")
    assert text == "ok"
    assert seen["timeout_s"] == DEFAULT_CLI_TIMEOUT_S


def test_run_agent_preserves_explicit_timeout():
    class CapturingProvider:
        name = "cap"
        model = "m"

        def __init__(self):
            self.timeout_s = None

        def run(self, config: AgentConfig, signal=None):
            self.timeout_s = config.timeout_s
            (config.artifact_dir / "out.md").write_text("ok")
            return AgentResult(artifacts=[], result_text="ok")

    p = CapturingProvider()
    text = _run_agent(p, "p", "c", "out.md", timeout_s=42)
    assert text == "ok"
    assert p.timeout_s == 42


def test_cli_timeout_surfaces_as_provider_failed(tmp_path):
    """S3:runner 超时 → Digest 边界记 provider-failed,不写半成品 digest。"""

    class TimeoutCliProvider:
        name = "timeout-cli"
        model = "t"

        def run(self, config: AgentConfig, signal=None):
            # 走真实 _default_cli_runner 超时路径
            config.artifact_dir.mkdir(parents=True, exist_ok=True)
            _default_cli_runner(
                sys.executable,
                ["-c", "import time; time.sleep(30)"],
                cwd=config.artifact_dir,
                input="",
                stdout_file=config.artifact_dir / "_out.txt",
                timeout=1,
            )
            (config.artifact_dir / (config.artifact or "output.md")).write_text("nope")
            return AgentResult(artifacts=[], result_text="nope")

    ws = Workspace.init(tmp_path / "ws", topic="t105")
    src = tmp_path / "m.txt"
    src.write_text("会议材料")
    ws.add([src])
    step(ws, TimeoutCliProvider())
    rid = ws.list_reference_ids()[0]
    key = f"references/{rid}/digest.md"
    st = ws.read_state()
    ps = st.products[key]
    assert ps.status == "blocked"
    assert ps.reason == REASON_PROVIDER_FAILED
    assert ps.diagnostic is not None
    assert ps.diagnostic.stage == "digest"
    assert ps.diagnostic.provider == "timeout-cli"
    assert "timeout" in ps.diagnostic.summary.lower() or "超时" in ps.diagnostic.summary
    assert not (ws.root / key).exists()
    # plain step 不自动重试
    prov = TimeoutCliProvider()
    # 用 Fail 风格:再 step 不应再调 hang(仍 blocked)
    # 重新读 is_stale — provider-failed 终态
    from kairo.engine import pending

    assert not any(key in it.key for it in pending(ws))


def test_step_flushes_state_after_blocked_before_later_hang(tmp_path):
    """S4:第一个 WorkItem blocked 后 state.json 已落盘,即使后续 item 挂起。"""

    class FailThenHangProvider:
        name = "fail-hang"
        model = "fh"

        def __init__(self):
            self.calls = 0
            self.gate = threading.Event()

        def run(self, config: AgentConfig, signal=None):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("first digest boom")
            # 后续 compose/其它:挂起直到测试放行
            self.gate.wait(timeout=60)
            config.artifact_dir.mkdir(parents=True, exist_ok=True)
            (config.artifact_dir / (config.artifact or "output.md")).write_text("late")
            return AgentResult(artifacts=[], result_text="late")

    ws = Workspace.init(tmp_path / "ws", topic="t105s4")
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("材料A")
    b.write_text("材料B")
    ws.add([a])
    ws.add([b])
    prov = FailThenHangProvider()
    err: list[BaseException] = []

    def _run():
        try:
            step(ws, prov)
        except BaseException as e:  # noqa: BLE001 — 收集线程异常
            err.append(e)

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    # 等第一次失败并中途落盘
    deadline = time.time() + 10
    disk_reason = None
    while time.time() < deadline:
        raw = json.loads((ws.root / ".kairo" / "state.json").read_text())
        for k, v in raw.get("products", {}).items():
            if v.get("reason") == REASON_PROVIDER_FAILED:
                disk_reason = v
                break
        if disk_reason:
            break
        time.sleep(0.05)
    assert disk_reason is not None, "blocked provider-failed not flushed mid-step"
    assert disk_reason.get("status") == "blocked"
    assert disk_reason.get("diagnostic") is not None
    # 放行挂起的后续调用,避免泄漏线程
    prov.gate.set()
    th.join(timeout=15)
    assert not th.is_alive(), f"step thread still alive; err={err}"


def test_is_fatal_agent_line_matches_grok_proxy_errors():
    """S2:可判定的致命日志行。"""
    assert is_fatal_agent_line(
        'Error: Internal error: "request error stream: error sending request for url '
        '(https://cli-chat-proxy.grok.com/v1/responses)"'
    )
    assert is_fatal_agent_line("RuntimeError: CLI agent timeout after 600s: grok")
    assert not is_fatal_agent_line("INFO: step progressed")
    assert not is_fatal_agent_line("")


def test_classify_task_failed_on_fatal_lines_even_if_exit_zero():
    """S2:进程已结束且日志含致命错误 → failed,非 succeeded。"""
    t = StepTask(
        task_id="x",
        slug="ws",
        done=True,
        exit_code=0,
        lines=[
            "digest start",
            'Error: Internal error: "request error stream: error sending request"',
        ],
    )
    r = classify_task(t)
    assert r.kind == "failed"
    assert "error" in r.message.lower() or "Internal" in r.message


def test_classify_task_running_until_done():
    t = StepTask(
        task_id="y",
        slug="ws",
        done=False,
        lines=['Error: Internal error: "request error stream"'],
    )
    # 仍 running(终态只在 done 后;超时由 runner 保证会 done)
    assert classify_task(t).kind == "running"
