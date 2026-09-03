"""#242–#247 全局 Ref / Tag / Topic / Project 闭环。走 shipped CLI、API、HTML。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from kairo.cli import app
from kairo.refs import (
    add_global_ref,
    add_tag,
    list_all_refs,
    run_ref_ids,
    set_include_tags,
    timeline_digest_path,
    topic_members,
)
from kairo.web.server import create_app
from kairo.workspace import Workspace

runner = CliRunner()


def _cli(args, cwd: Path, monkeypatch):
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(cwd))
    return runner.invoke(app, args)


def _load(result):
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_global_ref_tag_topic_project_cli_api_html(tmp_path, monkeypatch):
    serve = tmp_path / "root"
    serve.mkdir()
    topic_a = Workspace.init(serve / "energy", topic="综合能源研究")
    understanding = topic_a.root / "understanding.md"
    understanding.write_text("历史结论", encoding="utf-8")
    src_home = tmp_path / "meet.txt"
    src_home.write_text("会议原文", encoding="utf-8")
    home_id = topic_a.add([src_home], ref_id="2026-09-01-meet")
    home_dir = topic_a.references_dir() / home_id

    src_new = tmp_path / "note.txt"
    src_new.write_text("未归档笔记", encoding="utf-8")
    monkeypatch.chdir(serve)
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(serve))

    added = runner.invoke(app, ["add", str(src_new), "--id", "loose-note"])
    assert added.exit_code == 0, added.output
    assert "added loose-note" in added.output
    global_dir = serve / ".kairo" / "global-home" / "references" / "loose-note"
    assert global_dir.is_dir()
    assert home_dir.is_dir()
    assert not (topic_a.references_dir() / "loose-note").exists()

    tl = _load(_cli(["timeline", "--json"], serve, monkeypatch))
    ids = {row["id"] for row in tl}
    assert "2026-09-01-meet" in ids
    assert "loose-note" in ids
    loose = next(row for row in tl if row["id"] == "loose-note")
    assert loose["workspace"] == "global"
    assert loose["home"] == ""
    assert loose["tags"] == []

    tagged = _load(
        _cli(["tag", "add", "loose-note", "energy", "--json"], serve, monkeypatch)
    )
    assert "energy" in tagged["tags"]
    _cli(["tag", "add", "loose-note", "policy", "--json"], serve, monkeypatch)

    members_before = topic_members(serve, "energy")
    assert {m.id for m in members_before} == {home_id}

    monkeypatch.chdir(topic_a.root)
    included = _load(_cli(["include", "set", "energy", "--json"], topic_a.root, monkeypatch))
    assert included["include_tags"] == ["energy"]
    members = topic_members(serve, "energy")
    assert {m.id for m in members} == {"loose-note"}
    assert all(m.dir != home_dir or m.id != "loose-note" for m in members)
    digest_dirs = [p for p in (serve / ".kairo" / "global-home" / "references").iterdir() if p.is_dir()]
    assert len(digest_dirs) == 1

    monkeypatch.chdir(serve)
    filtered = _load(_cli(["timeline", "--tag", "energy", "--json"], serve, monkeypatch))
    assert {row["id"] for row in filtered} == {"loose-note"}
    both = _load(
        _cli(["timeline", "--tag", "energy", "--tag", "policy", "--json"], serve, monkeypatch)
    )
    assert {row["id"] for row in both} == {"loose-note"}
    _load(_cli(["tag", "rm", "loose-note", "policy", "--json"], serve, monkeypatch))
    assert global_dir.is_dir()

    created = _load(_cli(["project", "create", "综合能源"], serve, monkeypatch))
    pid = created["id"]
    linked = _load(_cli(["project", "link", pid, "energy"], serve, monkeypatch))
    assert linked["workspace_slugs"] == ["energy"]
    assert linked["topic_slugs"] == ["energy"]

    client = TestClient(create_app(serve))
    api_refs = client.get("/api/refs").json()
    assert api_refs["ok"] is True
    assert {r["id"] for r in api_refs["refs"]} >= {"loose-note", home_id}

    inc = client.get("/api/topics/energy/include").json()
    assert inc["ok"] is True
    assert inc["include_tags"] == ["energy"]
    assert {m["id"] for m in inc["members"]} == {"loose-note"}

    dash = client.get("/")
    assert dash.status_code == 200
    assert "Topics" in dash.text
    assert "Workspaces" not in dash.text

    timeline = client.get("/timeline", params={"mode": "recent"})
    assert timeline.status_code == 200
    assert "loose-note" in timeline.text
    tagged_html = client.get("/timeline", params={"mode": "recent", "tag": "energy"})
    assert "loose-note" in tagged_html.text
    opened = client.get("/refs/loose-note")
    assert opened.status_code == 200
    assert "loose-note" in opened.text
    assert "note.txt" in opened.text or "form" in opened.text

    alias = client.get("/topics/energy", follow_redirects=False)
    assert alias.status_code == 303
    assert "/w/energy" in alias.headers["location"]
    topic_page = client.get("/w/energy")
    assert topic_page.status_code == 200
    assert "loose-note" in topic_page.text
    assert 'href="/refs/loose-note"' in topic_page.text
    assert client.get("/refs/loose-note").status_code == 200
    assert "Data sources" not in topic_page.text
    assert home_id not in run_ref_ids(topic_a)
    assert run_ref_ids(topic_a) == []
    assert understanding.read_text(encoding="utf-8") == "历史结论"

    proj = client.get(f"/projects/{pid}")
    assert proj.status_code == 200
    assert "loose-note" in proj.text
    assert "Topics" in proj.text

    cleared = client.put("/api/topics/energy/include", json={"include_tags": []}).json()
    assert cleared["include_tags"] == []
    assert cleared["members"] == []
    assert understanding.is_file()
    assert global_dir.is_dir()
    assert home_dir.is_dir()

    unlinked = _load(_cli(["project", "unlink", pid, "energy"], serve, monkeypatch))
    assert unlinked["workspace_slugs"] == []
    assert (serve / "energy" / "constitution.yaml").is_file()


def test_corpus_appears_on_timeline(tmp_path):
    root = tmp_path / "root"
    a = root / "alpha"
    a.mkdir(parents=True)
    wa = Workspace.init(a, topic="A")
    (tmp_path / "c.txt").write_text("c")
    wa.add([tmp_path / "c.txt"], ref_id="base", source_class="corpus")
    from kairo.timeline import scan_timeline

    ids = {it.id for it in scan_timeline(root)}
    assert "base" in ids


def test_untagged_global_ref_not_in_topic(tmp_path):
    serve = tmp_path / "root"
    serve.mkdir()
    Workspace.init(serve / "t1", topic="T")
    src = tmp_path / "x.txt"
    src.write_text("x")
    add_global_ref(serve, [src], ref_id="orphan")
    set_include_tags(serve, "t1", ["energy"])
    assert topic_members(serve, "t1") == []
    assert any(r.id == "orphan" for r in list_all_refs(serve))
    add_tag(serve, home="", ref_id="orphan", tag="energy")
    assert {m.id for m in topic_members(serve, "t1")} == {"orphan"}
    csrc = tmp_path / "c.txt"
    csrc.write_text("c")
    add_global_ref(serve, [csrc], ref_id="g-corpus", source_class="corpus")
    add_tag(serve, home="", ref_id="g-corpus", tag="energy")
    page = TestClient(create_app(serve)).get("/w/t1")
    assert 'href="/refs/orphan"' in page.text
    assert 'href="/refs/g-corpus"' in page.text
    digest = timeline_digest_path(serve, "", "orphan")
    digest.parent.mkdir(parents=True, exist_ok=True)
    digest.write_text("纪要", encoding="utf-8")
    assert digest.is_file()
    ws = Workspace.open(serve / "t1")
    assert run_ref_ids(ws) == []
