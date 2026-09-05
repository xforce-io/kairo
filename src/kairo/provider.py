"""AgentProvider —— 唯一的 agent 缝。

#4:从「`complete(prompt)->str` 模型缝」升级为「`run(config)->artifacts` agent 缝」。
agent 靠往 `artifact_dir` 写文件来通信;外壳(rules/engine)只编排与记账。
backend:StubProvider(测试)/ GrokProvider / OpenAICompatibleProvider /
ClaudeCodeProvider / CodexProvider。
auto 偏好:Codex → Grok → Claude → OpenAI-compatible → Stub;
材料路径会跳过不支持授读的候选。显式 provider 不过滤,仍按契约 fail-closed。
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

# #105:CLI agent 默认超时(秒)。防 grok/claude 网络挂起时无限 wait。
# 合法长 digest 可经 AgentConfig.timeout_s 覆盖;测试注入短超时。
DEFAULT_CLI_TIMEOUT_S = 600

# start_new_session 的 CLI 子树 pgid;Web cancel 只 kill kairo 会话时据此清孤儿(#105)
_active_cli_pgids: set[int] = set()
_cli_pgid_lock = threading.Lock()
_cli_cleanup_handlers_installed = False


@dataclass
class AgentConfig:
    """一次 agent 运行的输入。agent 靠往 artifact_dir 写文件来「输出」。"""

    persona: str  # agent 是谁 + 方法论(→ system)
    context: str  # 任务输入(→ user)
    artifact_dir: Path  # cwd;产物落处
    model: str
    schema: dict | None = None  # 结构化输出契约(api backend 用;CLI 可忽略)
    artifact: str | None = None  # schema/产物落到哪个文件名
    timeout_s: int | None = None  # None → runner 使用 DEFAULT_CLI_TIMEOUT_S(#105)
    read_dirs: list[Path] = field(
        default_factory=list
    )  # agent 可读目录；provider 不得扩大源目录写权限


@dataclass
class AgentResult:
    artifacts: list[Path] = field(default_factory=list)
    result_text: str | None = None


class AgentProvider(Protocol):
    """运行一个被约束的 agent 到完成。输出 = 它写进 artifact_dir 的文件。"""

    name: str
    model: str
    supports_read_dirs: bool

    def run(self, config: AgentConfig, signal=None) -> AgentResult: ...


def _scan_artifacts(d: Path) -> list[Path]:
    """artifact = 非内部文件;'_'/'.' 前缀为内部通信(prompt/stdout),不计。"""
    if not d.exists():
        return []
    return sorted(
        p for p in d.iterdir() if p.is_file() and not p.name.startswith(("_", "."))
    )


def _parse_stub_catalog(context: str) -> list[tuple[str, str, str]]:
    """从 Compose context 的来源目录表解析 (S-id, title, digest_path)。"""
    import re

    rows: list[tuple[str, str, str]] = []
    for m in re.finditer(
        r"\|\s*(S-[0-9a-f]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*(references/[^|]+/digest\.md)\s*\|",
        context,
    ):
        sid, _ref, title, path = (
            m.group(1),
            m.group(2).strip(),
            m.group(3).strip(),
            m.group(4).strip(),
        )
        rows.append((sid, title, path))
    return rows


def _digest_bodies_from_context(context: str) -> list[str]:
    """抽取 context 中各 digest 块正文(供 stub 写入事实层,保持链上可断言)。"""
    import re

    parts = re.split(r"\n(?=\[(?:S-[0-9a-f]+ \||来源:))", context)
    bodies: list[str] = []
    for part in parts:
        if not part.startswith("["):
            continue
        nl = part.find("\n")
        if nl < 0:
            continue
        body = part[nl + 1 :].strip()
        if body:
            bodies.append(body)
    return bodies


def _stub_compose_document(
    persona: str, context: str, artifact_dir: Path | None = None
) -> str:
    """#99:stub 产出可通过溯源校验的紧凑文档(确定性,依赖 persona+context+必读文件)。"""
    seed = f"{persona}\n{context}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8]
    catalog = _parse_stub_catalog(context)
    # 勿用 persona 里「判断进 assessment」误判事实层;认协议标题
    judgment = "溯源输出协议 · 判断层" in persona or "【判断层】" in persona
    if not catalog:
        # 无 digest 时仍给最小合法结构
        if judgment:
            return (
                f"⚠️ STUB ASSESSMENT [{digest}]\n\n"
                f"## 判断\n\n暂无新材料支撑的判断。\n\n"
                f"## 依据事实索引\n\n| 锚点 | 说明 |\n|---|---|\n| — | 无 |\n"
            )
        return (
            f"⚠️ STUB UNDERSTANDING [{digest}]\n\n"
            f"## 概览\n\n暂无 fold 材料。\n\n"
            f"## 来源索引\n\n| ID | 材料 | 可核对来源 |\n|---|---|---|\n"
        )
    sids = [c[0] for c in catalog]
    scope = " ".join(f"〔{s}〕" for s in sorted(sids))
    glossary_bit = ""
    if "[领域真名册]" in persona:
        glossary_bit = (
            persona[persona.find("[领域真名册]") :].split("[阅读纪律]", 1)[0].strip()
        )
    knowledge_bit = ""
    if "[领域知识上下文]" in persona:
        knowledge_bit = (
            persona[persona.find("[领域知识上下文]") :]
            .split("[阅读纪律]", 1)[0]
            .strip()
        )
    bodies = _stub_required_bodies(context, artifact_dir)
    if not bodies:
        bodies = _digest_bodies_from_context(context)
    body_bits = [
        f"⚠️ STUB {'ASSESSMENT' if judgment else 'UNDERSTANDING'} [{digest}]",
        "",
        "## 主题",
        "",
        f"证据范围:{scope}",
        "",
    ]
    if glossary_bit:
        body_bits += [glossary_bit, ""]
    if knowledge_bit:
        body_bits += [knowledge_bit, ""]
    for i, (sid, title, path) in enumerate(catalog):
        core = sid[2:]
        fid = f"F-{core}-01"
        snippet = bodies[i] if i < len(bodies) else ""
        # 去掉 digest 内可能的路径泄漏,避免校验失败
        snippet = snippet.replace("references/", "ref:")
        if len(snippet) > 800:
            # stub digest 把正文附在末尾;截头会丢掉信息上界
            snippet = snippet[:400] + "\n...\n" + snippet[-400:]
        if judgment:
            body_bits.append(
                f"基于材料「{title}」的判断成立〔依据:{fid}〕。"
                + (f" 摘要:{snippet}" if snippet else "")
            )
        else:
            body_bits.append(
                f'<a id="{fid}"></a>与「{title}」相关的关键事实〔{sid}〕。'
                + (f"\n\n{snippet}" if snippet else "")
            )
        body_bits.append("")
    # 判断层应能看到上游 understanding 路径名(测试依赖)
    if judgment and "understanding.md" in context:
        body_bits.append("上游 understanding.md 已作为事实层输入。")
        body_bits.append("")
    if judgment:
        body_bits += [
            "## 依据事实索引",
            "",
            "| 锚点 | 来源 |",
            "|---|---|",
        ]
        for sid, title, path in catalog:
            core = sid[2:]
            body_bits.append(f"| F-{core}-01 | {sid} · {title} |")
        body_bits += [
            "",
            "## 来源索引",
            "",
            "| ID | 材料 | 可核对来源 |",
            "|---|---|---|",
        ]
        for sid, title, path in catalog:
            body_bits.append(f"| {sid} | {title} | [digest]({path}) |")
    else:
        body_bits += [
            "## 来源索引",
            "",
            "| ID | 材料 | 可核对来源 |",
            "|---|---|---|",
        ]
        for sid, title, path in catalog:
            body_bits.append(f"| {sid} | {title} | [digest]({path}) |")
    return "\n".join(body_bits) + "\n"


