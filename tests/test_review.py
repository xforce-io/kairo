import datetime as dt

import pytest

from kairo.review import (
    ReviewError,
    collect_digests,
    generate_review_body,
    prepare_range,
    review_title,
    write_review_reference,
)
from kairo.timeline import scan_timeline
from kairo.workspace import Workspace


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
