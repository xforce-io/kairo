"""#242–#247 全局 Ref / Tag / Topic / Project 闭环。走 shipped CLI、API、HTML。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from kairo.cli import app
from kairo.engine import step
from kairo.models import State, TargetState
from kairo.provider import StubProvider
from kairo.refs import (
    add_global_ref,
    add_tag,
    create_tag,
    list_all_refs,
    migrate_tag_rules,
    run_ref_ids,
    set_include_tags,
    timeline_digest_path,
    topic_members,
)
from kairo.rules import ComposeRule
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

    create_tag(serve, "energy")
    create_tag(serve, "policy")
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
    assert 'href="/refs/loose-note?home=global&back=/timeline%3Fmode%3Drecent"' in timeline.text
    assert "energy" in timeline.text
    tagged_html = client.get("/timeline", params={"mode": "recent", "tag": "energy"})
    assert "loose-note" in tagged_html.text
    opened = client.get("/refs/loose-note", params={"back": "/timeline?mode=recent"})
    assert opened.status_code == 200
    assert "loose-note" in opened.text
    assert "energy" in opened.text
    assert "Related topics" in opened.text
    assert 'href="/timeline?mode=recent"' in opened.text

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
    create_tag(serve, "energy")
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


def test_tag_vocabulary_delete_protection_and_strict_migration(tmp_path):
    serve = tmp_path / "root"
    serve.mkdir()
    ws = Workspace.init(serve / "energy", topic="能源")
    source = tmp_path / "note.txt"
    source.write_text("note", encoding="utf-8")
    rid = ws.add([source], ref_id="local")
    create_tag(serve, "能源")
    create_tag(serve, "政策")
    add_tag(serve, home="energy", ref_id=rid, tag="政策")
    set_include_tags(serve, "energy", ["政策"])
    from kairo.refs import delete_tag, topic_members

    with __import__("pytest").raises(Exception):
        delete_tag(serve, "能源")
    with __import__("pytest").raises(Exception):
        delete_tag(serve, "政策")

    evidence = _backup_evidence(tmp_path)
    legacy_state = ws.read_state()
    legacy_state.targets["understanding.md"] = TargetState(
        folded={f"references/{rid}/digest.md": "digest-hash"},
        last_major_folded={f"references/{rid}/digest.md": "digest-hash"},
    )
    ws.write_state(legacy_state)
    report = migrate_tag_rules(serve, evidence, dry_run=True)
    assert report["dry_run"] is True
    migrated = migrate_tag_rules(serve, evidence)
    assert migrated["dry_run"] is False
    assert {item.id for item in topic_members(serve, "energy")} == {rid}
    assert Workspace.open(serve / "energy").constitution.include_tags == ["政策"]
    migrated_state = Workspace.open(serve / "energy").read_state()
    assert migrated_state.targets["understanding.md"].folded == {"energy/local": "digest-hash"}
    assert migrated_state.targets["understanding.md"].last_major_folded == {"energy/local": "digest-hash"}


def _backup_evidence(tmp_path: Path) -> Path:
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "remote": "jms-115",
                "snapshot_path": "generations/b-20260903T143304Z-5a68d1308a8d",
                "backup_id": "b-20260903T143304Z-5a68d1308a8d",
                "created_at": "2026-09-03T22:33:04+08:00",
                "manifest_sha256": "f7f9b55c8e32a910e0eb07b7c86f3c53c3989026d3d9cdcf550c8434b8c7a4a4",
                "verified_at": "2026-09-04T00:00:00+08:00",
                "restored": True,
                "restored_root": "/srv/kairo/restore-check-252-b-20260903T143304Z",
            }
        ),
        encoding="utf-8",
    )
    return evidence


def test_strict_topic_folds_cross_home_digest_by_ref_identity(tmp_path):
    serve = tmp_path / "root"
    serve.mkdir()
    target = Workspace.init(serve / "target", topic="目标")
    source = Workspace.init(serve / "source", topic="来源")
    for tag in ("目标", "来源", "政策"):
        create_tag(serve, tag)
    source_file = tmp_path / "source.txt"
    source_file.write_text("原始材料", encoding="utf-8")
    ref_id = source.add([source_file], ref_id="shared")
    digest = source.references_dir() / ref_id / "digest.md"
    digest.write_text("跨来源唯一纪要", encoding="utf-8")
    add_tag(serve, home="source", ref_id=ref_id, tag="政策")
    set_include_tags(serve, "target", ["政策"])
    migrate_tag_rules(serve, _backup_evidence(tmp_path))

    state = State()
    items = ComposeRule(target, StubProvider()).discover(state)
    assert [item.key for item in items] == ["understanding.md"]
    items[0].run(state)

    understanding = (target.root / "understanding.md").read_text(encoding="utf-8")
    assert "跨来源唯一纪要" in understanding
    assert "source/shared" in state.targets["understanding.md"].folded
    assert not (target.references_dir() / ref_id).exists()
    assert digest.read_text(encoding="utf-8") == "跨来源唯一纪要"


def test_strict_topic_generates_missing_cross_home_digest_before_fold(tmp_path):
    serve = tmp_path / "root"
    serve.mkdir()
    target = Workspace.init(serve / "target", topic="目标")
    source = Workspace.init(serve / "source", topic="来源")
    for tag in ("目标", "来源", "政策"):
        create_tag(serve, tag)
    source_file = tmp_path / "source.txt"
    source_file.write_text("跨来源原始正文", encoding="utf-8")
    ref_id = source.add([source_file], ref_id="missing-digest")
    add_tag(serve, home="source", ref_id=ref_id, tag="政策")
    set_include_tags(serve, "target", ["政策"])
    migrate_tag_rules(serve, _backup_evidence(tmp_path))

    assert step(target, StubProvider()) is True
    digest = source.references_dir() / ref_id / "digest.md"
    assert digest.is_file()
    assert "跨来源原始正文" in digest.read_text(encoding="utf-8")
    state = target.read_state()
    assert "source/missing-digest" in state.products
    assert "source/missing-digest" in state.targets["understanding.md"].folded
    assert not (target.references_dir() / ref_id).exists()
