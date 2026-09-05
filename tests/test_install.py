"""#124 / #214: doctor / connect 只读体检与 skill 分发。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from typer.testing import CliRunner

from kairo.cli import app
from kairo.install import connect_skill, doctor_lines, skill_source_file

runner = CliRunner()

_CONNECTED_OK = "已 connect"
_ADAPTER_STUB = "---\nname: kairo\n---\n# Kairo adapter stub\n"


def _packaged_skill_text() -> str:
    src = skill_source_file()
    assert src is not None
    return src.read_text(encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plant_foreign_adapter(home: Path) -> Path:
    """Canonical path is a symlink to a short adapter; return the adapter SKILL.md."""
    adapter_dir = home / "library" / "kairo"
    adapter_dir.mkdir(parents=True)
    adapter_md = adapter_dir / "SKILL.md"
    adapter_md.write_text(_ADAPTER_STUB, encoding="utf-8")
    canon = home / ".agents" / "skills" / "kairo"
    canon.parent.mkdir(parents=True)
    canon.symlink_to(adapter_dir)
    return adapter_md


def test_skill_source_file_finds_packaged_copy():
    src = skill_source_file()
    assert src is not None
    assert src.is_file()
    assert src.name == "SKILL.md"
    assert "name: kairo" in src.read_text(encoding="utf-8")


def test_doctor_shows_configured_codex_model(tmp_path, monkeypatch):
    from kairo.provider import CodexProvider

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "kairo.install.select_provider",
        lambda: CodexProvider(model="gpt-5.6-terra"),
    )
    text = "\n".join(doctor_lines(home=tmp_path))
    assert "provider: codex (gpt-5.6-terra)" in text


def test_doctor_lines_stub_and_missing_skill(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    lines = doctor_lines(home=tmp_path)
    text = "\n".join(lines)
    assert "kairo " in text
    assert "provider: stub" in text
    assert "asr.whisper: 未配置" in text
    assert "-f srt" in text
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


def test_doctor_matching_packaged_skill_is_connected(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    canon = tmp_path / ".agents" / "skills" / "kairo"
    canon.mkdir(parents=True)
    (canon / "SKILL.md").write_text(_packaged_skill_text(), encoding="utf-8")
    text = "\n".join(doctor_lines(home=tmp_path))
    assert _CONNECTED_OK in text


def test_doctor_foreign_adapter_symlink_is_not_connected(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    _plant_foreign_adapter(tmp_path)
    text = "\n".join(doctor_lines(home=tmp_path))
    assert _CONNECTED_OK not in text
    assert "未 connect" in text
    assert "不一致" in text


def test_doctor_mismatched_file_is_not_connected(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    canon = tmp_path / ".agents" / "skills" / "kairo"
    canon.mkdir(parents=True)
    (canon / "SKILL.md").write_text(_ADAPTER_STUB, encoding="utf-8")
    text = "\n".join(doctor_lines(home=tmp_path))
    assert _CONNECTED_OK not in text
    assert "未 connect" in text
    assert "不一致" in text


def test_connect_does_not_clobber_foreign_adapter_symlink(tmp_path):
    adapter_md = _plant_foreign_adapter(tmp_path)
    before = _sha256(adapter_md)
    (tmp_path / ".claude").mkdir()
    lines = connect_skill(home=tmp_path)
    joined = "\n".join(lines)
    assert _sha256(adapter_md) == before
    assert adapter_md.read_text(encoding="utf-8") == _ADAPTER_STUB
    canon = tmp_path / ".agents" / "skills" / "kairo"
    assert canon.is_symlink()
    assert canon.resolve() == adapter_md.parent.resolve()
    assert not (tmp_path / ".claude" / "skills" / "kairo").exists()
    assert "canonical:" not in joined


def test_connect_does_not_overwrite_mismatched_canonical_file(tmp_path):
    canon = tmp_path / ".agents" / "skills" / "kairo"
    canon.mkdir(parents=True)
    md = canon / "SKILL.md"
    md.write_text(_ADAPTER_STUB, encoding="utf-8")
    before = _sha256(md)
    connect_skill(home=tmp_path)
    assert _sha256(md) == before
    assert md.read_text(encoding="utf-8") == _ADAPTER_STUB


def test_connect_does_not_write_through_matching_skill_md_symlink(tmp_path):
    foreign = tmp_path / "library" / "kairo"
    foreign.mkdir(parents=True)
    foreign_md = foreign / "SKILL.md"
    foreign_md.write_text(_packaged_skill_text(), encoding="utf-8")
    before = _sha256(foreign_md)
    before_mtime = foreign_md.stat().st_mtime
    canon = tmp_path / ".agents" / "skills" / "kairo"
    canon.mkdir(parents=True)
    (canon / "SKILL.md").symlink_to(foreign_md)
    (tmp_path / ".claude").mkdir()
    lines = connect_skill(home=tmp_path)
    assert _sha256(foreign_md) == before
    assert foreign_md.stat().st_mtime == before_mtime
    assert (canon / "SKILL.md").is_symlink()
    assert "canonical:" not in "\n".join(lines)
    assert not (tmp_path / ".claude" / "skills" / "kairo").exists()


def test_connect_does_not_write_through_mismatched_skill_md_symlink(tmp_path):
    foreign = tmp_path / "library" / "kairo"
    foreign.mkdir(parents=True)
    foreign_md = foreign / "SKILL.md"
    foreign_md.write_text(_ADAPTER_STUB, encoding="utf-8")
    before = _sha256(foreign_md)
    canon = tmp_path / ".agents" / "skills" / "kairo"
    canon.mkdir(parents=True)
    (canon / "SKILL.md").symlink_to(foreign_md)
    connect_skill(home=tmp_path)
    assert _sha256(foreign_md) == before
    assert foreign_md.read_text(encoding="utf-8") == _ADAPTER_STUB
    assert (canon / "SKILL.md").is_symlink()


def test_connect_empty_home_writes_packaged_skill_bytes(tmp_path):
    (tmp_path / ".claude").mkdir()
    connect_skill(home=tmp_path)
    canon_md = tmp_path / ".agents" / "skills" / "kairo" / "SKILL.md"
    assert canon_md.is_file()
    assert canon_md.read_text(encoding="utf-8") == _packaged_skill_text()
    assert (tmp_path / ".claude" / "skills" / "kairo" / "SKILL.md").is_file()