def _stub_required_bodies(context: str, artifact_dir: Path | None = None) -> list[str]:
    """#153:stub 从材料目录「必读」行读取文件正文(工作集 required/<相对路径> 或绝对路径)。"""
    bodies: list[str] = []
    for line in context.splitlines():
        if "| 必读 |" not in line:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 6:
            continue
        loc = cells[4]
        candidates: list[Path] = []
        if artifact_dir is not None:
            candidates.append(artifact_dir / loc)
        p = Path(loc)
        candidates.append(p)
        for c in candidates:
            if c.is_file():
                try:
                    bodies.append(c.read_text())
                except (OSError, UnicodeDecodeError):
                    continue
                break
    return bodies


class StubProvider:
    """确定性 Fake:离线 + 测试。echo 输入 + STUB 标记,只验骨牌链、不被当真。

    digest 会并入材料目录必读文件正文,以便测试断言信息上界仍到达产物。
    #99:compose(doc.md) 产出紧凑溯源结构,以便写盘前校验可通过。
    """

    name = "stub"
    model = "stub"
    supports_read_dirs = True

    def run(self, config: AgentConfig, signal=None) -> AgentResult:
        config.artifact_dir.mkdir(parents=True, exist_ok=True)
        art = config.artifact or "output.md"
        if art == "doc.md":
            content = _stub_compose_document(
                config.persona, config.context, artifact_dir=config.artifact_dir
            )
        elif art == "candidates.yaml":
            # 候选提取需要模型语义；离线 stub 保守地不提出任何候选。
            content = "[]\n"
        else:
            seed = f"{config.persona}\n{config.context}"
            digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8]
            bodies = _stub_required_bodies(config.context, config.artifact_dir)
            extra = ("\n\n" + "\n\n".join(bodies)) if bodies else ""
            content = (
                f"⚠️ STUB OUTPUT [{digest}]\n\n"
                f"{config.persona.strip()}\n\n{config.context.strip()}"
                f"{extra}"
            )
        (config.artifact_dir / art).write_text(content)
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
    section = (tomllib.loads(path.read_text()).get("provider") or {}).get(
        "openai"
    ) or {}

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


