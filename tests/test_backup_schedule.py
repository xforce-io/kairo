"""#156 周期备份:锁、最近结果、失败保留 current。"""

from __future__ import annotations

import fcntl
import os

import pytest

from typer.testing import CliRunner

from kairo.backup import push_named, read_result, result_path, verify_generation
from kairo.cli import app
from test_backup import _install_local_ssh, _serve_with_pointers, _write_remote_config

runner = CliRunner()


def test_push_named_writes_status_and_keeps_current_on_failure(tmp_path, monkeypatch):
    serve = _serve_with_pointers(tmp_path)
    remote = tmp_path / "remote"
    _install_local_ssh(tmp_path, monkeypatch)
    _write_remote_config(tmp_path, "reader", remote, ssh="kairo-test-remote")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    first = push_named("reader", serve)
    rec = read_result("reader")
    assert rec is not None
    assert rec["status"] == "pushed"
    assert rec["backup_id"] == first.backup_id
    assert rec["last_success_at"]
    bid = first.backup_id
    (tmp_path / "ext-audio.m4a").unlink()
    with pytest.raises(Exception):
        push_named("reader", serve)
    rec2 = read_result("reader")
    assert rec2 is not None
    assert rec2["status"] == "failed"
    assert rec2["backup_id"] == bid
    assert rec2["last_success_at"] == rec["last_success_at"]
    assert verify_generation(remote).backup_id == bid


def test_overlap_skip_exit_3(tmp_path, monkeypatch):
    serve = _serve_with_pointers(tmp_path)
    remote = tmp_path / "remote"
    _install_local_ssh(tmp_path, monkeypatch)
    _write_remote_config(tmp_path, "reader", remote, ssh="kairo-test-remote")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    push_named("reader", serve)
    lock = result_path("reader").with_suffix(".lock")
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        result = runner.invoke(app, ["backup", "push", "reader", str(serve)])
        assert result.exit_code == 3
        rec = read_result("reader")
        assert rec is not None
        assert rec["status"] == "skipped"
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_corrupt_result_status_fails(tmp_path, monkeypatch):
    _write_remote_config(tmp_path, "reader", tmp_path / "remote")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    path = result_path("reader")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[]", encoding="utf-8")
    st = runner.invoke(app, ["backup", "status", "reader"])
    assert st.exit_code == 1
    assert "不可读" in st.output


def test_backup_status_empty_and_ok(tmp_path, monkeypatch):
    serve = _serve_with_pointers(tmp_path)
    remote = tmp_path / "remote"
    _install_local_ssh(tmp_path, monkeypatch)
    _write_remote_config(tmp_path, "reader", remote, ssh="kairo-test-remote")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    empty = runner.invoke(app, ["backup", "status", "reader"])
    assert empty.exit_code == 0
    assert "empty" in empty.output
    runner.invoke(app, ["backup", "push", "reader", str(serve)])
    st = runner.invoke(app, ["backup", "status", "reader"])
    assert st.exit_code == 0
    assert "last_attempt_at=" in st.output
    assert "last_success_at=" in st.output
    assert "backup_id=" in st.output
    assert "status=pushed" in st.output
