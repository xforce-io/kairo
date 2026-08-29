"""#155 Docker public-read: --host、/readyz、current 跟随。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from kairo.cli import app as cli_app
from kairo.web.public import PUBLIC_STATE_FILENAME, PUBLIC_STATE_VERSION
from kairo.web.server import create_app

runner = CliRunner()


def _valid_empty_state(root: Path) -> None:
    (root / PUBLIC_STATE_FILENAME).write_text(
        json.dumps({"version": PUBLIC_STATE_VERSION, "generation": 1, "roots": []}),
        encoding="utf-8",
    )


def test_readyz_empty_valid_vs_missing(tmp_path):
    _valid_empty_state(tmp_path)
    c = TestClient(create_app(tmp_path, mode="public-read"))
    r = c.get("/readyz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    (tmp_path / PUBLIC_STATE_FILENAME).unlink()
    bad = c.get("/readyz")
    assert bad.status_code == 503
    assert bad.json() == {"ok": False}
    assert "path" not in bad.text


def test_readyz_corrupt_is_not_ready(tmp_path):
    (tmp_path / PUBLIC_STATE_FILENAME).write_text("{nope", encoding="utf-8")
    c = TestClient(create_app(tmp_path, mode="public-read"))
    r = c.get("/readyz")
    assert r.status_code == 503
    assert r.json() == {"ok": False}


def test_console_rejects_non_loopback_host(tmp_path):
    result = runner.invoke(
        cli_app, ["serve", str(tmp_path), "--host", "0.0.0.0", "--port", "9"]
    )
    assert result.exit_code == 2
    assert "回环" in result.output


def test_public_read_follows_current_symlink(tmp_path, monkeypatch):
    backup = tmp_path / "backup"
    g1 = backup / "generations" / "b-1" / "data"
    g2 = backup / "generations" / "b-2" / "data"
    g1.mkdir(parents=True)
    g2.mkdir(parents=True)
    _valid_empty_state(g1)
    (g2 / PUBLIC_STATE_FILENAME).write_text("{bad", encoding="utf-8")
    current = backup / "current"
    current.symlink_to("generations/b-1")
    data_root = backup / "current" / "data"
    c = TestClient(create_app(data_root, mode="public-read"))
    assert c.get("/readyz").status_code == 200
    current.unlink()
    current.symlink_to("generations/b-2")
    assert c.get("/readyz").status_code == 503


def test_cli_serve_public_read_passes_host(tmp_path, monkeypatch):
    _valid_empty_state(tmp_path)
    seen: dict = {}

    def fake_run(root, port=8787, *, mode="console", host="127.0.0.1"):
        seen.update(root=root, port=port, mode=mode, host=host)

    monkeypatch.setattr("kairo.web.server.run", fake_run)
    result = runner.invoke(
        cli_app,
        [
            "serve",
            str(tmp_path),
            "--mode",
            "public-read",
            "--host",
            "0.0.0.0",
            "--port",
            "9999",
        ],
    )
    assert result.exit_code == 0, result.output
    assert seen.get("mode") == "public-read"
    assert seen.get("host") == "0.0.0.0"
    assert seen.get("port") == 9999
