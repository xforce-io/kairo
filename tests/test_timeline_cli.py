import json

from typer.testing import CliRunner

from kairo.cli import app
from kairo.workspace import Workspace

runner = CliRunner()


def test_cli_timeline_and_occurred(tmp_path, monkeypatch):
    root = tmp_path / "root"
    wsdir = root / "alpha"
    wsdir.mkdir(parents=True)
    monkeypatch.chdir(wsdir)
    Workspace.init(wsdir, topic="A")
    src = tmp_path / "m.txt"
    src.write_text("hi")
    add = runner.invoke(app, ["add", str(src), "--id", "2026-08-25-weekly", "--occurred", "2026-08-24"])
    assert add.exit_code == 0
    out = runner.invoke(app, ["timeline", str(root), "--day", "2026-08-24"]).output
    assert "2026-08-25-weekly" in out
    js = json.loads(runner.invoke(app, ["timeline", str(root), "--json"]).output)
    assert any(x["id"] == "2026-08-25-weekly" and x["occurred_source"] == "user" for x in js)
    bad = runner.invoke(app, ["occurred", "2026-08-25-weekly", "2026-02-31"])
    assert bad.exit_code == 1
    ok = runner.invoke(app, ["occurred", "2026-08-25-weekly", "2026-08-21"])
    assert ok.exit_code == 0
    out = runner.invoke(app, ["timeline", str(root), "--day", "2026-08-21"]).output
    assert "2026-08-25-weekly" in out
    clr = runner.invoke(app, ["occurred", "2026-08-25-weekly", "--clear"])
    assert clr.exit_code == 0
    out = runner.invoke(app, ["timeline", str(root), "--day", "2026-08-25"]).output
    assert "2026-08-25-weekly" in out
    mutex = runner.invoke(app, ["timeline", str(root), "--day", "2026-08-24", "--recent"])
    assert mutex.exit_code == 1
    corpus = tmp_path / "c.txt"
    corpus.write_text("c")
    badc = runner.invoke(app, ["add", str(corpus), "--corpus", "--occurred", "2026-08-24"])
    assert badc.exit_code == 1
