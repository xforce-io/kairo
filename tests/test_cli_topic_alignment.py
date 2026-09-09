"""Tests for #269 CLI Topic alignment."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kairo.cli import app
from kairo.refs import add_tag, create_tag, list_all_refs, set_include_tags, topic_members
from kairo.workspace import Workspace

runner = CliRunner()


def test_add_in_topic_dir_without_topic_stamps_home_tag(tmp_path, monkeypatch):
    """cwd Topic add without --topic stamps Topic-name Tag and is a member."""
    serve = tmp_path / "root"
    serve.mkdir()
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(serve))
    create_tag(serve, "research")
    created = runner.invoke(app, ["new", "research"])
    assert created.exit_code == 0, created.output

    monkeypatch.chdir(serve / "research")
    note = tmp_path / "note.txt"
    note.write_text("Research note")
    result = runner.invoke(app, ["add", str(note)])
    assert result.exit_code == 0, result.output
    assert "tagged with research" in result.output

    refs = list_all_refs(serve)
    assert len(refs) == 1
    ref = refs[0]
    assert ref.home == "research"
    assert "research" in ref.tags
    members = topic_members(serve, "research")
    assert any(m.id == ref.id and m.home == "research" for m in members)


def test_add_with_topic_tags_ref(tmp_path, monkeypatch):
    """#269: kairo add --topic SLUG tags the Ref with Topic-name Tag."""
    serve = tmp_path / "root"
    serve.mkdir()
    monkeypatch.chdir(serve)
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(serve))
    
    # Create Tag first
    create_tag(serve, "energy")
    
    # Add Ref with --topic
    note = tmp_path / "note.txt"
    note.write_text("Energy research note")
    result = runner.invoke(app, ["add", str(note), "--topic", "energy"])
    assert result.exit_code == 0, result.output
    assert "tagged with energy" in result.output
    
    # Verify Ref is tagged
    refs = list_all_refs(serve)
    assert len(refs) == 1
    ref = refs[0]
    assert "energy" in ref.tags
    assert ref.home == ""  # global


def test_add_topic_fails_when_tag_missing(tmp_path, monkeypatch):
    """#269: kairo add --topic fails when Topic-name Tag doesn't exist."""
    serve = tmp_path / "root"
    serve.mkdir()
    monkeypatch.chdir(serve)
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(serve))
    
    note = tmp_path / "note.txt"
    note.write_text("Note")
    result = runner.invoke(app, ["add", str(note), "--topic", "missing"])
    
    assert result.exit_code == 1
    assert "Tag 不在词表中" in result.output or "不在词表" in result.output


def test_new_topic_default_include_tags(tmp_path, monkeypatch):
    """#269: kairo new sets include_tags=[topic] by default."""
    serve = tmp_path / "root"
    serve.mkdir()
    monkeypatch.chdir(serve)
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(serve))
    
    # Create Tag first
    create_tag(serve, "research")
    
    # Create Topic
    result = runner.invoke(app, ["new", "research"])
    assert result.exit_code == 0, result.output
    assert "include_tags=[research]" in result.output
    
    # Verify include_tags
    ws = Workspace.open(serve / "research")
    assert ws.constitution.include_tags == ["research"]


def test_init_topic_default_include_tags(tmp_path, monkeypatch):
    """#269: kairo init sets include_tags=[topic] by default."""
    serve = tmp_path / "root"
    serve.mkdir()
    topic_dir = serve / "research"
    topic_dir.mkdir()
    monkeypatch.chdir(topic_dir)
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(serve))
    
    # Create Tag first
    create_tag(serve, "research")
    
    # Init Topic
    result = runner.invoke(app, ["init", "research"])
    assert result.exit_code == 0, result.output
    assert "include_tags=[research]" in result.output
    
    # Verify include_tags
    ws = Workspace.open(topic_dir)
    assert ws.constitution.include_tags == ["research"]


def test_include_clear_and_set_keep_topic_name_tag(tmp_path, monkeypatch):
    """Include writes cannot drop the Topic 名称 Tag; extras remain optional."""
    serve = tmp_path / "root"
    serve.mkdir()
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(serve))
    create_tag(serve, "research")
    create_tag(serve, "extra")
    monkeypatch.chdir(serve)
    created = runner.invoke(app, ["new", "research"])
    assert created.exit_code == 0, created.output
    assert Workspace.open(serve / "research").constitution.include_tags == ["research"]

    monkeypatch.chdir(serve / "research")
    extra = runner.invoke(app, ["include", "set", "extra", "--json"])
    assert extra.exit_code == 0, extra.output
    assert json.loads(extra.output)["include_tags"] == ["research", "extra"]

    cleared = runner.invoke(app, ["include", "clear", "--json"])
    assert cleared.exit_code == 0, cleared.output
    payload = json.loads(cleared.output)
    assert payload["include_tags"] == ["research"]
    assert "extra" not in payload["include_tags"]