def resolve_codex_provider_config() -> dict[str, str]:
    """解析 machine-local Codex CLI 约束。未配置则空串,调用时不传 -m。"""
    path = _config_path()
    section: dict = {}
    if path.is_file():
        section = (tomllib.loads(path.read_text()).get("provider") or {}).get(
            "codex"
        ) or {}

    def _value(key: str) -> str:
        env_name = str(section.get(f"{key}_env") or "").strip()
        if env_name:
            env_value = os.environ.get(env_name)
            if env_value:
                return env_value.strip()
        return str(section.get(key) or "").strip()

    return {
        "model": _value("model"),
        "reasoning_effort": _value("reasoning_effort"),
    }


def _codex_provider() -> CodexProvider:
    cfg = resolve_codex_provider_config()
    return CodexProvider(
        model=cfg["model"],
        reasoning_effort=cfg["reasoning_effort"],
    )


def _reject_unsupported_read(config: AgentConfig, name: str) -> None:
    """#153:无授读能力的 provider 不得把全文倾倒回 prompt,直接失败。"""
    if config.read_dirs:
        raise RuntimeError(f"{name} 不支持授读(read_dirs),无法按目录引用运行")


class OpenAICompatibleProvider:
    """OpenAI-compatible Chat Completions endpoint provider。

    这是薄 LLM adapter,不是工具型 agent:它只把 persona/context 发给 endpoint,
    再把最终文本写回 artifact。
    """

    name = "openai"
    supports_read_dirs = False

    def __init__(self, *, base_url: str, api_key: str, model: str, client=None) -> None:
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
        _reject_unsupported_read(config, self.name)
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


def resolve_cli_timeout(timeout: int | None) -> int:
    """#105:None → 默认超时;显式值原样使用(含测试用短超时)。"""
    if timeout is None:
        return DEFAULT_CLI_TIMEOUT_S
    return int(timeout)


