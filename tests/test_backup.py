"""#154 完整备份:恢复闭包、原子 current、校验与恢复。"""

from __future__ import annotations

import os
import shutil
import stat
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

_FAKE_SSH = r"""#!/usr/bin/env python3
import subprocess
import sys

args = sys.argv[1:]
i = 0
while i < len(args):
    a = args[i]
    if a in ("-o", "-p", "-l", "-F", "-i") and i + 1 < len(args):
        i += 2
        continue
    if a == "--":
        i += 1
        break
    if a.startswith("-"):
        i += 1
        continue
    break
if i >= len(args):
    sys.exit(1)
cmd = args[i + 1 :]
if cmd[:1] == ["--"]:
    cmd = cmd[1:]
if not cmd:
    sys.exit(1)
if len(cmd) == 1:
    raise SystemExit(
        subprocess.call(["bash", "--noprofile", "--norc", "-c", cmd[0]])
    )
raise SystemExit(subprocess.call(cmd))
"""


def _install_local_ssh(tmp_path: Path, monkeypatch) -> None:
    """PATH 上的 ssh 在本机执行远端命令,供 rsync -e ssh 与 ssh 脚本共用。"""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    ssh = bindir / "ssh"
    ssh.write_text(_FAKE_SSH, encoding="utf-8")
    ssh.chmod(ssh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])


def _write_remote_config(
    tmp_path: Path, name: str, dest: Path, *, ssh: str = "localhost"
) -> None:
    cfg = tmp_path / "cfg" / "kairo"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "config.toml").write_text(
        f'[remote.{name}]\nssh = "{ssh}"\npath = "{dest}"\n',
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
    _install_local_ssh(tmp_path, monkeypatch)
    _write_remote_config(tmp_path, "reader", remote, ssh="kairo-test-remote")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
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


def test_push_named_ssh_other_host_does_not_write_local_path(tmp_path, monkeypatch):
    serve = _serve_with_pointers(tmp_path)
    canary = tmp_path / "srv" / "kairo" / "backups"
    canary.mkdir(parents=True)
    marker = canary / "keep-me"
    marker.write_text("untouched\n", encoding="utf-8")
    _write_remote_config(
        tmp_path, "reader", canary, ssh="kairo-missing-backup-host.invalid"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    result = runner.invoke(app, ["backup", "push", "reader", str(serve)])
    assert result.exit_code != 0
    combined = (result.output or "") + (getattr(result, "stderr", None) or "")
    assert "pushed" not in combined
    assert "Traceback" not in combined
    assert ":" in combined
    assert not (canary / "generations").exists()
    assert not (canary / "current").exists()
    assert marker.read_text(encoding="utf-8") == "untouched\n"
    assert list(canary.iterdir()) == [marker]


def test_restore_rejects_mutated_copy_before_commit(tmp_path, monkeypatch):
    serve = _serve_with_pointers(tmp_path)
    remote = tmp_path / "remote"
    publish(serve, remote)
    dest = tmp_path / "restored"
    real = shutil.copytree

    def mutate(src, dst, *args, **kwargs):
        out = real(src, dst, *args, **kwargs)
        target = Path(dst) / "alpha" / "understanding.md"
        if target.is_file():
            target.write_text("mutated-bytes\n", encoding="utf-8")
        return out

    monkeypatch.setattr("kairo.backup.shutil.copytree", mutate)
    with pytest.raises(BackupError):
        restore_generation(remote, dest)
    assert not dest.exists() or (dest.is_dir() and not any(dest.iterdir()))


def test_cli_push_corrupt_current_backup_json_is_contract_error(tmp_path, monkeypatch):
    serve = _serve_with_pointers(tmp_path)
    remote = tmp_path / "remote"
    _install_local_ssh(tmp_path, monkeypatch)
    _write_remote_config(tmp_path, "reader", remote, ssh="kairo-test-remote")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    first = runner.invoke(app, ["backup", "push", "reader", str(serve)])
    assert first.exit_code == 0, first.output
    assert "pushed" in first.output
    current = (remote / "current").readlink()
    gen = remote / current
    (gen / "backup.json").write_text("{nope", encoding="utf-8")
    second = runner.invoke(app, ["backup", "push", "reader", str(serve)])
    combined = (second.output or "") + (getattr(second, "stderr", None) or "")
    assert second.exit_code != 0
    assert "Traceback" not in combined
    assert ":" in combined
    assert (remote / "current").readlink() == current
    from kairo.backup import read_result

    rec = read_result("reader")
    assert rec is not None
    assert rec["status"] == "failed"
