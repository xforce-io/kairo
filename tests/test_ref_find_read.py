"""#402 / #445 kairo ref find / ref read。"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from kairo.cli import app
from kairo.notes import add_note
from kairo.ref_find import FIND_IN_VALUES
from kairo.refs import create_tag, global_home_path
from kairo.workspace import Workspace

runner = CliRunner()
STEPS = {"n": 0}


def _cli(args, cwd: Path, monkeypatch):
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(cwd if (cwd / ".kairo").is_dir() or (cwd / "energy").is_dir() else cwd))
    return runner.invoke(app, args)


def _load(result):
    assert result.exit_code == 0, result.output + result.stderr
    return json.loads(result.stdout)


def _fail(result):
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "content" not in payload
    return payload


def _setup(tmp_path, monkeypatch):
    serve = tmp_path / "root"
    serve.mkdir()
    create_tag(serve, "energy")
    Workspace.init(serve / "energy", topic="energy")
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(serve))
    monkeypatch.chdir(serve / "energy")
    a = tmp_path / "北港现场同步.txt"
    a.write_text("转写甲")
    b = tmp_path / "北港产品计划.txt"
    b.write_text("转写乙")
    g = tmp_path / "全局笔记.txt"
    g.write_text("全局转写")
    r1 = runner.invoke(app, ["add", str(a), "--copy", "--occurred", "2026-09-11"])
    r2 = runner.invoke(app, ["add", str(b), "--copy", "--occurred", "2026-09-11"])
    assert r1.exit_code == 0, r1.output
    assert r2.exit_code == 0, r2.output
    monkeypatch.chdir(serve)
    r3 = runner.invoke(app, ["add", str(g), "--copy", "--occurred", "2026-09-12"])
    assert r3.exit_code == 0, r3.output
    energy = serve / "energy"
    ws = Workspace.open(energy)
    titles = {ws.read_manifest(i).title: i for i in ws.list_reference_ids()}
    (energy / "references" / titles["北港现场同步"] / "digest.md").write_text("纪要甲")
    (energy / "references" / titles["北港产品计划"] / "digest.md").write_text("纪要乙")
    gws = Workspace.open(global_home_path(serve))
    gtitles = {gws.read_manifest(i).title: i for i in gws.list_reference_ids()}
    titles.update(gtitles)
    return serve, titles


def test_s1_filters_zero_one_many(tmp_path, monkeypatch):
    serve, titles = _setup(tmp_path, monkeypatch)
    monkeypatch.chdir(serve)
    many = _load(runner.invoke(app, ["ref", "find", "--day", "2026-09-11", "--json"]))
    assert many["count"] == 2
    assert {i["id"] for i in many["items"]} == {titles["北港现场同步"], titles["北港产品计划"]}
    assert all("content" not in i for i in many["items"])
    one = _load(runner.invoke(app, ["ref", "find", "--title", "现场同步", "--json"]))
    assert one["count"] == 1
    assert one["items"][0]["id"] == titles["北港现场同步"]
    zero = _load(runner.invoke(app, ["ref", "find", "--title", "不存在的标题", "--json"]))
    assert zero["count"] == 0
    assert zero["items"] == []
    human = runner.invoke(app, ["ref", "find", "--title", "不存在的标题"])
    assert human.exit_code == 0
    assert "count 0" in human.stdout
    topic = _load(runner.invoke(app, ["ref", "find", "--topic", "energy", "--json"]))
    assert {titles["北港现场同步"], titles["北港产品计划"]} <= {i["id"] for i in topic["items"]}
    anded = _load(
        runner.invoke(app, ["ref", "find", "--title", "北港", "--day", "2026-09-11", "--json"])
    )
    assert anded["count"] == 2
    empty_and = _load(
        runner.invoke(app, ["ref", "find", "--title", "全局", "--day", "2026-09-11", "--json"])
    )
    assert empty_and["count"] == 0


def test_s2_read_digest_and_transcript(tmp_path, monkeypatch):
    serve, titles = _setup(tmp_path, monkeypatch)
    monkeypatch.chdir(serve)
    rid = titles["北港现场同步"]
    digest = _load(
        runner.invoke(app, ["ref", "read", "--id", rid, "--form", "digest", "--json"])
    )
    assert digest["content"] == "纪要甲"
    assert digest["form"] == "digest"
    assert digest["home"] == "energy"
    assert digest["source"]["id"] == rid
    assert "references/" not in json.dumps(digest["source"])
    tr = _load(
        runner.invoke(app, ["ref", "read", "--id", rid, "--form", "transcript", "--json"])
    )
    assert "转写甲" in tr["content"]
    glob = _load(
        runner.invoke(
            app,
            ["ref", "read", "--id", titles["全局笔记"], "--home", "global", "--form", "transcript", "--json"],
        )
    )
    assert glob["home"] == ""
    assert "全局转写" in glob["content"]


def test_s3_errors_and_no_swap(tmp_path, monkeypatch):
    serve, titles = _setup(tmp_path, monkeypatch)
    monkeypatch.chdir(serve)
    rid = titles["北港现场同步"]
    none = _fail(runner.invoke(app, ["ref", "find", "--json"]))
    assert none["code"] == "invalid_request"
    missing = _fail(runner.invoke(app, ["ref", "read", "--id", "nope", "--form", "digest", "--json"]))
    assert missing["code"] == "not_found"
    no_digest = _fail(
        runner.invoke(app, ["ref", "read", "--id", titles["全局笔记"], "--form", "digest", "--json"])
    )
    assert no_digest["code"] == "material_unavailable"
    bad_form = _fail(
        runner.invoke(app, ["ref", "read", "--id", rid, "--form", "prose", "--json"])
    )
    assert bad_form["code"] == "invalid_request"
    upload = serve / "energy" / ".kairo" / "uploads" / "北港现场同步.txt"
    assert upload.is_file()
    upload.unlink()
    gone = _fail(
        runner.invoke(app, ["ref", "read", "--id", rid, "--form", "transcript", "--json"])
    )
    assert gone["code"] == "read_failed"
    swapped = runner.invoke(app, ["ref", "read", "--id", titles["全局笔记"], "--form", "digest", "--json"])
    payload = json.loads(swapped.stdout)
    assert payload.get("content") != "全局转写"


def test_no_step_run_re_step(tmp_path, monkeypatch):
    serve, titles = _setup(tmp_path, monkeypatch)
    monkeypatch.chdir(serve)
    import kairo.engine as engine

    calls = {"n": 0}

    def boom(*_a, **_k):
        calls["n"] += 1
        raise AssertionError("step must not run")

    monkeypatch.setattr(engine, "step", boom)
    runner.invoke(app, ["ref", "find", "--title", "北港", "--json"])
    runner.invoke(app, ["ref", "read", "--id", titles["北港现场同步"], "--form", "digest", "--json"])
    assert calls["n"] == 0


def _ids(result):
    return {i["id"] for i in _load(result)["items"]}


def test_s1_in_enum_and_invalid(tmp_path, monkeypatch):
    serve, titles = _setup(tmp_path, monkeypatch)
    monkeypatch.chdir(serve)
    help_r = runner.invoke(app, ["ref", "find", "--help"])
    assert help_r.exit_code == 0, help_r.output
    assert "--in" in help_r.stdout
    for value in FIND_IN_VALUES:
        assert value in help_r.stdout
    assert FIND_IN_VALUES == ("title", "digest", "notes", "transcript", "all")
    bad = runner.invoke(app, ["ref", "find", "纪要甲", "--in", "body", "--json"])
    payload = _fail(bad)
    assert payload["code"] == "invalid_request"
    assert "非法 --in" in payload["error"]
    human = runner.invoke(app, ["ref", "find", "纪要甲", "--in", "semantic"])
    assert human.exit_code != 0
    assert "非法 --in" in (human.stderr or "") + human.output


def test_s2_omit_in_equals_title(tmp_path, monkeypatch):
    serve, titles = _setup(tmp_path, monkeypatch)
    monkeypatch.chdir(serve)
    omitted = _ids(runner.invoke(app, ["ref", "find", "--title", "北港", "--json"]))
    explicit = _ids(runner.invoke(app, ["ref", "find", "--title", "北港", "--in", "title", "--json"]))
    positional = _ids(runner.invoke(app, ["ref", "find", "北港", "--json"]))
    assert omitted == explicit == positional
    assert omitted == {titles["北港现场同步"], titles["北港产品计划"]}


def test_s3_literal_field_and_all(tmp_path, monkeypatch):
    serve, titles = _setup(tmp_path, monkeypatch)
    monkeypatch.chdir(serve)
    rid = titles["北港现场同步"]
    add_note(serve, ref_id=rid, content="洞察标记词", home="energy")
    cases = (
        ("纪要甲", "digest", rid),
        ("转写甲", "transcript", rid),
        ("洞察标记词", "notes", rid),
    )
    for needle, field, expect in cases:
        miss = _load(runner.invoke(app, ["ref", "find", needle, "--in", "title", "--json"]))
        assert miss["count"] == 0, field
        hit = _ids(runner.invoke(app, ["ref", "find", needle, "--in", field, "--json"]))
        assert hit == {expect}, field
        via_title = _ids(
            runner.invoke(app, ["ref", "find", "--title", needle, "--in", field, "--json"])
        )
        assert via_title == {expect}, field
        also = _ids(runner.invoke(app, ["ref", "find", needle, "--in", "all", "--json"]))
        assert expect in also, field
    no_query = _fail(runner.invoke(app, ["ref", "find", "--in", "digest", "--day", "2026-09-11", "--json"]))
    assert no_query["code"] == "invalid_request"
    clash = _fail(runner.invoke(app, ["ref", "find", "纪要甲", "--title", "北港", "--in", "digest", "--json"]))
    assert clash["code"] == "invalid_request"
    title_only = Path(__file__).resolve().parents[1] / "src" / "kairo" / "ref_find.py"
    source = title_only.read_text(encoding="utf-8").lower()
    for banned in ("embedding", "vector", "semantic", "similarity"):
        assert banned not in source
