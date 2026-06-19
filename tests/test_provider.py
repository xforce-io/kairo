from kairo.provider import (
    AgentConfig,
    ClaudeCodeProvider,
    ClaudeProvider,
    CodexProvider,
    StubProvider,
    select_provider,
)


# ---- ClaudeProvider(注入式 client,不触真 API)----


class _Block:
    def __init__(self, type, text=""):
        self.type = type
        self.text = text


class _Resp:
    def __init__(self, content):
        self.content = content


class _Messages:
    def __init__(self, resp):
        self._resp = resp
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._resp


class _FakeClient:
    def __init__(self, resp):
        self.messages = _Messages(resp)


def test_claude_provider_run_splits_system_user_and_extracts_text(tmp_path):
    fake = _FakeClient(_Resp([_Block("thinking", ""), _Block("text", "忠实纪要")]))
    p = ClaudeProvider(client=fake)
    res = p.run(
        AgentConfig(
            persona="你是纪要员",
            context="正文内容",
            artifact_dir=tmp_path,
            model="claude-opus-4-8",
            artifact="digest.md",
        )
    )
    out = tmp_path / "digest.md"
    assert out.read_text() == "忠实纪要"  # 只取 text block,丢 thinking
    assert out in res.artifacts
    call = fake.messages.calls[0]
    assert call["system"] == "你是纪要员"  # persona → system(§5 分离)
    assert call["messages"] == [{"role": "user", "content": "正文内容"}]  # context → user
    assert call["model"] == "claude-opus-4-8"


def test_claude_provider_identity_for_provenance():
    p = ClaudeProvider(client=_FakeClient(_Resp([])))
    assert p.name == "claude"
    assert p.model == "claude-opus-4-8"


# ---- provider 选择 ----


def test_select_provider_uses_stub_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert isinstance(select_provider(), StubProvider)


def test_select_provider_uses_claude_with_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("KAIRO_STUB", raising=False)
    assert isinstance(select_provider(), ClaudeProvider)


def test_select_provider_forced_stub_overrides_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("KAIRO_STUB", "1")
    assert isinstance(select_provider(), StubProvider)


def test_select_provider_explicit_claude_code(monkeypatch):
    monkeypatch.delenv("KAIRO_STUB", raising=False)
    monkeypatch.setenv("KAIRO_PROVIDER", "claude-code")
    assert isinstance(select_provider(), ClaudeCodeProvider)


def test_select_provider_explicit_codex(monkeypatch):
    monkeypatch.delenv("KAIRO_STUB", raising=False)
    monkeypatch.setenv("KAIRO_PROVIDER", "codex")
    assert isinstance(select_provider(), CodexProvider)


def test_select_provider_kairo_stub_overrides_explicit_provider(monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    monkeypatch.setenv("KAIRO_PROVIDER", "codex")
    # KAIRO_STUB 最高优先(测试隔离保证,永不真跑 agent)
    assert isinstance(select_provider(), StubProvider)
