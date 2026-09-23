"""#425: machine notes pin grok + reasoning effort low and a short timeout_cap."""
from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from kairo.cli import app
from kairo.generated_note import (
    DEFAULT_NOTE_TIMEOUT_CAP_S,
    resolve_note_timeout_cap,
    select_note_provider,
)
from kairo.provider import (
    CodexProvider,
    GrokProvider,
    StubProvider,
    resolve_agent_timeout_s,
    resolve_cli_timeout,
    select_provider,
)
from kairo.workspace import Workspace
from test_agent_provider import _grok_ndjson
from test_run_ref_generated_note_423 import _NoteProvider, _show

runner = CliRunner()


def _ws(tmp_path, monkeypatch):
    serve = tmp_path / "root"
    serve.mkdir()
    ws = Workspace.init(serve / "topic", topic="单条")
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(serve))
    monkeypatch.chdir(ws.root)
    return serve, ws


def _add_text(ws, tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    return ws.add([path])


def _write_agent_config(tmp_path, monkeypatch, body: str):
    cfg = tmp_path / "kairo" / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(body)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return cfg


def _bind_digest(monkeypatch, provider):
    monkeypatch.delenv("KAIRO_STUB", raising=False)
    monkeypatch.setattr("kairo.cli.select_provider", lambda **_kw: provider)


def test_default_note_timeout_cap_is_at_most_120():
    assert DEFAULT_NOTE_TIMEOUT_CAP_S <= 120
    assert resolve_note_timeout_cap() == DEFAULT_NOTE_TIMEOUT_CAP_S


def test_select_note_provider_pins_grok_low_not_auto_or_env(monkeypatch):
    monkeypatch.delenv("KAIRO_STUB", raising=False)
    monkeypatch.setenv("KAIRO_PROVIDER", "codex")
    monkeypatch.setattr(
        "kairo.provider._cli_available", lambda cmd: cmd in {"codex", "grok"}
    )
    digest = select_provider(require_read_dirs=True)
    assert isinstance(digest, CodexProvider)
    note = select_note_provider()
    assert isinstance(note, GrokProvider)
    assert note.reasoning_effort == "low"
    assert note.name == "grok"


def test_select_note_provider_keeps_stub_override(monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    assert isinstance(select_note_provider(), StubProvider)


def test_note_timeout_cap_ignores_agent_timeout_s(tmp_path, monkeypatch):
    _write_agent_config(tmp_path, monkeypatch, "[agent]\ntimeout_s = 1800\n")
    assert resolve_agent_timeout_s() == 1800
    assert resolve_cli_timeout(None) == 1800
    assert resolve_note_timeout_cap() == DEFAULT_NOTE_TIMEOUT_CAP_S


def test_note_timeout_cap_is_configurable(tmp_path, monkeypatch):
    _write_agent_config(
        tmp_path, monkeypatch, "[agent]\ntimeout_s = 1800\nnote_timeout_s = 60\n"
    )
    assert resolve_agent_timeout_s() == 1800
    assert resolve_note_timeout_cap() == 60


def test_s1_run_ref_note_argv_is_grok_low_not_digest_codex(tmp_path, monkeypatch):
    _serve, ws = _ws(tmp_path, monkeypatch)
    ref_id = _add_text(ws, tmp_path, "a.txt", "纪要材料")
    digest_provider = _NoteProvider("should-not-write-note")
    seen: dict = {}

    def fake_runner(cmd, args, *, cwd, input, stdout_file=None, timeout=None):
        seen["cmd"] = cmd
        seen["args"] = list(args)
        seen["timeout"] = timeout
        Path(stdout_file).write_text(_grok_ndjson("机器短记"))

    _bind_digest(monkeypatch, digest_provider)
    monkeypatch.setenv("KAIRO_PROVIDER", "codex")
    monkeypatch.setattr("kairo.provider._default_cli_runner", fake_runner)

    result = runner.invoke(app, ["run", "--ref", ref_id])
    assert result.exit_code == 0, result.output + result.stderr
    assert seen["cmd"] == "grok"
    args = seen["args"]
    assert args[args.index("--reasoning-effort") + 1] == "low"
    assert digest_provider.contexts == []
    shown = _show(ref_id)
    assert shown["count"] == 1
    assert shown["items"][0]["type"] == "generated"
    assert shown["items"][0]["author"] == "machine"
    assert shown["items"][0]["content"] == "机器短记"


def test_s2_note_uses_default_timeout_cap_not_agent_timeout(tmp_path, monkeypatch):
    _write_agent_config(tmp_path, monkeypatch, "[agent]\ntimeout_s = 1800\n")
    _serve, ws = _ws(tmp_path, monkeypatch)
    ref_id = _add_text(ws, tmp_path, "a.txt", "纪要材料")
    digest_provider = _NoteProvider("digest-only")
    seen: dict = {}

    def fake_runner(cmd, args, *, cwd, input, stdout_file=None, timeout=None):
        seen["cmd"] = cmd
        seen["timeout"] = timeout
        Path(stdout_file).write_text(_grok_ndjson("短超时笔记"))

    _bind_digest(monkeypatch, digest_provider)
    monkeypatch.setattr("kairo.provider._default_cli_runner", fake_runner)

    result = runner.invoke(app, ["run", "--ref", ref_id])
    assert result.exit_code == 0, result.output + result.stderr
    assert seen["cmd"] == "grok"
    assert seen["timeout"] == DEFAULT_NOTE_TIMEOUT_CAP_S
    assert seen["timeout"] <= 120
    assert resolve_agent_timeout_s() == 1800
    assert resolve_cli_timeout(None) == 1800
    assert _show(ref_id)["count"] == 1


def test_s2_note_timeout_is_visible_and_does_not_fail_run(tmp_path, monkeypatch):
    _write_agent_config(tmp_path, monkeypatch, "[agent]\ntimeout_s = 1800\n")
    _serve, ws = _ws(tmp_path, monkeypatch)
    ref_id = _add_text(ws, tmp_path, "a.txt", "会超时的纪要")
    digest_provider = _NoteProvider("digest-only")
    seen: dict = {}

    def fake_runner(cmd, args, *, cwd, input, stdout_file=None, timeout=None):
        seen["timeout"] = timeout
        raise RuntimeError(f"CLI agent timeout after {timeout}s: {cmd}")

    _bind_digest(monkeypatch, digest_provider)
    monkeypatch.setattr("kairo.provider._default_cli_runner", fake_runner)

    result = runner.invoke(app, ["run", "--ref", ref_id])
    assert result.exit_code == 0, result.output + result.stderr
    assert seen["timeout"] == DEFAULT_NOTE_TIMEOUT_CAP_S
    assert seen["timeout"] != 1800
    combined = result.output + result.stderr
    assert "机器 note 未写入" in combined
    assert "timeout" in combined.lower()
    assert "120" in combined
    assert _show(ref_id)["count"] == 0
    assert (ws.root / "references" / ref_id / "digest.md").is_file()
    assert resolve_agent_timeout_s() == 1800