def _kill_pgid(pgid: int, *, sig: int = signal.SIGTERM) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def kill_active_cli_agents() -> None:
    """终止所有登记中的 CLI agent 进程组(Web cancel / 父进程 SIGTERM 时调用)。"""
    with _cli_pgid_lock:
        pgids = list(_active_cli_pgids)
        _active_cli_pgids.clear()
    for pgid in pgids:
        _kill_pgid(pgid, sig=signal.SIGTERM)
    # 给优雅退出一点时间,再强杀
    if pgids:
        time.sleep(0.15)
    for pgid in pgids:
        _kill_pgid(pgid, sig=signal.SIGKILL)


def _register_cli_pgid(pgid: int) -> None:
    with _cli_pgid_lock:
        _active_cli_pgids.add(pgid)
    _ensure_cli_cleanup_handlers()


def _unregister_cli_pgid(pgid: int) -> None:
    with _cli_pgid_lock:
        _active_cli_pgids.discard(pgid)


def _ensure_cli_cleanup_handlers() -> None:
    """父进程被 Web cancel(killpg) 时先清 CLI 子会话,避免 grok/claude 孤儿。"""
    global _cli_cleanup_handlers_installed
    if _cli_cleanup_handlers_installed:
        return
    _cli_cleanup_handlers_installed = True

    def _wrap(prev):
        def _handler(signum, frame):
            kill_active_cli_agents()
            if callable(prev) and prev not in (signal.SIG_DFL, signal.SIG_IGN):
                prev(signum, frame)
            else:
                signal.signal(signum, signal.SIG_DFL)
                try:
                    os.kill(os.getpid(), signum)
                except ProcessLookupError:
                    pass

        return _handler

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            prev = signal.getsignal(sig)
            signal.signal(sig, _wrap(prev))
        except (ValueError, OSError):
            # 非主线程等无法装 handler 时跳过;仍靠 timeout/finally 清理
            pass
    atexit.register(kill_active_cli_agents)


def _kill_process_group(proc: subprocess.Popen) -> None:
    """终止 start_new_session 子进程组;失败则 terminate/kill 单进程。"""
    if proc.poll() is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, OSError):
        pgid = None
    if pgid is not None:
        _kill_pgid(pgid, sig=signal.SIGTERM)
    else:
        try:
            proc.terminate()
        except ProcessLookupError:
            return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if pgid is not None:
            _kill_pgid(pgid, sig=signal.SIGKILL)
        else:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass


def _default_cli_runner(cmd, args, *, cwd, input, stdout_file=None, timeout=None):
    """跑 CLI agent。#105:默认超时;超时杀进程组并抛 RuntimeError(供 #98 落盘)。

    start_new_session 使 CLI 子树独立 pgid,超时 killpg 清 grok/claude。
    同时登记 pgid:Web cancel 只杀 kairo 会话时,SIGTERM handler 会清这些子会话。
    超时消息写 stderr,供 Web SSE / classify_task 识别为 failed。
    """
    limit = resolve_cli_timeout(timeout)
    # 回答在 stdout(claude)时重定向到文件;codex 用 --output-last-message 自写文件,无需重定向
    out = open(stdout_file, "w") if stdout_file else None
    proc: subprocess.Popen | None = None
    pgid: int | None = None
    try:
        proc = subprocess.Popen(
            [cmd, *args],
            cwd=str(cwd),
            stdin=subprocess.PIPE if input is not None else None,
            # stdout → 文件(JSON 结果);stderr 继承父进程,Web SSE 仍可见 Internal error(#105)
            stdout=out if out is not None else None,
            stderr=None,
            text=True,
            start_new_session=True,
        )
        try:
            pgid = os.getpgid(proc.pid)
            _register_cli_pgid(pgid)
        except (ProcessLookupError, OSError):
            pgid = None
        try:
            proc.communicate(input=input, timeout=limit)
        except subprocess.TimeoutExpired:
            _kill_process_group(proc)
            msg = f"CLI agent timeout after {limit}s: {cmd}"
            # 进入合并 stdout 的 Web 任务流,触发 classify failed(#105 S2)
            print(msg, file=sys.stderr, flush=True)
            raise RuntimeError(msg) from None
    finally:
        if pgid is not None:
            _unregister_cli_pgid(pgid)
        if out:
            out.close()