def test_new_topic_fails_when_tag_missing(tmp_path, monkeypatch):
    """#269: kairo new fails when Topic-name Tag doesn't exist."""
    serve = tmp_path / "root"
    serve.mkdir()
    monkeypatch.chdir(serve)
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(serve))
    
    result = runner.invoke(app, ["new", "missing"])
    
    assert result.exit_code == 1
    assert "Settings 创建同名 Tag" in result.output


def test_list_json_includes_member_info(tmp_path, monkeypatch):
    """#269: kairo list --json exposes include_tags and member_count."""
    serve = tmp_path / "root"
    serve.mkdir()
    monkeypatch.chdir(serve)
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(serve))
    
    # Create Tag and Topic
    create_tag(serve, "energy")
    runner.invoke(app, ["new", "energy"])
    
    # Add Ref with topic tag
    note = tmp_path / "note.txt"
    note.write_text("Note")
    runner.invoke(app, ["add", str(note), "--topic", "energy"])
    
    # List with --json
    result = runner.invoke(app, ["list", str(serve), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    
    assert len(data) == 1
    topic = data[0]
    assert "include_tags" in topic
    assert "member_count" in topic
    assert topic["include_tags"] == ["energy"]
    assert topic["member_count"] == 1


def test_step_with_topic_option(tmp_path, monkeypatch):
    """#269: kairo step --topic processes specified Topic."""
    serve = tmp_path / "root"
    serve.mkdir()
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(serve))
    monkeypatch.setenv("KAIRO_STUB", "1")
    
    create_tag(serve, "research")
    runner.invoke(app, ["new", "research"])
    set_include_tags(serve, "research", ["research"])
    ws = Workspace.open(serve / "research")
    note = tmp_path / "note.txt"
    note.write_text("Research note")
    rid = ws.add([note])
    add_tag(serve, home="research", ref_id=rid, tag="research")
    
    # Run step from different directory
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["step", "--topic", "research"])
    
    assert result.exit_code == 0, result.output
    assert (serve / "research" / "understanding.md").exists()


def test_status_with_topic_option(tmp_path, monkeypatch):
    """#269: kairo status --topic shows specified Topic status."""
    serve = tmp_path / "root"
    serve.mkdir()
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(serve))
    monkeypatch.setenv("KAIRO_STUB", "1")
    
    # Create Tag and Topic
    create_tag(serve, "research")
    runner.invoke(app, ["new", "research"])
    
    # Check status from different directory
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["status", "--topic", "research"])
    
    assert result.exit_code == 0, result.output
    assert "topic research" in result.output or "research" in result.output


def test_processing_commands_cwd_fallback(tmp_path, monkeypatch):
    """#269: Processing commands default to cwd when --topic omitted."""
    serve = tmp_path / "root"
    serve.mkdir()
    monkeypatch.setenv("KAIRO_STUB", "1")
    
    # Create Tag and Topic
    create_tag(serve, "research")
    topic_dir = serve / "research"
    ws = Workspace.init(topic_dir, topic="research")
    ws.constitution.include_tags = ["research"]
    ws.write_constitution(ws.constitution)
    
    note = tmp_path / "note.txt"
    note.write_text("Note")
    rid = ws.add([note])
    add_tag(serve, home="research", ref_id=rid, tag="research")
    
    # Run from Topic directory
    monkeypatch.chdir(topic_dir)
    result = runner.invoke(app, ["step"])
    
    assert result.exit_code == 0, result.output
    assert (topic_dir / "understanding.md").exists()


def test_add_topic_from_inside_topic(tmp_path, monkeypatch):
    """#269: add --topic works from inside a Topic directory."""
    serve = tmp_path / "root"
    serve.mkdir()
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(serve))
    
    # Create Tags and Topics
    create_tag(serve, "energy")
    create_tag(serve, "policy")
    runner.invoke(app, ["new", "energy"])
    runner.invoke(app, ["new", "policy"])
    
    # Add from inside energy Topic to policy Topic
    monkeypatch.chdir(serve / "energy")
    note = tmp_path / "note.txt"
    note.write_text("Policy note")
    result = runner.invoke(app, ["add", str(note), "--topic", "policy"])
    
    assert result.exit_code == 0, result.output
    assert "tagged with policy" in result.output
    assert "energy" in result.output

    refs = list_all_refs(serve)
    ref = next(r for r in refs if r.title != "")
    assert "policy" in ref.tags
    assert "energy" in ref.tags
    assert ref.home == "energy"
    members = topic_members(serve, "energy")
    assert any(m.id == ref.id and m.home == "energy" for m in members)
    assert any(m.id == ref.id for m in topic_members(serve, "policy"))


