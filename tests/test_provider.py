from kairo import provider
from kairo.provider import (
    ClaudeCodeProvider,
    CodexProvider,
    StubProvider,
    select_provider,
)


# ---- provider 选择(只 subscription,无 API 模式)----


def test_select_provider_auto_uses_claude_code_when_cli_available(monkeypatch):
    monkeypatch.delenv("KAIRO_STUB", raising=False)
    monkeypatch.delenv("KAIRO_PROVIDER", raising=False)
    monkeypatch.setattr(provider, "_cli_available", lambda cmd: True)
    assert isinstance(select_provider(), ClaudeCodeProvider)  # subscription 优先


def test_select_provider_auto_falls_back_to_stub_without_cli(monkeypatch):
    monkeypatch.delenv("KAIRO_STUB", raising=False)
    monkeypatch.delenv("KAIRO_PROVIDER", raising=False)
    monkeypatch.setattr(provider, "_cli_available", lambda cmd: False)
    assert isinstance(select_provider(), StubProvider)  # 无 claude CLI → stub


def test_select_provider_forced_stub_is_highest(monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    monkeypatch.setattr(provider, "_cli_available", lambda cmd: True)
    assert isinstance(select_provider(), StubProvider)  # KAIRO_STUB 压过一切


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
    assert isinstance(select_provider(), StubProvider)
