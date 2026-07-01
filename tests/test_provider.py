from kairo import provider
from kairo.provider import (
    ClaudeCodeProvider,
    CodexProvider,
    OpenAICompatibleProvider,
    StubProvider,
    resolve_openai_provider_config,
    select_provider,
)


# ---- provider 选择(只 subscription,无 API 模式)----


def test_select_provider_auto_uses_claude_code_when_cli_available(monkeypatch):
    monkeypatch.delenv("KAIRO_STUB", raising=False)
    monkeypatch.delenv("KAIRO_PROVIDER", raising=False)
    monkeypatch.setattr(provider, "resolve_openai_provider_config", lambda: None)
    monkeypatch.setattr(provider, "_cli_available", lambda cmd: True)
    assert isinstance(select_provider(), ClaudeCodeProvider)  # subscription 优先


def test_select_provider_auto_prefers_configured_openai_endpoint(monkeypatch):
    monkeypatch.delenv("KAIRO_STUB", raising=False)
    monkeypatch.delenv("KAIRO_PROVIDER", raising=False)
    monkeypatch.setattr(
        provider,
        "resolve_openai_provider_config",
        lambda: {
            "base_url": "https://llm.example/v1",
            "model": "endpoint-model",
            "api_key": "test-key",
        },
    )
    monkeypatch.setattr(provider, "_cli_available", lambda cmd: True)
    selected = select_provider()
    assert isinstance(selected, OpenAICompatibleProvider)
    assert selected.model == "endpoint-model"


def test_select_provider_auto_falls_back_to_stub_without_cli(monkeypatch):
    monkeypatch.delenv("KAIRO_STUB", raising=False)
    monkeypatch.delenv("KAIRO_PROVIDER", raising=False)
    monkeypatch.setattr(provider, "resolve_openai_provider_config", lambda: None)
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


def test_select_provider_explicit_openai(monkeypatch):
    monkeypatch.delenv("KAIRO_STUB", raising=False)
    monkeypatch.setenv("KAIRO_PROVIDER", "openai")
    monkeypatch.setattr(
        provider,
        "resolve_openai_provider_config",
        lambda: {
            "base_url": "https://llm.example/v1",
            "model": "endpoint-model",
            "api_key": "test-key",
        },
    )
    assert isinstance(select_provider(), OpenAICompatibleProvider)


def test_select_provider_kairo_stub_overrides_explicit_provider(monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    monkeypatch.setenv("KAIRO_PROVIDER", "codex")
    assert isinstance(select_provider(), StubProvider)


def test_resolve_openai_provider_config_from_config_toml(tmp_path, monkeypatch):
    cfg = tmp_path / "kairo" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text(
        """
[provider.openai]
base_url = "https://llm.example/v1"
model = "endpoint-model"
api_key_env = "KAIRO_TEST_LLM_KEY"
""".strip()
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("KAIRO_TEST_LLM_KEY", "secret")
    resolved = resolve_openai_provider_config()
    assert resolved == {
        "base_url": "https://llm.example/v1",
        "model": "endpoint-model",
        "api_key": "secret",
    }


def test_resolve_openai_provider_config_supports_base_url_and_model_env(
    tmp_path, monkeypatch
):
    cfg = tmp_path / "kairo" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text(
        """
[provider.openai]
base_url_env = "KAIRO_TEST_LLM_BASE"
model_env = "KAIRO_TEST_LLM_MODEL"
api_key_env = "KAIRO_TEST_LLM_KEY"
""".strip()
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("KAIRO_TEST_LLM_BASE", "https://llm.example/v1")
    monkeypatch.setenv("KAIRO_TEST_LLM_MODEL", "endpoint-model")
    monkeypatch.setenv("KAIRO_TEST_LLM_KEY", "secret")
    resolved = resolve_openai_provider_config()
    assert resolved == {
        "base_url": "https://llm.example/v1",
        "model": "endpoint-model",
        "api_key": "secret",
    }


def test_resolve_openai_provider_config_missing_key_returns_none(tmp_path, monkeypatch):
    cfg = tmp_path / "kairo" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text(
        """
[provider.openai]
base_url = "https://llm.example/v1"
model = "endpoint-model"
api_key_env = "KAIRO_TEST_LLM_KEY"
""".strip()
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("KAIRO_TEST_LLM_KEY", raising=False)
    assert resolve_openai_provider_config() is None
