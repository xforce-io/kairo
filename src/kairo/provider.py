"""AgentProvider —— 唯一的 agent 缝。

#4:从「`complete(prompt)->str` 模型缝」升级为「`run(config)->artifacts` agent 缝」。
agent 靠往 `artifact_dir` 写文件来通信;外壳(rules/engine)只编排与记账。
backend:StubProvider(测试)/ GrokProvider / OpenAICompatibleProvider /
ClaudeCodeProvider / CodexProvider。
默认真实路径:本机 grok CLI 可用 → GrokProvider;否则 openai endpoint;
否则 claude CLI;否则 stub。Grok 无 --add-dir,read_dirs 场景请用 claude-code。
"""

from __future__ import annotations

import hashlib
import json
import os
import tomllib
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
    read_dirs: list[Path] = field(default_factory=list)  # 只读授权目录(corpus 参考层 → --add-dir)


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


def _config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "kairo" / "config.toml"


def resolve_openai_provider_config() -> dict | None:
    """解析 machine-local OpenAI-compatible endpoint 配置。

    密钥只从环境变量取,避免落入 workspace 或配置样例中。
    """
    path = _config_path()
    if not path.is_file():
        return None
    section = (tomllib.loads(path.read_text()).get("provider") or {}).get("openai") or {}

    def _value(key: str, default_env: str | None = None) -> str:
        env_name = str(section.get(f"{key}_env") or default_env or "").strip()
        if env_name:
            env_value = os.environ.get(env_name)
            if env_value:
                return env_value.strip()
        return str(section.get(key) or "").strip()

    base_url = _value("base_url")
    model = _value("model")
    api_key_env = str(section.get("api_key_env") or "OPENAI_API_KEY").strip()
    api_key = os.environ.get(api_key_env) if api_key_env else None
    if not (base_url and model and api_key):
        return None
    return {"base_url": base_url, "model": model, "api_key": api_key}


