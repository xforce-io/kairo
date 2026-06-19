from kairo.provider import ClaudeProvider, StubProvider, select_provider


def test_stub_complete_is_deterministic_and_marked():
    p = StubProvider()
    out1 = p.complete("分析这段文字:你好")
    out2 = p.complete("分析这段文字:你好")
    assert out1 == out2  # 同输入 → 同输出(确定性)
    assert "STUB" in out1  # 显式标记,不被当真


def test_stub_complete_varies_with_prompt():
    p = StubProvider()
    assert p.complete("AAA") != p.complete("BBB")  # 不同输入 → 不同输出


def test_stub_provider_identity_for_provenance():
    p = StubProvider()
    assert p.name == "stub"
    assert p.model == "stub"


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


def test_claude_provider_calls_sdk_and_extracts_only_text():
    fake = _FakeClient(_Resp([_Block("thinking", ""), _Block("text", "忠实纪要")]))
    p = ClaudeProvider(client=fake)
    out = p.complete("为这条 reference 写纪要")
    assert out == "忠实纪要"  # 只取 text block,丢 thinking
    call = fake.messages.calls[0]
    assert call["model"] == "claude-opus-4-8"
    assert call["messages"] == [{"role": "user", "content": "为这条 reference 写纪要"}]


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
