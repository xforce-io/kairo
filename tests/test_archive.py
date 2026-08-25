"""#136: coding agent 会话归档 — 回执、续接、分叉、manifest 单点提交。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kairo.cli import app
from kairo.workspace import Workspace

runner = CliRunner()


def _envelope(key, workspace, reference, form_index, version, body_sha256) -> str:
    payload = (
        f"KAIRO_ARCHIVE/1 {key} {workspace} {reference} "
        f"{form_index} {version} {body_sha256}"
    )
    return (
        f'<KAIRO_ARCHIVE_RECEIPT preserve="verbatim">{payload}'
        f"</KAIRO_ARCHIVE_RECEIPT>"
    )


def _serve_ws(tmp_path: Path, slug: str = "topic-a") -> Path:
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    dest = root / slug
    dest.mkdir()
    Workspace.init(dest, topic=slug)
    return root


def test_strip_keeps_fenced_and_illegal_envelopes():
    from kairo.archive import session_body

    legal = _envelope("a" * 32, "ws", "ref", 0, 1, "b" * 64)
    fenced = f"before\n```\n{legal}\n```\nquoted\n> {legal}\nend"
    body = session_body(fenced)
    assert legal in body
    assert "before" in body and "end" in body
    assert "<KAIRO_ARCHIVE_RECEIPT" in body


def test_strip_removes_only_top_level_complete_envelope():
    from kairo.archive import session_body

    legal = _envelope("a" * 32, "ws", "ref", 0, 1, "b" * 64)
    text = f"hello\n{legal}\nworld"
    assert session_body(text) == "hello\nworld"


def test_last_valid_receipt_scans_from_end(tmp_path):
    from kairo.archive import ArchiveReceipt, last_valid_receipt

    root = _serve_ws(tmp_path)
    ws = Workspace.open(root / "topic-a")
    body = "session-one"
    rec = _create_archive(root, body, workspace="topic-a")
    older = _envelope("c" * 32, "topic-a", "nope", 0, 1, "d" * 64)
    text = f"{body}\n{older}\n{rec.envelope()}\n"
    found = last_valid_receipt(text, serve_root=root)
    assert found is not None
    assert found.key == rec.key
    assert found.version == 1


def test_cli_first_archive_requires_workspace(tmp_path, monkeypatch):
    root = _serve_ws(tmp_path)
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(root))
    src = tmp_path / "s.md"
    src.write_text("hello session")
    result = runner.invoke(app, ["archive", str(src)])
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["reason"] == "need-workspace"
    assert any(w["slug"] == "topic-a" for w in payload["workspaces"])
    assert list((root / "topic-a" / "references").glob("*")) == []


def test_cli_first_archive_create_writes_one_form(tmp_path, monkeypatch):
    root = _serve_ws(tmp_path)
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(root))
    src = tmp_path / "s.md"
    src.write_text("hello session")
    result = runner.invoke(
        app, ["archive", str(src), "--workspace", "topic-a", "--create"]
    )
    assert result.exit_code == 0
    line = result.stdout.strip()
    assert line.startswith("<KAIRO_ARCHIVE_RECEIPT preserve=\"verbatim\">")
    ws = Workspace.open(root / "topic-a")
    ids = ws.list_reference_ids()
    assert len(ids) == 1
    man = ws.read_manifest(ids[0])
    assert man.source_class == "stream"
    assert man.archive is not None
    assert man.archive.version == 1
    assert man.archive.form_index == 0
    assert len(man.forms) == 1
    assert man.forms[0].role == "source_text"
    loc = Path(man.forms[0].location)
    p = loc if loc.is_absolute() else ws.root / loc
    assert p.name.startswith("session.") and p.name.endswith(".md")
    assert p.read_text() == "hello session"


def test_cli_continue_updates_same_form(tmp_path, monkeypatch):
    root = _serve_ws(tmp_path)
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(root))
    rec = _create_archive(root, "hello session", workspace="topic-a")
    src = tmp_path / "s2.md"
    src.write_text(f"hello session\n{rec.envelope()}\nmore talk\n")
    result = runner.invoke(app, ["archive", str(src)])
    assert result.exit_code == 0
    ws = Workspace.open(root / "topic-a")
    ids = ws.list_reference_ids()
    assert len(ids) == 1
    man = ws.read_manifest(ids[0])
    assert len(man.forms) == 1
    assert man.archive.version == 2
    loc = Path(man.forms[0].location)
    p = loc if loc.is_absolute() else ws.root / loc
    assert p.read_text() == "hello session\nmore talk"


def test_cli_idempotent_same_body(tmp_path, monkeypatch):
    root = _serve_ws(tmp_path)
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(root))
    rec = _create_archive(root, "hello session", workspace="topic-a")
    src = tmp_path / "s.md"
    src.write_text(f"hello session\n{rec.envelope()}\n")
    result = runner.invoke(app, ["archive", str(src)])
    assert result.exit_code == 0
    assert result.stdout.strip() == rec.envelope()
    ws = Workspace.open(root / "topic-a")
    man = ws.read_manifest(ws.list_reference_ids()[0])
    assert man.archive.version == 1


def test_cli_fork_does_not_write(tmp_path, monkeypatch):
    root = _serve_ws(tmp_path)
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(root))
    rec = _create_archive(root, "hello session", workspace="topic-a")
    src = tmp_path / "s.md"
    src.write_text(f"rewritten history\n{rec.envelope()}\nnew\n")
    result = runner.invoke(app, ["archive", str(src)])
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["reason"] == "fork"
    ws = Workspace.open(root / "topic-a")
    man = ws.read_manifest(ws.list_reference_ids()[0])
    assert man.archive.version == 1
    loc = Path(man.forms[0].location)
    p = loc if loc.is_absolute() else ws.root / loc
    assert p.read_text() == "hello session"


def test_cli_fork_bind_overwrites(tmp_path, monkeypatch):
    root = _serve_ws(tmp_path)
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(root))
    rec = _create_archive(root, "hello session", workspace="topic-a")
    src = tmp_path / "s.md"
    src.write_text(f"rewritten history\n{rec.envelope()}\n")
    result = runner.invoke(
        app,
        [
            "archive",
            str(src),
            "--workspace",
            "topic-a",
            "--bind",
            rec.reference,
        ],
    )
    assert result.exit_code == 0
    ws = Workspace.open(root / "topic-a")
    man = ws.read_manifest(rec.reference)
    assert man.archive.version == 2
    assert len(man.forms) == 1
    loc = Path(man.forms[0].location)
    p = loc if loc.is_absolute() else ws.root / loc
    assert p.read_text() == "rewritten history"


def test_corrupt_receipt_ids_are_not_receipts_in_body():
    from kairo.archive import session_body

    bad = (
        '<KAIRO_ARCHIVE_RECEIPT preserve="verbatim">'
        "KAIRO_ARCHIVE/1 not-hex ws ref 0 1 "
        + "b" * 64
        + "</KAIRO_ARCHIVE_RECEIPT>"
    )
    text = f"keep\n{bad}\nme"
    assert session_body(text) == f"keep\n{bad}\nme"


def test_manifest_commit_is_the_visibility_point(tmp_path, monkeypatch):
    from kairo import archive as archive_mod

    root = _serve_ws(tmp_path)
    rec = _create_archive(root, "hello session", workspace="topic-a")
    real_replace = archive_mod.os.replace
    calls = {"n": 0}

    def boom(src, dst):
        calls["n"] += 1
        if Path(dst).name == "manifest.yaml":
            raise OSError("simulated crash before manifest commit")
        return real_replace(src, dst)

    monkeypatch.setattr(archive_mod.os, "replace", boom)
    with pytest.raises(OSError):
        archive_mod.archive_markdown(
            f"hello session\n{rec.envelope()}\nmore\n",
            serve_root=root,
            workspace=None,
            create=False,
            bind=None,
            title=None,
        )
    ws = Workspace.open(root / "topic-a")
    man = ws.read_manifest(rec.reference)
    assert man.archive.version == 1
    loc = Path(man.forms[0].location)
    p = loc if loc.is_absolute() else ws.root / loc
    assert p.read_text() == "hello session"
    orphans = [
        f
        for f in (ws.references_dir() / rec.reference).glob("session.*.md")
        if f.read_text() == "hello session\nmore"
    ]
    assert orphans, "uncommitted new body may remain as an unreferenced file"


def _create_archive(root: Path, body: str, *, workspace: str):
    from kairo.archive import archive_markdown

    return archive_markdown(
        body,
        serve_root=root,
        workspace=workspace,
        create=True,
        bind=None,
        title=None,
    )


def test_json_receipt_matches_stdout_envelope(tmp_path, monkeypatch):
    root = _serve_ws(tmp_path)
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(root))
    src = tmp_path / "s.md"
    src.write_text("hello session")
    plain = runner.invoke(
        app, ["archive", str(src), "--workspace", "topic-a", "--create"]
    )
    rec_line = plain.stdout.strip()
    src2 = tmp_path / "s2.md"
    src2.write_text(f"hello session\n{rec_line}\n")
    js = runner.invoke(app, ["archive", str(src2), "--json"])
    assert js.exit_code == 0
    payload = json.loads(js.stdout)
    assert payload["ok"] is True
    assert payload["receipt"] == rec_line
    assert payload["version"] == 1


def test_continue_preserves_extra_forms(tmp_path, monkeypatch):
    root = _serve_ws(tmp_path)
    rec = _create_archive(root, "hello session", workspace="topic-a")
    ws = Workspace.open(root / "topic-a")
    extra = tmp_path / "note.txt"
    extra.write_text("attached")
    ws.add([extra], ref_id=rec.reference, copy=True)
    before = len(ws.read_manifest(rec.reference).forms)
    assert before >= 2
    src = tmp_path / "s.md"
    src.write_text(f"hello session\n{rec.envelope()}\nmore\n")
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(root))
    result = runner.invoke(app, ["archive", str(src)])
    assert result.exit_code == 0
    man = ws.read_manifest(rec.reference)
    assert len(man.forms) == before
    assert man.forms[0].role == "source_text"
    assert man.archive.form_index == 0
    loc = Path(man.forms[0].location)
    p = loc if loc.is_absolute() else ws.root / loc
    assert p.read_text() == "hello session\nmore"


def test_continue_rejects_create(tmp_path, monkeypatch):
    root = _serve_ws(tmp_path)
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(root))
    rec = _create_archive(root, "hello session", workspace="topic-a")
    src = tmp_path / "s.md"
    src.write_text(f"hello session\n{rec.envelope()}\nmore\n")
    result = runner.invoke(
        app, ["archive", str(src), "--workspace", "topic-a", "--create"]
    )
    assert result.exit_code == 1
    ws = Workspace.open(root / "topic-a")
    assert len(ws.list_reference_ids()) == 1


def test_archive_does_not_step_until_asked(tmp_path, monkeypatch):
    root = _serve_ws(tmp_path)
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(root))
    monkeypatch.setenv("KAIRO_STUB", "1")
    _create_archive(root, "hello session", workspace="topic-a")
    ws = Workspace.open(root / "topic-a")
    rid = ws.list_reference_ids()[0]
    assert not (ws.root / "references" / rid / "digest.md").is_file()
    assert not (ws.root / "understanding.md").is_file()
    monkeypatch.chdir(ws.root)
    stepped = runner.invoke(app, ["step"])
    assert stepped.exit_code == 0
    assert (ws.root / "references" / rid / "digest.md").is_file()
