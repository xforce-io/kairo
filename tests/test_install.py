"""#124: doctor / connect 只读体检与 skill 分发。"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from kairo.cli import app
from kairo.install import connect_skill, doctor_lines, skill_source_file

runner = CliRunner()


def test_skill_source_file_finds_packaged_copy():
    src = skill_source_file()
    assert src is not None
    assert src.is_file()
    assert src.name == "SKILL.md"
    assert "name: kairo" in src.read_text(encoding="utf-8")


def test_doctor_lines_stub_and_missing_skill(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    lines = doctor_lines(home=tmp_path)
    text = "\n".join(lines)
    assert "kairo " in text
    assert "provider: stub" in text
    assert "asr.whisper: 未配置" in text
    assert "skill: 未 connect" in text
    assert "kairo connect" in text


def test_connect_writes_canonical_and_detected_agents(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".cursor").mkdir()
    lines = connect_skill(home=tmp_path)
    canon = tmp_path / ".agents" / "skills" / "kairo" / "SKILL.md"
    assert canon.is_file()
    assert "name: kairo" in canon.read_text()
    claude = tmp_path / ".claude" / "skills" / "kairo"
    cursor = tmp_path / ".cursor" / "skills" / "kairo"
    assert claude.exists()
    assert cursor.exists()
    assert (claude / "SKILL.md").is_file()
    assert not (tmp_path / ".codex").exists()
    assert not (tmp_path / ".pi").exists()
    joined = "\n".join(lines)
    assert "canonical:" in joined
    assert "claude:" in joined
    assert "cursor:" in joined


def test_connect_without_agents_only_canonical(tmp_path):
    lines = connect_skill(home=tmp_path)
    assert (tmp_path / ".agents" / "skills" / "kairo" / "SKILL.md").is_file()
    assert not (tmp_path / ".claude").exists()
    assert "未探测到" in "\n".join(lines)
    assert "npx skills add xforce-io/kairo" in "\n".join(lines)


def test_cli_help_lists_doctor_and_connect():
    out = runner.invoke(app, ["--help"]).output
    assert "doctor" in out
    assert "connect" in out


def test_cli_doctor_outside_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KAIRO_STUB", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "provider:" in result.output
    assert "Traceback" not in result.output


def test_cli_connect_uses_home(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude").mkdir()
    result = runner.invoke(app, ["connect"])
    assert result.exit_code == 0
    assert (tmp_path / ".agents" / "skills" / "kairo" / "SKILL.md").is_file()
    assert (tmp_path / ".claude" / "skills" / "kairo" / "SKILL.md").is_file()
