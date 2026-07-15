"""AgentProvider 抽象(#4):run(config) → artifacts,靠写文件通信。

StubProvider 是确定性 Fake:守可测性 —— 把输入 echo 进产物 + STUB 标记,
让 rules/engine 的「正文流过产物」「收敛幂等」断言在新接口下继续成立。
"""

import json
from pathlib import Path

import pytest

from kairo.provider import (
    AgentConfig,
    AgentResult,
    ClaudeCodeProvider,
    CodexProvider,
    GrokProvider,
    OpenAICompatibleProvider,
    StubProvider,
)


def _cfg(artifact_dir, persona="写一份纪要", context="正文", artifact="out.md", model="stub"):
    return AgentConfig(
        persona=persona,
        context=context,
        artifact_dir=Path(artifact_dir),
        model=model,
        artifact=artifact,
    )


def test_stub_run_writes_artifact_carrying_context(tmp_path):
    res = StubProvider().run(_cfg(tmp_path, context="正文ABC", artifact="digest.md"))
    out = tmp_path / "digest.md"
    assert out.is_file()
    txt = out.read_text()
    assert "正文ABC" in txt  # 输入可见于产物(rules 溯源/收敛依赖)
    assert "STUB" in txt  # 显式标记,不被当真
    assert isinstance(res, AgentResult)
    assert out in res.artifacts