class ClaudeCodeProvider:
    """驱动 `claude -p` CLI。agent 在 artifact_dir(cwd)里写文件。runner 可注入便于测试。"""

    name = "claude-code"
    supports_read_dirs = True

    def __init__(self, model: str = "opus", runner=None) -> None:
        self.model = model
        self._runner = runner or _default_cli_runner

    def run(self, config: AgentConfig, signal=None) -> AgentResult:
        config.artifact_dir.mkdir(parents=True, exist_ok=True)
        prompt = f"{config.persona}\n\n---\n\n{config.context}"
        (config.artifact_dir / "_prompt.md").write_text(
            prompt
        )  # 内部文件,不计 artifact
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
    supports_read_dirs = True

    def __init__(
        self, model: str = "", reasoning_effort: str = "", runner=None
    ) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort
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
        if self.reasoning_effort.strip():
            args += ["-c", f'model_reasoning_effort="{self.reasoning_effort.strip()}"']
        # Codex sandbox 本就可读绝对路径；--add-dir 会把源目录升级为可写，禁止使用。
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
    """驱动 grok CLI。agent 在 artifact_dir(cwd)里写文件。runner 可注入便于测试。

    #153:Grok 无授读;read_dirs 非空则失败,不回退倾倒全文。
    #145:prompt 走 --prompt-file,不把正文塞进 argv。
    JSON 成功字段为 text;错误为 {"type":"error","message":...},写产物前拦截(#8)。
    """

    name = "grok"
    supports_read_dirs = False

    def __init__(self, model: str = "", runner=None) -> None:
        self.model = model
        self._runner = runner or _default_cli_runner

    def run(self, config: AgentConfig, signal=None) -> AgentResult:
        _reject_unsupported_read(config, self.name)
        config.artifact_dir.mkdir(parents=True, exist_ok=True)
        prompt = f"{config.persona}\n\n---\n\n{config.context}"
        prompt_file = config.artifact_dir / "_prompt.md"
        prompt_file.write_text(prompt)
        stdout_file = config.artifact_dir / "_grok_stdout.json"
        # #145/#126:prompt 走 --prompt-file,不把正文塞进 argv。
        args = ["--prompt-file", "_prompt.md", "--output-format", "json"]
        if self.model.strip():
            args += ["-m", self.model]
        self._runner(
            "grok",
            args,
            cwd=config.artifact_dir,
            input=None,
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


def select_provider(*, require_read_dirs: bool = False):
    """选 backend:强制配置不变;auto 按偏好顺序取首个满足任务能力者。

    auto:Codex → Grok → Claude → OpenAI-compatible → Stub。
    require_read_dirs=True 时跳过不支持材料读取的候选,不做调用失败后的切换。
    """
    if os.environ.get("KAIRO_STUB"):
        return StubProvider()
    explicit = os.environ.get("KAIRO_PROVIDER")
    if explicit:
        if explicit == "openai":
            provider = _openai_provider_from_config()
            if provider is None:
                raise RuntimeError(
                    "KAIRO_PROVIDER=openai 但缺少 provider.openai 配置或 API key"
                )
            return provider
        if explicit == "codex":
            return _codex_provider()
        return _BACKENDS.get(explicit, StubProvider)()

    def eligible(candidate) -> bool:
        return not require_read_dirs or candidate.supports_read_dirs

    if _cli_available("codex"):
        candidate = _codex_provider()
        if eligible(candidate):
            return candidate
    if _cli_available("grok"):
        candidate = GrokProvider()
        if eligible(candidate):
            return candidate
    if _cli_available("claude"):
        candidate = ClaudeCodeProvider()
        if eligible(candidate):
            return candidate
    candidate = _openai_provider_from_config()
    if candidate is not None and eligible(candidate):
        return candidate
    return StubProvider()
