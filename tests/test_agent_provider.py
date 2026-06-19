"""AgentProvider 抽象(#4):run(config) → artifacts,靠写文件通信。

StubProvider 是确定性 Fake:守可测性 —— 把输入 echo 进产物 + STUB 标记,
让 rules/engine 的「正文流过产物」「收敛幂等」断言在新接口下继续成立。
"""

from pathlib import Path

from kairo.provider import (
    AgentConfig,
    AgentResult,
    ClaudeCodeProvider,
    CodexProvider,
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


def test_claude_code_provider_invokes_cli_and_collects_artifacts(tmp_path):
    calls = []

    def fake_runner(cmd, args, *, cwd, input, timeout=None):
        calls.append((cmd, args, input))
        # 模拟 agent 在 cwd 写产物
        (Path(cwd) / "digest.md").write_text(f"AGENT OUTPUT\n{input}")

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
    assert "AGENT OUTPUT" in out.read_text()
    # 内部 _prompt.md 不计入 artifacts
    assert all(Path(a).name != "_prompt.md" for a in res.artifacts)


def test_claude_code_provider_identity():
    p = ClaudeCodeProvider(model="opus")
    assert p.name == "claude-code"
    assert p.model == "opus"


# ---- CodexProvider(driving `codex exec`,注入 runner)----


def test_codex_provider_invokes_cli_and_collects_artifacts(tmp_path):
    calls = []

    def fake_runner(cmd, args, *, cwd, input, timeout=None):
        calls.append((cmd, args, input))
        (Path(cwd) / "out.md").write_text(f"CODEX OUTPUT\n{input}")

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
    assert "-m" in args and "gpt-5" in args  # 模型透传
    assert "角色A" in sent and "任务B" in sent
    assert (tmp_path / "out.md") in res.artifacts


def test_codex_provider_identity():
    assert CodexProvider().name == "codex"
