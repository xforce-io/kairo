"""#154 完整备份:恢复闭包、原子 current、校验与恢复。"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from kairo.backup import (
    BackupError,
    load_remote,
    publish,
    restore_generation,
    validate_backup_id,
    verify_generation,
)
from kairo.cli import app
from kairo.workspace import Workspace

runner = CliRunner()


def _write_remote_config(tmp_path: Path, name: str, dest: Path) -> None:
    cfg = tmp_path / "cfg" / "kairo"
    cfg.mkdir(parents=True)
    (cfg / "config.toml").write_text(
        f'[remote.{name}]\nssh = "localhost"\npath = "{dest}"\n',
        encoding="utf-8",
    )


def _serve_with_pointers(tmp_path: Path) -> Path:
    serve = tmp_path / "serve"
    serve.mkdir()
    (serve / "pinned.yaml").write_text("- a\n", encoding="utf-8")
    a = Workspace.init(serve / "alpha", topic="alpha")
    (a.root / "understanding.md").write_text("fact-a\n", encoding="utf-8")
    inner = a.root / "inside.txt"
    inner.write_text("inside\n", encoding="utf-8")
    a.add([inner])
    ext = tmp_path / "ext-audio.m4a"
    ext.write_bytes(b"AUDIO")
    a.add([ext])
    b = Workspace.init(serve / "beta", topic="beta")
    (b.root / "understanding.md").write_text("fact-b\n", encoding="utf-8")
    tree = tmp_path / "ext-corpus"
    tree.mkdir()
    (tree / "doc.md").write_text("corpus\n", encoding="utf-8")
    empty = tree / "empty-dir"
    empty.mkdir()
    b.add([tree], source_class="corpus")
    return serve


def test_publish_restore_survives_deleted_external_source(tmp_path):
    serve = _serve_with_pointers(tmp_path)
    remote = tmp_path / "remote"
    first = publish(serve, remote)
    assert first.status == "pushed"
    validate_backup_id(first.backup_id)
    current = remote / "current"
    assert current.is_symlink()
    assert Path(current.readlink()).as_posix() == f"generations/{first.backup_id}"
    verify_generation(remote)

    (tmp_path / "ext-audio.m4a").unlink()
    shutil_rm = tmp_path / "ext-corpus"
    import shutil

    shutil.rmtree(shutil_rm)

    dest = tmp_path / "restored"
    restored = restore_generation(remote, dest)
    assert restored.status == "restored"
    assert restored.backup_id == first.backup_id
    items = {p.name for p in dest.iterdir() if p.is_dir()}
    assert "alpha" in items and "beta" in items
    alpha = Workspace.open(dest / "alpha")
    for rid in alpha.list_reference_ids():
        man = alpha.read_manifest(rid)
        for form in man.forms:
            loc = Path(form.location)
            path = loc if loc.is_absolute() else alpha.root / loc
            assert path.exists(), form.location
            assert path.is_relative_to(alpha.root)
    beta = Workspace.open(dest / "beta")
    for rid in beta.list_reference_ids():
        man = beta.read_manifest(rid)
        for form in man.forms:
            path = beta.root / form.location
            assert path.exists()

    with pytest.raises(BackupError):
        publish(serve, remote)
    assert verify_generation(remote).backup_id == first.backup_id


def test_publish_unchanged_and_failed_current_kept(tmp_path):
    serve = _serve_with_pointers(tmp_path)
    remote = tmp_path / "remote"
    first = publish(serve, remote)
    second = publish(serve, remote)
    assert second.status == "unchanged"
    assert second.backup_id == first.backup_id
    gens = list((remote / "generations").iterdir())
    assert len(gens) == 1

    dest = tmp_path / "not-empty"
    dest.mkdir()
    (dest / "x").write_text("nope")
    with pytest.raises(BackupError) as exc:
        restore_generation(remote, dest)
    assert exc.value.code == 2
    assert (dest / "x").read_text() == "nope"


def test_symlink_in_serve_root_fails(tmp_path):
    serve = tmp_path / "serve"
    serve.mkdir()
    Workspace.init(serve / "ws", topic="ws")
    (serve / "link").symlink_to(serve / "ws")
    with pytest.raises(BackupError):
        publish(serve, tmp_path / "remote")


def test_cli_backup_push_verify_restore(tmp_path, monkeypatch):
    serve = _serve_with_pointers(tmp_path)
    remote = tmp_path / "remote"
    _write_remote_config(tmp_path, "reader", remote)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    result = runner.invoke(app, ["backup", "push", "reader", str(serve)])
    assert result.exit_code == 0, result.output
    assert "pushed" in result.output
    v = runner.invoke(app, ["backup", "verify", "reader"])
    assert v.exit_code == 0 and "ok" in v.output
    dest = tmp_path / "out"
    r = runner.invoke(app, ["backup", "restore", "reader", str(dest)])
    assert r.exit_code == 0, r.output
    assert (dest / "alpha" / "understanding.md").is_file()


def test_missing_pointer_fails_before_current_switch(tmp_path):
    serve = _serve_with_pointers(tmp_path)
    (tmp_path / "ext-audio.m4a").unlink()
    remote = tmp_path / "remote"
    with pytest.raises(BackupError):
        publish(serve, remote)
    assert not (remote / "current").exists()


def test_tampered_generation_verify_fails(tmp_path):
    serve = _serve_with_pointers(tmp_path)
    remote = tmp_path / "remote"
    result = publish(serve, remote)
    body = remote / "generations" / result.backup_id / "data" / "alpha" / "understanding.md"
    body.write_text("mutated\n", encoding="utf-8")
    with pytest.raises(BackupError):
        verify_generation(remote)


def test_load_remote_rejects_bad_name(tmp_path, monkeypatch):
    _write_remote_config(tmp_path, "ok", tmp_path / "r")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    with pytest.raises(BackupError) as exc:
        load_remote("../x")
    assert exc.value.code == 2