class OpenAICompatibleProvider:
    """OpenAI-compatible Chat Completions endpoint provider。

    这是薄 LLM adapter,不是工具型 agent:它只把 persona/context 发给 endpoint,
    再把最终文本写回 artifact。
    """

    name = "openai"

    def __init__(
        self, *, base_url: str, api_key: str, model: str, client=None
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self._client = client

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except ImportError as e:
            raise RuntimeError("openai provider 需要安装 openai Python SDK") from e
        self._client = OpenAI(base_url=self.base_url, api_key=self.api_key)
        return self._client

    def run(self, config: AgentConfig, signal=None) -> AgentResult:
        config.artifact_dir.mkdir(parents=True, exist_ok=True)
        prompt = f"{config.persona}\n\n---\n\n{config.context}"
        (config.artifact_dir / "_prompt.md").write_text(prompt)
        kwargs = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": config.persona},
                {"role": "user", "content": config.context},
            ],
        }
        if config.timeout_s is not None:
            kwargs["timeout"] = config.timeout_s
        resp = self._get_client().chat.completions.create(**kwargs)
        choices = getattr(resp, "choices", None) or []
        message = getattr(choices[0], "message", None) if choices else None
        result = getattr(message, "content", None)
        if isinstance(result, list):
            result = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in result
            )
        if not isinstance(result, str) or not result.strip():
            raise RuntimeError("openai provider 返回空响应")
        (config.artifact_dir / (config.artifact or "output.md")).write_text(result)
        return AgentResult(
            artifacts=_scan_artifacts(config.artifact_dir), result_text=result
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
        add_dir_args = []
        for d in config.read_dirs:  # corpus 只读参考层 → 授 agent 读访问(写仍限 cwd)
            add_dir_args += ["--add-dir", str(d)]
        if config.read_dirs:  # 非交互预授只读工具,否则 -p 下读 corpus 会被拒
            add_dir_args += ["--allowedTools", "Read", "Glob", "Grep"]
        self._runner(
            "claude",
            ["-p", "--model", self.model, "--output-format", "json", *add_dir_args],
            cwd=config.artifact_dir,
            input=prompt,
            stdout_file=stdout_file,
            timeout=config.timeout_s,
        )
        # claude -p 把回答写 stdout 的 json result(不写文件)→ 取回落到 config.artifact
        if not stdout_file.exists():
            raise RuntimeError(f"claude-code 无 stdout 输出:{stdout_file}")
        data = json.loads(stdout_file.read_text())
        # claude -p 报错(连接中断/执行失败)时 is_error=true,且把错误信息塞进 result;
        # 必须在写产物前拦截,否则错误文本会被当正常产物写入 + 记账(#8)
        if data.get("is_error"):
            raise RuntimeError(f"claude-code 报错:{data.get('result')!r}")
        result = data.get("result")
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


class GrokProvider:
    """驱动 `grok -p` CLI。agent 在 artifact_dir(cwd)里写文件。runner 可注入便于测试。

    #61:Grok 无 --add-dir;read_dirs(corpus/附件)忽略,相关场景请用 claude-code。
    JSON 成功字段为 text;错误为 {"type":"error","message":...},写产物前拦截(#8)。
    """

    name = "grok"

    def __init__(self, model: str = "", runner=None) -> None:
        self.model = model
        self._runner = runner or _default_cli_runner

    def run(self, config: AgentConfig, signal=None) -> AgentResult:
        config.artifact_dir.mkdir(parents=True, exist_ok=True)
        prompt = f"{config.persona}\n\n---\n\n{config.context}"
        (config.artifact_dir / "_prompt.md").write_text(prompt)
        stdout_file = config.artifact_dir / "_grok_stdout.json"
        # -p 要求 <PROMPT>;read_dirs 无 CLI 等价物,MVP 忽略(见设计 #61)
        args = ["-p", prompt, "--output-format", "json"]
        if self.model.strip():
            args += ["-m", self.model]
        self._runner(
            "grok",
            args,
            cwd=config.artifact_dir,
            input=prompt,
            stdout_file=stdout_file,
            timeout=config.timeout_s,
        )
        if not stdout_file.exists():
            raise RuntimeError(f"grok 无 stdout 输出:{stdout_file}")
        data = json.loads(stdout_file.read_text())
        if data.get("type") == "error":
            raise RuntimeError(f"grok 报错:{data.get('message')!r}")
        result = data.get("text")
        if not isinstance(result, str) or not result.strip():
            raise RuntimeError(f"grok stdout 缺 text 字段:{stdout_file}")
        (config.artifact_dir / (config.artifact or "output.md")).write_text(result)
        return AgentResult(
            artifacts=_scan_artifacts(config.artifact_dir), result_text=result
        )


_BACKENDS = {
    "stub": StubProvider,
    "claude-code": ClaudeCodeProvider,
    "codex": CodexProvider,
    "grok": GrokProvider,
}


def _openai_provider_from_config() -> OpenAICompatibleProvider | None:
    cfg = resolve_openai_provider_config()
    if cfg is None:
        return None
    return OpenAICompatibleProvider(**cfg)


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

    auto:grok CLI 可用 → GrokProvider;否则 OpenAI-compatible endpoint;
    否则 claude CLI → ClaudeCodeProvider;否则 StubProvider。
    """
    if os.environ.get("KAIRO_STUB"):
        return StubProvider()
    explicit = os.environ.get("KAIRO_PROVIDER")
    if explicit:
        if explicit == "openai":
            provider = _openai_provider_from_config()
            if provider is None:
                raise RuntimeError("KAIRO_PROVIDER=openai 但缺少 provider.openai 配置或 API key")
            return provider
        return _BACKENDS.get(explicit, StubProvider)()
    if _cli_available("grok"):
        return GrokProvider()
    provider = _openai_provider_from_config()
    if provider is not None:
        return provider
    if _cli_available("claude"):
        return ClaudeCodeProvider()
    return StubProvider()
