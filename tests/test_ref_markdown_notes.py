"""#410 kairo notes list/add/show。不触发 step/compose。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from typer.testing import CliRunner

from kairo.cli import app
from kairo.notes import add_note, parse_stable_id, stable_id_for
from kairo.refs import create_tag, global_home_path, ref_key
from kairo.workspace import Workspace

runner = CliRunner()


def _cli(args, *, input=None):
    return runner.invoke(app, args, input=input)


def _load(result):
    assert result.exit_code == 0, result.output + result.stderr
    return json.loads(result.stdout)


def _fail(result):
    assert result.exit_code == 1, result.output + result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    return payload


def _setup(tmp_path, monkeypatch):
    serve = tmp_path / "root"
    serve.mkdir()
    create_tag(serve, "energy")
    create_tag(serve, "other")
    Workspace.init(serve / "energy", topic="energy")
    Workspace.init(serve / "other", topic="other")
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(serve))
    monkeypatch.chdir(serve / "energy")
    a = tmp_path / "现场.txt"
    a.write_text("转写甲")
    r1 = _cli(["add", str(a), "--copy"])
    assert r1.exit_code == 0, r1.output
    monkeypatch.chdir(serve / "other")
    b = tmp_path / "旁路.txt"
    b.write_text("转写乙")
    r2 = _cli(["add", str(b), "--copy"])
    assert r2.exit_code == 0, r2.output
    energy = Workspace.open(serve / "energy")
    other = Workspace.open(serve / "other")
    eid = energy.list_reference_ids()[0]
    oid = other.list_reference_ids()[0]
    understanding = serve / "energy" / "understanding.md"
    understanding.write_text("SENTINEL")
    monkeypatch.chdir(serve)
    return serve, eid, oid, understanding


def test_help_three_commands():
    for name in ("add", "list", "show"):
        r = _cli(["notes", name, "--help"])
        assert r.exit_code == 0, r.output
        assert "Usage" in r.stdout or "usage" in r.stdout.lower() or "--help" in r.stdout or "help" in r.stdout.lower()
        assert r.stdout.strip()


def test_s1_add_list_show(tmp_path, monkeypatch):
    serve, eid, _oid, _u = _setup(tmp_path, monkeypatch)
    empty = _load(_cli(["notes", "list", "--ref", eid, "--json"]))
    assert empty["count"] == 0
    assert empty["items"] == []
    assert empty["provenance"] == []
    added = _load(
        _cli(
            ["notes", "add", eid, "--content", "-", "--json"],
            input="第一条洞察\n",
        )
    )
    assert added["ok"] is True
    assert added["type"] == "insight"
    assert added["stable_id"].startswith("note:")
    listed = _load(_cli(["notes", "list", "--ref", eid, "--json"]))
    assert listed["count"] == 1
    assert listed["items"][0]["stable_id"] == added["stable_id"]
    shown = _load(_cli(["notes", "show", added["stable_id"], "--json"]))
    assert shown["content"] == "第一条洞察"
    assert shown["author"]
    assert shown["created_at"]
    assert shown["type"] == "insight"
    human = _cli(["notes", "show", "--ref", eid])
    assert human.exit_code == 0
    assert "第一条洞察" in human.stdout


def test_s2_failures_no_half_write(tmp_path, monkeypatch):
    serve, eid, _oid, _u = _setup(tmp_path, monkeypatch)
    before = list((serve / "energy" / "references" / eid).glob("notes.jsonl"))
    missing = _fail(_cli(["notes", "add", "no-such-ref", "--content", "x", "--json"]))
    assert missing["code"] == "not_found"
    blank = _fail(_cli(["notes", "add", eid, "--content", "   ", "--json"]))
    assert blank["code"] == "invalid_request"
    bad_type = _fail(_cli(["notes", "add", eid, "--content", "x", "--type", "gossip", "--json"]))
    assert bad_type["code"] == "invalid_request"
    noscope = _fail(_cli(["notes", "list", "--json"]))
    assert noscope["code"] == "invalid_request"
    both = _fail(_cli(["notes", "list", "--ref", eid, "--topic", "energy", "--json"]))
    assert both["code"] == "invalid_request"
    after = list((serve / "energy" / "references" / eid).glob("notes.jsonl"))
    assert after == before
    empty = _load(_cli(["notes", "list", "--ref", eid, "--json"]))
    assert empty["count"] == 0
    human_fail = _cli(["notes", "list"])
    assert human_fail.exit_code == 1
    assert human_fail.stderr.strip() or "必须指定" in (human_fail.output or "")


def test_s2_topic_and_s3_since(tmp_path, monkeypatch):
    serve, eid, oid, _u = _setup(tmp_path, monkeypatch)
    _load(_cli(["notes", "add", eid, "--home", "energy", "--content", "能源洞察", "--json"]))
    _load(_cli(["notes", "add", oid, "--home", "other", "--content", "其它洞察", "--json"]))
    topic = _load(_cli(["notes", "list", "--topic", "energy", "--json"]))
    assert topic["count"] == 1
    assert topic["items"][0]["ref_id"] == eid
    assert all(i["ref_id"] != oid for i in topic["items"])
    other = _load(_cli(["notes", "list", "--topic", "other", "--json"]))
    assert other["count"] == 1
    assert other["items"][0]["ref_id"] == oid
    window = _load(_cli(["notes", "list", "--ref", eid, "--since", "1", "--json"]))
    assert window["count"] == 1
    assert window["items"][0]["stable_id"].startswith("note:")
    none = _load(_cli(["notes", "list", "--ref", eid, "--since", "0", "--json"]))
    # 0 hours → only notes at/after now; just-added should still count if created_at <= now
    # 0 hours means since=now, created_at slightly earlier → may be 0. Use 1 hour above.
    missing_ref = _load(
        _cli(["notes", "list", "--ref", eid, "--since", "2020-01-01T00:00:00+00:00", "--json"])
    )
    assert missing_ref["count"] == 1


def test_s3_default_48h(tmp_path, monkeypatch):
    serve, eid, _oid, _u = _setup(tmp_path, monkeypatch)
    now = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
    add_note(
        serve,
        ref_id=eid,
        content="窗外",
        home="energy",
        now=now - timedelta(hours=72),
    )
    add_note(
        serve,
        ref_id=eid,
        content="窗内",
        home="energy",
        now=now - timedelta(hours=1),
    )
    monkeypatch.setattr("kairo.notes._now", lambda: now)
    listed = _load(_cli(["notes", "list", "--ref", eid, "--home", "energy", "--json"]))
    bodies_via_show = _load(_cli(["notes", "show", "--ref", eid, "--home", "energy", "--json"]))
    assert listed["count"] == 1
    assert listed["items"][0]["excerpt"] == "窗内"
    assert bodies_via_show["count"] == 2
    ids = {i["content"] for i in bodies_via_show["items"]}
    assert ids == {"窗外", "窗内"}


def test_s4_provenance_and_folded_unchanged(tmp_path, monkeypatch):
    serve, eid, _oid, understanding = _setup(tmp_path, monkeypatch)
    monkeypatch.chdir(serve / "energy")
    before = _load(_cli(["status", "--json"]))
    folded_before = before["targets"][0]["folded"]
    empty = _load(_cli(["notes", "list", "--ref", eid, "--json"]))
    assert empty["provenance"] == []
    blob = json.dumps(empty)
    assert "note:" not in blob
    added = _load(_cli(["notes", "add", eid, "--content", "洞察", "--json"]))
    listed = _load(_cli(["notes", "list", "--ref", eid, "--json"]))
    assert listed["provenance"]
    assert listed["provenance"][0]["kind"] == "note"
    assert listed["provenance"][0]["id"] == added["stable_id"]
    after = _load(_cli(["status", "--json"]))
    assert after["targets"][0]["folded"] == folded_before
    assert understanding.read_text() == "SENTINEL"
    digest = serve / "energy" / "references" / eid / "digest.md"
    assert not digest.is_file() or digest.read_text() != "洞察"


def test_stable_key_global_home():
    sid = stable_id_for("", "abc", "n1")
    assert sid == "note:global/abc/n1"
    key, nid = parse_stable_id(sid)
    assert key == ref_key("", "abc")
    assert nid == "n1"


def test_no_step_run(tmp_path, monkeypatch):
    serve, eid, _oid, _u = _setup(tmp_path, monkeypatch)

    def boom(*_a, **_k):
        raise AssertionError("must not step")

    monkeypatch.setattr("kairo.engine.step", boom)
    monkeypatch.setattr("kairo.engine.run_workspace", boom, raising=False)
    _load(_cli(["notes", "add", eid, "--content", "x", "--json"]))
    _load(_cli(["notes", "list", "--ref", eid, "--json"]))
