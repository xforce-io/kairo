import datetime as dt
import json
from pathlib import Path

import pytest

from kairo.review import (
    JOURNAL_NAME,
    ReviewError,
    collect_digests,
    generate_review_body,
    layout_review,
    prepare_range,
    resolve_review_workspace,
    review_title,
    write_review_reference,
)
from kairo.timeline import scan_timeline
from kairo.workspace import Workspace, WorkspaceNotFound


def _range_ws(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    root = tmp_path / "root"
    root.mkdir()
    a = Workspace.init(root / "alpha", topic="能源梳理")
    b = Workspace.init(root / "beta", topic="招聘")
    dest = Workspace.init(root / "回顾", topic="回顾")
    (tmp_path / "m.txt").write_text("周会")
    (tmp_path / "n.txt").write_text("沟通")
    (tmp_path / "z.txt").write_text("无纪要")
    rid_a = a.add([tmp_path / "m.txt"], ref_id="2026-08-21-weekly", occurred_at="2026-08-21")
    rid_b = b.add([tmp_path / "n.txt"], ref_id="2026-08-18-call", occurred_at="2026-08-18")
    a.add([tmp_path / "z.txt"], ref_id="2026-08-24-empty", occurred_at="2026-08-24")
    (a.references_dir() / rid_a / "digest.md").write_text("能源周会:推进并网")
    (b.references_dir() / rid_b / "digest.md").write_text("候选人沟通:下周面试")
    return root, dest


def test_prepare_range_and_collect(tmp_path, monkeypatch):
    root, _ = _range_ws(tmp_path, monkeypatch)
    items = scan_timeline(root)
    start, end = dt.date(2026, 8, 18), dt.date(2026, 8, 24)
    found = prepare_range(items, start, end)
    assert {it.id for it in found} == {
        "2026-08-21-weekly",
        "2026-08-18-call",
        "2026-08-24-empty",
    }
    with_d, without = collect_digests(root, found)
    assert {it.id for it, _ in with_d} == {"2026-08-21-weekly", "2026-08-18-call"}
    assert {it.id for it in without} == {"2026-08-24-empty"}
    with pytest.raises(ReviewError):
        prepare_range(items, dt.date(2026, 7, 1), dt.date(2026, 7, 2))
    with pytest.raises(ReviewError) as too_long:
        prepare_range(items, dt.date(2026, 8, 1), dt.date(2026, 9, 1))
    assert str(too_long.value) == "range-too-long"


def test_write_review_reference_sets_title_and_occurred(tmp_path, monkeypatch):
    root, dest = _range_ws(tmp_path, monkeypatch)
    items = scan_timeline(root)
    start, end = dt.date(2026, 8, 18), dt.date(2026, 8, 24)
    found = prepare_range(items, start, end)
    with_d, without = collect_digests(root, found)
    body = generate_review_body(
        with_d, without, artifact_dir=tmp_path / "art", provider=None
    )
    assert "推进并网" in body or "STUB" in body
    orig_digest = (root / "alpha" / "references" / "2026-08-21-weekly" / "digest.md").read_text()
    rid = write_review_reference(dest, start, end, body)
    man = dest.read_manifest(rid)
    assert man.title == review_title(start, end)
    assert man.occurred_at == "2026-08-24"
    assert (dest.references_dir() / rid).is_dir()
    assert any(f.role == "source_text" for f in man.forms)
    assert (root / "alpha" / "references" / "2026-08-21-weekly" / "digest.md").read_text() == orig_digest
    rid2 = write_review_reference(dest, start, end, body + "\n第二稿")
    assert rid2 == rid
    assert dest.list_reference_ids() == [rid]
    text = (dest.root / dest.read_manifest(rid).forms[0].location).read_text(encoding="utf-8")
    assert "第二稿" in text


def test_prepare_range_skips_journal_items(tmp_path, monkeypatch):
    root, dest = _range_ws(tmp_path, monkeypatch)
    journal = resolve_review_workspace(root)
    write_review_reference(journal, dt.date(2026, 8, 18), dt.date(2026, 8, 24), "旧回顾")
    items = scan_timeline(root)
    found = prepare_range(items, dt.date(2026, 8, 18), dt.date(2026, 8, 24))
    assert all(it.workspace != JOURNAL_NAME and it.topic != JOURNAL_NAME for it in found)
    assert {it.id for it in found} == {
        "2026-08-21-weekly",
        "2026-08-18-call",
        "2026-08-24-empty",
    }


def test_resolve_review_workspace_creates_and_reuses(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    Workspace.init(root / "alpha", topic="能源梳理")
    first = resolve_review_workspace(root)
    assert first.root.name == JOURNAL_NAME
    assert first.constitution.topic == JOURNAL_NAME
    second = resolve_review_workspace(root)
    assert second.root.resolve() == first.root.resolve()
    named = Workspace.init(root / "回顾", topic="回顾")
    escaped = resolve_review_workspace(root, "回顾")
    assert escaped.root.resolve() == named.root.resolve()
    with pytest.raises(WorkspaceNotFound):
        resolve_review_workspace(root, "missing")


def test_layout_review_keeps_bodies_in_files_not_context(tmp_path, monkeypatch):
    root, dest = _range_ws(tmp_path, monkeypatch)
    items = scan_timeline(root)
    found = prepare_range(items, dt.date(2026, 8, 18), dt.date(2026, 8, 24), root=root)
    with_d, without = collect_digests(root, found)
    materials, context = layout_review(with_d, without)
    blob = "".join(text for _, text in with_d)
    assert "推进并网" in blob
    assert "推进并网" not in context
    assert "digest/" in context
    art = tmp_path / "art"
    generate_review_body(with_d, without, artifact_dir=art, provider=None)
    on_disk = 0
    for rel in materials:
        p = art / rel
        assert p.is_file()
        on_disk += p.stat().st_size
    assert on_disk >= sum(len(t.encode("utf-8")) for t in materials.values())
    assert len(context.encode("utf-8")) < 2000


def test_grok_review_uses_prompt_file_not_digest_argv(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    from kairo.provider import GrokProvider

    root, dest = _range_ws(tmp_path, monkeypatch)
    items = scan_timeline(root)
    found = prepare_range(items, dt.date(2026, 8, 18), dt.date(2026, 8, 24), root=root)
    with_d, without = collect_digests(root, found)
    calls = []

    def fake_runner(cmd, args, *, cwd, input, stdout_file=None, timeout=None):
        calls.append(args)
        Path(stdout_file).write_text(
            json.dumps({"text": "# 回顾\n推进并网\n"}, ensure_ascii=False)
        )

    body = generate_review_body(
        with_d,
        without,
        artifact_dir=tmp_path / "art",
        provider=GrokProvider(runner=fake_runner),
    )
    assert calls
    joined = " ".join(str(a) for a in calls[0])
    assert "--prompt-file" in calls[0]
    assert "-p" not in calls[0]
    assert "推进并网" not in joined
    assert "推进并网" in body