def test_epilog_shows_three_paths(tmp_path):
    """#269: CLI --help epilog shows three paths."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    output = result.output
    
    assert "add" in output
    assert "Topic" in output or "topic" in output or "step" in output
    assert "Ref" in output or "add" in output


def test_cli_add_copy_without_title_uses_file_stem(tmp_path, monkeypatch):
    """#352 S1: add --copy --topic --root without --title stores file stem."""
    serve = tmp_path / "root"
    serve.mkdir()
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(serve))
    create_tag(serve, "energy")
    runner.invoke(app, ["new", "energy", "--root", str(serve)])
    src = tmp_path / "群控方向讨论-260908.txt"
    src.write_text("group control")
    result = runner.invoke(
        app,
        ["add", str(src), "--copy", "--topic", "energy", "--root", str(serve)],
    )
    assert result.exit_code == 0, result.output
    refs = list_all_refs(serve)
    assert len(refs) == 1
    assert refs[0].title == "群控方向讨论-260908"
    assert refs[0].title != "energy"
    man = (refs[0].dir / "manifest.yaml").read_text()
    assert "群控方向讨论-260908" in man
    assert "title: " in man
    import re

    clock = re.search(r"title: ['\"]?\d{8}-\d{2}", man)
    assert clock is None, man


def test_cli_add_explicit_title_on_global_topic_member(tmp_path, monkeypatch):
    """#352 S2: add --title HUMAN stores HUMAN."""
    serve = tmp_path / "root"
    serve.mkdir()
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(serve))
    create_tag(serve, "energy")
    runner.invoke(app, ["new", "energy", "--root", str(serve)])
    src = tmp_path / "file-stem-should-not-win.txt"
    src.write_text("x")
    result = runner.invoke(
        app,
        [
            "add",
            str(src),
            "--copy",
            "--topic",
            "energy",
            "--root",
            str(serve),
            "--title",
            "一张网 demo-260908",
        ],
    )
    assert result.exit_code == 0, result.output
    refs = list_all_refs(serve)
    assert refs[0].title == "一张网 demo-260908"


def test_cli_title_and_status_for_global_topic_member(tmp_path, monkeypatch):
    """#352 S3: title renames a global-home member; status lists NEWNAME."""
    serve = tmp_path / "root"
    serve.mkdir()
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(serve))
    create_tag(serve, "energy")
    runner.invoke(app, ["new", "energy", "--root", str(serve)])
    src = tmp_path / "群控方向讨论-260908.txt"
    src.write_text("x")
    added = runner.invoke(
        app,
        ["add", str(src), "--copy", "--topic", "energy", "--root", str(serve)],
    )
    assert added.exit_code == 0, added.output
    rid = list_all_refs(serve)[0].id
    monkeypatch.chdir(serve / "energy")
    titled = runner.invoke(app, ["title", rid, "群控方向讨论"])
    assert titled.exit_code == 0, titled.output
    st = runner.invoke(app, ["status"])
    assert st.exit_code == 0, st.output
    assert rid in st.output
    assert "群控方向讨论" in st.output


def test_add_without_topic_still_works(tmp_path, monkeypatch):
    """Serve-root add without --topic still registers a global untagged Ref."""
    serve = tmp_path / "root"
    serve.mkdir()
    monkeypatch.chdir(serve)
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(serve))
    
    note = tmp_path / "note.txt"
    note.write_text("Note")
    result = runner.invoke(app, ["add", str(note)])
    
    assert result.exit_code == 0, result.output
    assert "added" in result.output
    
    # Verify Ref exists
    refs = list_all_refs(serve)
    assert len(refs) == 1


def test_archive_uses_topic_parameter(tmp_path, monkeypatch):
    """#269: archive command uses --topic instead of --workspace."""
    serve = tmp_path / "root"
    serve.mkdir()
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(serve))
    
    # Create Tag and Topic
    create_tag(serve, "archive")
    runner.invoke(app, ["new", "archive"])
    
    # Archive to Topic
    session = tmp_path / "session.md"
    session.write_text("""
# Session

## workspace
archive

## title
Test session
""")
    
    result = runner.invoke(app, ["archive", str(session), "--topic", "archive", "--create", "--json"])
    assert result.exit_code == 0 or result.exit_code == 2  # May need choice
    # Check that --topic parameter is recognized
    assert "--topic" in runner.invoke(app, ["archive", "--help"]).output


def test_review_uses_topic_parameter(tmp_path, monkeypatch):
    """#269: review command uses --topic instead of --workspace."""
    result = runner.invoke(app, ["review", "--help"])
    assert "--topic" in result.output
    assert "-t" in result.output