def test_stub_run_is_deterministic_regardless_of_artifact_dir(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    StubProvider().run(_cfg(a, persona="P", context="同样输入", artifact="o.md"))
    StubProvider().run(_cfg(b, persona="P", context="同样输入", artifact="o.md"))
    # 同 (persona, context) → 同产物;不得依赖 artifact_dir 路径(否则破坏 idempotent)
    assert (a / "o.md").read_text() == (b / "o.md").read_text()


def test_stub_run_varies_with_input(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    StubProvider().run(_cfg(a, context="AAA", artifact="o.md"))
    StubProvider().run(_cfg(b, context="BBB", artifact="o.md"))
    assert (a / "o.md").read_text() != (b / "o.md").read_text()


def test_stub_run_artifacts_exclude_internal_files(tmp_path):
    # '_'/'.' 前缀是内部通信文件,不计入 artifacts(monastery 约定)
    (tmp_path / "_prompt.md").write_text("internal")
    (tmp_path / ".hidden").write_text("hidden")
    res = StubProvider().run(_cfg(tmp_path, artifact="out.md"))
    names = {Path(p).name for p in res.artifacts}
    assert "out.md" in names
    assert "_prompt.md" not in names
    assert ".hidden" not in names


def test_stub_provider_identity_for_provenance():
    p = StubProvider()
    assert p.name == "stub"
    assert p.model == "stub"


# ---- ClaudeCodeProvider(driving `claude -p`,注入 runner 不真跑 CLI)----


def test_claude_code_provider_invokes_cli_and_reads_stdout_result(tmp_path):
    calls = []

    def fake_runner(cmd, args, *, cwd, input, stdout_file, timeout=None):
        calls.append((cmd, args, input))
        # claude -p 把回答写 stdout(json);runner 重定向到 stdout_file
        Path(stdout_file).write_text(json.dumps({"result": "AGENT 纪要"}))

    p = ClaudeCodeProvider(model="opus", runner=fake_runner)
    res = p.run(
        AgentConfig(
            persona="你是X",
            context="材料Y",
            artifact_dir=tmp_path,
            model="opus",
            artifact="digest.md",
        )
    )
    cmd, args, sent = calls[0]
    assert cmd == "claude"
    assert "-p" in args
    assert "你是X" in sent and "材料Y" in sent  # persona + context 进 prompt
    out = tmp_path / "digest.md"
    assert out in res.artifacts
    assert "AGENT 纪要" in out.read_text()  # stdout result → artifact
    assert res.result_text and "AGENT 纪要" in res.result_text
    # 内部文件不计入 artifacts
    names = {Path(a).name for a in res.artifacts}
    assert "_prompt.md" not in names and "_claude_stdout.json" not in names


def test_claude_code_provider_passes_read_dirs_as_add_dir(tmp_path):
    """#13 v2:read_dirs(corpus 只读参考层)→ --add-dir,授 agent 只读访问。"""
    calls = []

    def fake_runner(cmd, args, *, cwd, input, stdout_file, timeout=None):
        calls.append((cmd, args, input))
        Path(stdout_file).write_text(json.dumps({"result": "OK"}))

    ref = tmp_path / "corpus_dir"
    ref.mkdir()
    p = ClaudeCodeProvider(model="opus", runner=fake_runner)
    p.run(
        AgentConfig(
            persona="P",
            context="C",
            artifact_dir=tmp_path,
            model="opus",
            artifact="doc.md",
            read_dirs=[ref],
        )
    )
    _, args, _ = calls[0]
    assert "--add-dir" in args
    assert str(ref) in args


def test_claude_code_provider_allows_read_tools_for_corpus(tmp_path):
    """#13 v2:有 read_dirs 时预授只读工具(Read/Glob/Grep),非交互下 agent 才能读 corpus。"""
    calls = []

    def fake_runner(cmd, args, *, cwd, input, stdout_file, timeout=None):
        calls.append(args)
        Path(stdout_file).write_text(json.dumps({"result": "OK"}))

    ref = tmp_path / "c"
    ref.mkdir()
    ClaudeCodeProvider(model="opus", runner=fake_runner).run(
        AgentConfig(
            persona="P",
            context="C",
            artifact_dir=tmp_path,
            model="opus",
            artifact="doc.md",
            read_dirs=[ref],
        )
    )
    args = calls[0]
    assert "--allowedTools" in args
    assert "Read" in args and "Glob" in args and "Grep" in args


def test_claude_code_provider_no_allowed_tools_without_read_dirs(tmp_path):
    """无 read_dirs(纯 stream)时不预授工具,保持收紧默认。"""
    calls = []

    def fake_runner(cmd, args, *, cwd, input, stdout_file, timeout=None):
        calls.append(args)
        Path(stdout_file).write_text(json.dumps({"result": "OK"}))

    ClaudeCodeProvider(model="opus", runner=fake_runner).run(
        AgentConfig(
            persona="P", context="C", artifact_dir=tmp_path, model="opus", artifact="d.md"
        )
    )
    assert "--allowedTools" not in calls[0]


def test_claude_code_provider_identity():
    p = ClaudeCodeProvider(model="opus")
    assert p.name == "claude-code"
    assert p.model == "opus"


# ---- CodexProvider(driving `codex exec`,注入 runner)----


def test_codex_provider_invokes_cli_and_reads_last_message(tmp_path):
    calls = []

    def fake_runner(cmd, args, *, cwd, input, stdout_file=None, timeout=None):
        calls.append((cmd, args, input))
        # codex 把最终消息写到 --output-last-message 指定的文件
        idx = args.index("--output-last-message")
        Path(args[idx + 1]).write_text("CODEX 纪要")

    p = CodexProvider(model="gpt-5", runner=fake_runner)
    res = p.run(
        AgentConfig(
            persona="角色A",
            context="任务B",
            artifact_dir=tmp_path,
            model="gpt-5",
            artifact="out.md",
        )
    )
    cmd, args, sent = calls[0]
    assert cmd == "codex"
    assert "exec" in args
    assert "--output-last-message" in args
    assert "-m" in args and "gpt-5" in args  # 模型透传
    assert "角色A" in sent and "任务B" in sent
    out = tmp_path / "out.md"
    assert out in res.artifacts
    assert "CODEX 纪要" in out.read_text()
    assert res.result_text and "CODEX 纪要" in res.result_text


def test_codex_provider_identity():
    assert CodexProvider().name == "codex"


# ---- GrokProvider(driving `grok -p`,注入 runner)----


def test_grok_provider_invokes_cli_and_reads_stdout_text(tmp_path):
    calls = []

    def fake_runner(cmd, args, *, cwd, input, stdout_file=None, timeout=None):
        calls.append((cmd, args, input, timeout))
        Path(stdout_file).write_text(
            json.dumps({"text": "GROK 纪要", "stopReason": "EndTurn"})
        )

    p = GrokProvider(model="grok-4.5", runner=fake_runner)
    res = p.run(
        AgentConfig(
            persona="你是X",
            context="材料Y",
            artifact_dir=tmp_path,
            model="grok-4.5",
            artifact="digest.md",
            timeout_s=30,
        )
    )
    cmd, args, sent, timeout = calls[0]
    assert cmd == "grok"
    assert "-p" in args
    assert "--output-format" in args and "json" in args
    assert "-m" in args and "grok-4.5" in args
    assert timeout == 30
    # prompt 进 -p 参数或 input（与 runner 签名对齐）
    prompt_blob = " ".join(args) + "\n" + (sent or "")
    assert "你是X" in prompt_blob and "材料Y" in prompt_blob
    out = tmp_path / "digest.md"
    assert out in res.artifacts
    assert out.read_text() == "GROK 纪要"
    assert res.result_text == "GROK 纪要"
    names = {Path(a).name for a in res.artifacts}
    assert "_prompt.md" not in names and "_grok_stdout.json" not in names


def test_grok_provider_omits_model_flag_when_empty(tmp_path):
    calls = []

    def fake_runner(cmd, args, *, cwd, input, stdout_file=None, timeout=None):
        calls.append(args)
        Path(stdout_file).write_text(json.dumps({"text": "OK"}))

    GrokProvider(model="", runner=fake_runner).run(
        AgentConfig(
            persona="P",
            context="C",
            artifact_dir=tmp_path,
            model="",
            artifact="out.md",
        )
    )
    assert "-m" not in calls[0]


def test_grok_provider_ignores_read_dirs(tmp_path):
    """#61:Grok 无 --add-dir；有 read_dirs 时不伪造授权旗标。"""
    calls = []

    def fake_runner(cmd, args, *, cwd, input, stdout_file=None, timeout=None):
        calls.append(args)
        Path(stdout_file).write_text(json.dumps({"text": "OK"}))

    ref = tmp_path / "corpus"
    ref.mkdir()
    GrokProvider(runner=fake_runner).run(
        AgentConfig(
            persona="P",
            context="C",
            artifact_dir=tmp_path,
            model="",
            artifact="out.md",
            read_dirs=[ref],
        )
    )
    args = calls[0]
    assert "--add-dir" not in args
    assert "--allowedTools" not in args
    assert str(ref) not in args


def test_grok_provider_identity():
    p = GrokProvider(model="")
    assert p.name == "grok"
    assert p.model == ""


def test_grok_provider_raises_on_error_type(tmp_path):
    def fake_runner(cmd, args, *, cwd, input, stdout_file=None, timeout=None):
        Path(stdout_file).write_text(
            json.dumps({"type": "error", "message": "Couldn't set model"})
        )

    p = GrokProvider(runner=fake_runner)
    with pytest.raises(RuntimeError):
        p.run(
            AgentConfig(
                persona="X",
                context="Y",
                artifact_dir=tmp_path,
                model="",
                artifact="out.md",
            )
        )
    assert not (tmp_path / "out.md").exists()


def test_grok_provider_raises_on_missing_text(tmp_path):
    def fake_runner(cmd, args, *, cwd, input, stdout_file=None, timeout=None):
        Path(stdout_file).write_text(json.dumps({"stopReason": "EndTurn"}))

    with pytest.raises(RuntimeError):
        GrokProvider(runner=fake_runner).run(
            AgentConfig(
                persona="X",
                context="Y",
                artifact_dir=tmp_path,
                model="",
                artifact="out.md",
            )
        )
    assert not (tmp_path / "out.md").exists()


def test_grok_provider_raises_when_no_stdout(tmp_path):
    def fake_runner(cmd, args, *, cwd, input, stdout_file=None, timeout=None):
        pass

    with pytest.raises(RuntimeError):
        GrokProvider(runner=fake_runner).run(
            AgentConfig(
                persona="X",
                context="Y",
                artifact_dir=tmp_path,
                model="",
                artifact="out.md",
            )
        )
    assert not (tmp_path / "out.md").exists()


# ---- OpenAICompatibleProvider(configured endpoint via official SDK seam)----


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeCompletion:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, calls):
        self.calls = calls

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeCompletion("endpoint 纪要")


class _FakeChat:
    def __init__(self, calls):
        self.completions = _FakeCompletions(calls)


class _FakeOpenAIClient:
    def __init__(self, calls):
        self.chat = _FakeChat(calls)


def test_openai_compatible_provider_invokes_chat_completion_and_writes_artifact(tmp_path):
    calls = []
    p = OpenAICompatibleProvider(
        base_url="https://llm.example/v1",
        api_key="secret",
        model="endpoint-model",
        client=_FakeOpenAIClient(calls),
    )
    res = p.run(
        AgentConfig(
            persona="你是X",
            context="材料Y",
            artifact_dir=tmp_path,
            model="endpoint-model",
            artifact="digest.md",
            timeout_s=12,
        )
    )
    assert calls == [
        {
            "model": "endpoint-model",
            "messages": [
                {"role": "system", "content": "你是X"},
                {"role": "user", "content": "材料Y"},
            ],
            "timeout": 12,
        }
    ]
    out = tmp_path / "digest.md"
    assert out in res.artifacts
    assert out.read_text() == "endpoint 纪要"
    assert res.result_text == "endpoint 纪要"


def test_openai_compatible_provider_identity():
    p = OpenAICompatibleProvider(
        base_url="https://llm.example/v1",
        api_key="secret",
        model="endpoint-model",
        client=_FakeOpenAIClient([]),
    )
    assert p.name == "openai"
    assert p.model == "endpoint-model"


def test_openai_compatible_provider_raises_on_empty_response(tmp_path):
    class _EmptyCompletions:
        def create(self, **kwargs):
            return _FakeCompletion("")

    class _EmptyChat:
        completions = _EmptyCompletions()

    class _EmptyClient:
        chat = _EmptyChat()

    p = OpenAICompatibleProvider(
        base_url="https://llm.example/v1",
        api_key="secret",
        model="endpoint-model",
        client=_EmptyClient(),
    )
    with pytest.raises(RuntimeError):
        p.run(
            AgentConfig(
                persona="P",
                context="C",
                artifact_dir=tmp_path,
                model="endpoint-model",
                artifact="out.md",
            )
        )
    assert not (tmp_path / "out.md").exists()


# ---- #8:错误响应须抛错,不写坏产物、不记账 ----


def test_claude_code_provider_raises_on_error_response(tmp_path):
    """claude -p 报错(is_error=true,result 含错误文本)→ 抛错,不写产物。"""

    def fake_runner(cmd, args, *, cwd, input, stdout_file, timeout=None):
        # 连接中断时 claude -p 把错误塞进 result 且 is_error=true
        Path(stdout_file).write_text(
            json.dumps(
                {
                    "is_error": True,
                    "subtype": "error_during_execution",
                    "result": "API Error: Connection closed mid-response.",
                }
            )
        )

    p = ClaudeCodeProvider(model="opus", runner=fake_runner)
    with pytest.raises(RuntimeError):
        p.run(
            AgentConfig(
                persona="X",
                context="Y",
                artifact_dir=tmp_path,
                model="opus",
                artifact="out.md",
            )
        )
    assert not (tmp_path / "out.md").exists()  # 不写坏产物


def test_codex_provider_raises_when_no_last_message(tmp_path):
    """codex 失败(未产出 last-message)→ 抛错,不写产物。"""

    def fake_runner(cmd, args, *, cwd, input, stdout_file=None, timeout=None):
        pass  # 模拟 codex 失败,不写 last-message

    p = CodexProvider(model="gpt-5", runner=fake_runner)
    with pytest.raises(RuntimeError):
        p.run(
            AgentConfig(
                persona="X",
                context="Y",
                artifact_dir=tmp_path,
                model="gpt-5",
                artifact="out.md",
            )
        )
    assert not (tmp_path / "out.md").exists()
