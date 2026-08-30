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
    spanned = runner.invoke(
        app, ["timeline", str(root), "--from", "2026-08-25", "--to", "2026-08-25"]
    )
    assert spanned.exit_code == 0
    assert "2026-08-25-weekly" in spanned.output
    corpus = tmp_path / "c.txt"
    corpus.write_text("c")
    badc = runner.invoke(app, ["add", str(corpus), "--corpus", "--occurred", "2026-08-24"])
    assert badc.exit_code == 1


def test_cli_review_writes_reference(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    root = tmp_path / "root"
    wsdir = root / "alpha"
    dest = root / "回顾"
    wsdir.mkdir(parents=True)
    dest.mkdir()
    Workspace.init(wsdir, topic="A")
    Workspace.init(dest, topic="回顾")
    src = tmp_path / "m.txt"
    src.write_text("hi")
    wa = Workspace.open(wsdir)
    rid = wa.add([src], ref_id="meet", occurred_at="2026-08-20")
    (wa.references_dir() / rid / "digest.md").write_text("结论")
    missing = runner.invoke(
        app, ["review", "--from", "2026-08-20", "--to", "2026-08-20", "--root", str(root)]
    )
    assert missing.exit_code == 2
    ok = runner.invoke(
        app,
        [
            "review",
            "--from",
            "2026-08-20",
            "--to",
            "2026-08-20",
            "--workspace",
            "回顾",
            "--root",
            str(root),
        ],
    )
    assert ok.exit_code == 0, ok.output
    assert "review " in ok.output
    ids = Workspace.open(dest).list_reference_ids()
    assert ids
    man = Workspace.open(dest).read_manifest(ids[-1])
    assert man.occurred_at == "2026-08-20"
    too_long = runner.invoke(
        app,
        [
            "review",
            "--from",
            "2026-08-01",
            "--to",
            "2026-09-01",
            "--workspace",
            "回顾",
            "--root",
            str(root),
        ],
    )
    assert too_long.exit_code == 1
    assert "31" in (too_long.output + (too_long.stderr or ""))
