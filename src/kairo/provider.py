"""AgentProvider —— 唯一的 agent 缝。

#4:从「`complete(prompt)->str` 模型缝」升级为「`run(config)->artifacts` agent 缝」。
agent 靠往 `artifact_dir` 写文件来通信;外壳(rules/engine)只编排与记账。
backend:StubProvider(测试)/ ClaudeCodeProvider / CodexProvider(CLI,subscription)。
真实 LLM 只走 subscription(claude-code),不支持直连 API。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class AgentConfig:
    """一次 agent 运行的输入。agent 靠往 artifact_dir 写文件来「输出」。"""

    persona: str  # agent 是谁 + 方法论(→ system)
    context: str  # 任务输入(→ user)
    artifact_dir: Path  # cwd;产物落处
    model: str
    schema: dict | None = None  # 结构化输出契约(api backend 用;CLI 可忽略)
    artifact: str | None = None  # schema/产物落到哪个文件名
    timeout_s: int | None = None


@dataclass
class AgentResult:
    artifacts: list[Path] = field(default_factory=list)
    result_text: str | None = None


class AgentProvider(Protocol):
    """运行一个被约束的 agent 到完成。输出 = 它写进 artifact_dir 的文件。"""

    name: str
    model: str

    def run(self, config: AgentConfig, signal=None) -> AgentResult: ...


def _scan_artifacts(d: Path) -> list[Path]:
    """artifact = 非内部文件;'_'/'.' 前缀为内部通信(prompt/stdout),不计。"""
    if not d.exists():
        return []
    return sorted(
        p for p in d.iterdir() if p.is_file() and not p.name.startswith(("_", "."))
    )


class StubProvider:
    """确定性 Fake:离线 + 测试。echo 输入 + STUB 标记,只验骨牌链、不被当真。

    输出只依赖 (persona, context),不依赖 artifact_dir 路径 —— 否则破坏收敛幂等。
    """

    name = "stub"
    model = "stub"

    def run(self, config: AgentConfig, signal=None) -> AgentResult:
        config.artifact_dir.mkdir(parents=True, exist_ok=True)
        seed = f"{config.persona}\n{config.context}"
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8]
        content = (
            f"⚠️ STUB OUTPUT [{digest}]\n\n"
            f"{config.persona.strip()}\n\n{config.context.strip()}"
        )
        (config.artifact_dir / (config.artifact or "output.md")).write_text(content)
        return AgentResult(
            artifacts=_scan_artifacts(config.artifact_dir), result_text=content
        )


def _default_cli_runner(cmd, args, *, cwd, input, stdout_file=None, timeout=None):
    import subprocess

    # 回答在 stdout(claude)时重定向到文件;codex 用 --output-last-message 自写文件,无需重定向
    out = open(stdout_file, "w") if stdout_file else None
    try:
        subprocess.run(
            [cmd, *args],
            cwd=str(cwd),
            input=input,
            text=True,
            stdout=out,
            timeout=timeout,
            check=False,  # exit code 不当异常;外壳凭产物判断
        )
    finally:
        if out:
            out.close()


class ClaudeCodeProvider:
    """驱动 `claude -p` CLI。agent 在 artifact_dir(cwd)里写文件。runner 可注入便于测试。"""

    name = "claude-code"

    def __init__(self, model: str = "opus", runner=None) -> None:
        self.model = model
        self._runner = runner or _default_cli_runner

    def run(self, config: AgentConfig, signal=None) -> AgentResult:
        config.artifact_dir.mkdir(parents=True, exist_ok=True)
        prompt = f"{config.persona}\n\n---\n\n{config.context}"
        (config.artifact_dir / "_prompt.md").write_text(prompt)  # 内部文件,不计 artifact
        stdout_file = config.artifact_dir / "_claude_stdout.json"
        self._runner(
            "claude",
            ["-p", "--model", self.model, "--output-format", "json"],
            cwd=config.artifact_dir,
            input=prompt,
            stdout_file=stdout_file,
            timeout=config.timeout_s,
        )
        # claude -p 把回答写 stdout 的 json result(不写文件)→ 取回落到 config.artifact
        if not stdout_file.exists():
            raise RuntimeError(f"claude-code 无 stdout 输出:{stdout_file}")
        result = json.loads(stdout_file.read_text()).get("result")
        if not isinstance(result, str):
            raise RuntimeError(f"claude-code stdout 缺 result 字段:{stdout_file}")
        (config.artifact_dir / (config.artifact or "output.md")).write_text(result)
        return AgentResult(
            artifacts=_scan_artifacts(config.artifact_dir), result_text=result
        )


class CodexProvider:
    """驱动 `codex exec` CLI。runner 可注入便于测试。"""

    name = "codex"

    def __init__(self, model: str = "", runner=None) -> None:
        self.model = model
        self._runner = runner or _default_cli_runner

    def run(self, config: AgentConfig, signal=None) -> AgentResult:
        config.artifact_dir.mkdir(parents=True, exist_ok=True)
        prompt = f"{config.persona}\n\n---\n\n{config.context}"
        (config.artifact_dir / "_prompt.md").write_text(prompt)
        last_msg = config.artifact_dir / "_codex_last.txt"
        args = [
            "exec",
            "-C",
            str(config.artifact_dir),
            "--sandbox",
            "workspace-write",
            "--skip-git-repo-check",
            "--output-last-message",
            str(last_msg),
        ]
        if self.model.strip():
            args += ["-m", self.model]
        self._runner(
            "codex",
            args,
            cwd=config.artifact_dir,
            input=prompt,
            timeout=config.timeout_s,
        )
        # codex 把最终消息写到 --output-last-message 文件 → 取回落到 config.artifact
        if not last_msg.exists():
            raise RuntimeError(f"codex 无 last-message 输出:{last_msg}")
        result = last_msg.read_text()
        (config.artifact_dir / (config.artifact or "output.md")).write_text(result)
        return AgentResult(
            artifacts=_scan_artifacts(config.artifact_dir), result_text=result
        )


_BACKENDS = {
    "stub": StubProvider,
    "claude-code": ClaudeCodeProvider,
    "codex": CodexProvider,
}


def _cli_available(cmd: str) -> bool:
    """探活:`<cmd> --version` exit 0 → True;异常 / 非 0 → False。"""
    import subprocess

    try:
        r = subprocess.run(
            [cmd, "--version"], capture_output=True, timeout=10, check=False
        )
        return r.returncode == 0
    except Exception:
        return False


def select_provider():
    """选 backend:KAIRO_STUB(测试隔离,最高)> KAIRO_PROVIDER(显式)> auto。

    auto 只走 subscription:claude CLI 可用 → ClaudeCodeProvider;否则 StubProvider。
    """
    if os.environ.get("KAIRO_STUB"):
        return StubProvider()
    explicit = os.environ.get("KAIRO_PROVIDER")
    if explicit:
        return _BACKENDS.get(explicit, StubProvider)()
    if _cli_available("claude"):
        return ClaudeCodeProvider()
    return StubProvider()
