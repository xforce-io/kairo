from kairo import provider
from kairo.provider import (
    ClaudeCodeProvider,
    CodexProvider,
    GrokProvider,
    OpenAICompatibleProvider,
    StubProvider,
    resolve_codex_provider_config,
    resolve_openai_provider_config,
    select_provider,
)


# ---- provider 选择 ----


def _auto(monkeypatch, *, available=(), openai=None, require_read_dirs=False):
    monkeypatch.delenv("KAIRO_STUB", raising=False)
    monkeypatch.delenv("KAIRO_PROVIDER", raising=False)
    monkeypatch.setattr(provider, "resolve_openai_provider_config", lambda: openai)
    monkeypatch.setattr(provider, "_cli_available", lambda cmd: cmd in available)
    return select_provider(require_read_dirs=require_read_dirs)


def test_select_provider_auto_prefers_codex(monkeypatch):
    selected = _auto(monkeypatch, available={"codex", "grok", "claude"})
    assert isinstance(selected, CodexProvider)


def test_select_provider_auto_non_material_prefers_grok_before_claude(monkeypatch):
    selected = _auto(monkeypatch, available={"grok", "claude"})
    assert isinstance(selected, GrokProvider)


def test_select_provider_auto_materials_skip_grok_for_claude(monkeypatch):
    selected = _auto(monkeypatch, available={"grok", "claude"}, require_read_dirs=True)
    assert isinstance(selected, ClaudeCodeProvider)


def test_select_provider_auto_materials_prefer_codex_over_grok(monkeypatch):
    selected = _auto(
        monkeypatch, available={"codex", "grok", "claude"}, require_read_dirs=True
    )
    assert isinstance(selected, CodexProvider)


def test_select_provider_auto_prefers_claude_over_openai(monkeypatch):
    selected = _auto(
        monkeypatch,
        available={"claude"},
        openai={
            "base_url": "https://llm.example/v1",
            "model": "endpoint-model",
            "api_key": "test-key",
        },
    )
    assert isinstance(selected, ClaudeCodeProvider)


def test_select_provider_auto_materials_prefer_claude_over_openai(monkeypatch):
    selected = _auto(
        monkeypatch,
        available={"claude"},
        openai={
            "base_url": "https://llm.example/v1",
            "model": "endpoint-model",
            "api_key": "test-key",
        },
        require_read_dirs=True,
    )
    assert isinstance(selected, ClaudeCodeProvider)


def test_select_provider_auto_materials_run_to_fold_with_selected_codex(
    tmp_path, monkeypatch
):
    from kairo.engine import run_workspace
    from kairo.workspace import Workspace

    class SelectedCodex(StubProvider):
        name = "codex"
        model = "selected-test"

        def __init__(self, *args, **kwargs):
            super().__init__()

    monkeypatch.setattr(provider, "CodexProvider", SelectedCodex)
    selected = _auto(
        monkeypatch, available={"codex", "grok", "claude"}, require_read_dirs=True
    )
    ws = Workspace.init(tmp_path / "ws")
    source = tmp_path / "source.txt"
    source.write_text("能力选择闭环事实")
    ref_id = ws.add([source])

    assert run_workspace(ws, selected)
    state = ws.read_state()
    digest = state.products[f"references/{ref_id}/digest.md"]
    assert digest.produced_by == {"provider": "codex", "model": "selected-test"}
    assert state.targets["understanding.md"].produced_by == {
        "provider": "codex",
        "model": "selected-test",
    }


def test_select_provider_auto_prefers_grok_over_configured_openai(monkeypatch):
    """只有不支持材料读取的候选时仍保留既有非材料顺序。"""
    selected = _auto(
        monkeypatch,
        available={"grok"},
        openai={
            "base_url": "https://llm.example/v1",
            "model": "endpoint-model",
            "api_key": "test-key",
        },
    )
    assert isinstance(selected, GrokProvider)


def test_select_provider_auto_materials_skip_openai_for_stub(monkeypatch):
    selected = _auto(
        monkeypatch,
        openai={
            "base_url": "https://llm.example/v1",
            "model": "endpoint-model",
            "api_key": "test-key",
        },
        require_read_dirs=True,
    )
    assert isinstance(selected, StubProvider)


def test_select_provider_auto_uses_openai_when_no_cli(monkeypatch):
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
    monkeypatch.setattr(provider, "_cli_available", lambda cmd: False)
    selected = select_provider()
    assert isinstance(selected, OpenAICompatibleProvider)
    assert selected.model == "endpoint-model"


def test_select_provider_auto_falls_back_to_stub_without_cli(monkeypatch):
    monkeypatch.delenv("KAIRO_STUB", raising=False)
    monkeypatch.delenv("KAIRO_PROVIDER", raising=False)
    monkeypatch.setattr(provider, "resolve_openai_provider_config", lambda: None)
    monkeypatch.setattr(provider, "_cli_available", lambda cmd: False)
    assert isinstance(select_provider(), StubProvider)


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


def test_select_provider_explicit_grok(monkeypatch):
    monkeypatch.delenv("KAIRO_STUB", raising=False)
    monkeypatch.setenv("KAIRO_PROVIDER", "grok")
    assert isinstance(select_provider(require_read_dirs=True), GrokProvider)


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
    assert isinstance(select_provider(require_read_dirs=True), OpenAICompatibleProvider)


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


def test_resolve_codex_provider_config_from_config_toml(tmp_path, monkeypatch):
    cfg = tmp_path / "kairo" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text(
        """
[provider.codex]
model = "gpt-5.6-terra"
reasoning_effort = "high"
""".strip()
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert resolve_codex_provider_config() == {
        "model": "gpt-5.6-terra",
        "reasoning_effort": "high",
    }


def test_resolve_codex_provider_config_missing_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert resolve_codex_provider_config() == {"model": "", "reasoning_effort": ""}


def test_select_provider_codex_uses_configured_model(tmp_path, monkeypatch):
    cfg = tmp_path / "kairo" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text('[provider.codex]\nmodel = "gpt-5.6-terra"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    selected = _auto(monkeypatch, available={"codex"})
    assert isinstance(selected, CodexProvider)
    assert selected.model == "gpt-5.6-terra"


def test_select_provider_explicit_codex_uses_configured_model(tmp_path, monkeypatch):
    cfg = tmp_path / "kairo" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text('[provider.codex]\nmodel = "gpt-5.6-terra"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("KAIRO_STUB", raising=False)
    monkeypatch.setenv("KAIRO_PROVIDER", "codex")
    selected = select_provider()
    assert isinstance(selected, CodexProvider)
    assert selected.model == "gpt-5.6-terra"


def test_select_provider_codex_without_model_stays_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    selected = _auto(monkeypatch, available={"codex"})
    assert isinstance(selected, CodexProvider)
    assert selected.model == ""
