import datetime as dt
from pathlib import Path

import pytest
import yaml

from kairo.models import Manifest
from kairo.timeline import (
    TimelineQueryError,
    cell_href,
    effective_occurred,
    filter_range,
    format_cli_timeline,
    format_range_label,
    parse_calendar_date,
    range_day_count,
    resolve_timeline_query,
    scan_timeline,
    shift_month_day,
    week_bounds,
)
from kairo.workspace import AddError, Workspace


def test_parse_rejects_nonexistent_calendar_day():
    assert parse_calendar_date("2026-02-31") is None
    assert parse_calendar_date("2025-02-29") is None
    assert parse_calendar_date("2024-02-29") == dt.date(2024, 2, 29)
    assert parse_calendar_date("2026-08-24") == dt.date(2026, 8, 24)


def test_effective_occurred_user_beats_id_prefix():
    d, src = effective_occurred("2026-08-25-weekly", "2026-08-24")
    assert d == dt.date(2026, 8, 24) and src == "user"


def test_effective_occurred_id_prefix_and_unknown():
    d, src = effective_occurred("2026-08-24-weekly", None)
    assert d == dt.date(2026, 8, 24) and src == "id"
    d, src = effective_occurred("2026-02-31-weekly", None)
    assert d is None and src == "unknown"
    d, src = effective_occurred("notes-candidate", None)
    assert d is None and src == "unknown"


def test_read_manifest_coerces_unquoted_yaml_date(tmp_path):
    ws = Workspace.init(tmp_path)
    src = tmp_path / "a.txt"
    src.write_text("x")
    rid = ws.add([src], ref_id="plain")
    path = ws.references_dir() / rid / "manifest.yaml"
    raw = yaml.safe_load(path.read_text())
    raw["occurred_at"] = dt.date(2026, 8, 24)
    raw["added_at"] = dt.datetime(2026, 8, 25, 14, 12, 3)
    path.write_text(yaml.safe_dump(raw, allow_unicode=True))
    # simulate unquoted load
    loaded = yaml.safe_load("occurred_at: 2026-08-24\nadded_at: 2026-08-25T14:12:03\nid: x\nclass: stream\nforms: []\n")
    man = Manifest.model_validate(loaded)
    assert man.occurred_at == "2026-08-24"
    assert man.added_at.startswith("2026-08-25T14:12:03")


def test_read_manifest_dirty_occurred_at_does_not_drop_ref(tmp_path):
    ws = Workspace.init(tmp_path)
    src = tmp_path / "a.txt"
    src.write_text("x")
    rid = ws.add([src], ref_id="2026-08-24-ok")
    path = ws.references_dir() / rid / "manifest.yaml"
    data = yaml.safe_load(path.read_text())
    data["occurred_at"] = "2026-02-31"
    path.write_text(yaml.safe_dump(data, allow_unicode=True))
    man = ws.read_manifest(rid)
    assert man.occurred_at == "2026-02-31"
    d, src = effective_occurred(rid, man.occurred_at)
    assert d == dt.date(2026, 8, 24) and src == "id"


def test_read_manifest_non_date_type_becomes_none():
    man = Manifest.model_validate(
        {"id": "x", "occurred_at": 12, "added_at": True, "forms": []}
    )
    assert man.occurred_at is None and man.added_at is None


def test_added_at_frozen_against_caller_tamper(tmp_path):
    ws = Workspace.init(tmp_path)
    src = tmp_path / "a.txt"
    src.write_text("x")
    rid = ws.add([src], ref_id="n")
    first = ws.read_manifest(rid).added_at
    assert first
    man = ws.read_manifest(rid)
    man.added_at = "1999-01-01T00:00:00+00:00"
    man.title = "renamed"
    ws.write_manifest(rid, man)
    assert ws.read_manifest(rid).added_at == first
    assert ws.read_manifest(rid).title == "renamed"


def test_added_at_missing_frozen_to_pre_write_mtime(tmp_path):
    ws = Workspace.init(tmp_path)
    src = tmp_path / "a.txt"
    src.write_text("x")
    rid = ws.add([src], ref_id="old")
    path = ws.references_dir() / rid / "manifest.yaml"
    data = yaml.safe_load(path.read_text())
    data.pop("added_at", None)
    path.write_text(yaml.safe_dump(data, allow_unicode=True))
    before = path.stat().st_mtime
    man = ws.read_manifest(rid)
    man.title = "t2"
    ws.write_manifest(rid, man)
    frozen = dt.datetime.fromisoformat(ws.read_manifest(rid).added_at)
    assert abs(frozen.timestamp() - before) < 2
    again = ws.read_manifest(rid).added_at
    man = ws.read_manifest(rid)
    man.title = "t3"
    ws.write_manifest(rid, man)
    assert ws.read_manifest(rid).added_at == again


def test_write_manifest_failure_keeps_old_file(tmp_path, monkeypatch):
    ws = Workspace.init(tmp_path)
    src = tmp_path / "a.txt"
    src.write_text("x")
    rid = ws.add([src], ref_id="keep")
    path = ws.references_dir() / rid / "manifest.yaml"
    original = path.read_text()

    def boom(self, data, encoding="utf-8"):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", boom)
    man = ws.read_manifest(rid)
    man.title = "nope"
    with pytest.raises(OSError):
        ws.write_manifest(rid, man)
    # restore read (monkeypatch hits read_text too if we used write_text only)
    monkeypatch.undo()
    assert path.read_text() == original
    assert ws.read_manifest(rid).title != "nope"


def test_add_occurred_does_not_change_id(tmp_path):
    ws = Workspace.init(tmp_path)
    src = tmp_path / "a.txt"
    src.write_text("x")
    rid = ws.add([src], occurred_at="2026-08-24")
    today = dt.date.today().isoformat()
    assert rid.startswith(today)
    assert ws.read_manifest(rid).occurred_at == "2026-08-24"
    d, src_kind = effective_occurred(rid, ws.read_manifest(rid).occurred_at)
    assert d == dt.date(2026, 8, 24) and src_kind == "user"


def test_add_occurred_rejects_illegal_and_corpus(tmp_path):
    ws = Workspace.init(tmp_path)
    src = tmp_path / "a.txt"
    src.write_text("x")
    with pytest.raises(AddError):
        ws.add([src], occurred_at="2026-02-31")
    with pytest.raises(AddError):
        ws.add([src], occurred_at="2026-08-24", source_class="corpus")


def test_scan_skips_corpus_and_broken(tmp_path):
    root = tmp_path / "root"
    a = root / "alpha"
    b = root / "beta"
    a.mkdir(parents=True)
    b.mkdir()
    wa = Workspace.init(a, topic="A")
    wb = Workspace.init(b, topic="B")
    (tmp_path / "s.txt").write_text("s")
    (tmp_path / "c.txt").write_text("c")
    wa.add([tmp_path / "s.txt"], ref_id="2026-08-24-meet")
    wb.add([tmp_path / "c.txt"], ref_id="base", source_class="corpus")
    broken = a / "references" / "broken"
    broken.mkdir()
    (broken / "manifest.yaml").write_text("::: not yaml")
    items = scan_timeline(root)
    ids = {it.id for it in items}
    assert "2026-08-24-meet" in ids
    assert "base" not in ids
    assert "broken" not in ids


def test_custom_fold_class_eligibility(tmp_path):
    ws = Workspace.init(tmp_path, topic="t")
    con = ws.constitution
    from kairo.models import SourceClass

    con.source_classes["note"] = SourceClass(label="n", fold=True)
    con.source_classes["skip"] = SourceClass(label="s", fold=False)
    ws.write_constitution(con)
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    ws.add([tmp_path / "a.txt"], ref_id="n1", source_class="note")
    ws.add([tmp_path / "b.txt"], ref_id="s1", source_class="skip")
    # workspace is tmp_path itself if we scan parent... scan children of parent
    # tmp_path is the ws root, scan parent to find it
    found = {it.id for it in scan_timeline(tmp_path.parent) if it.workspace == tmp_path.name}
    assert "n1" in found and "s1" not in found


def test_resolve_timeline_query_mutex():
    today = dt.date(2026, 8, 25)
    q = resolve_timeline_query(today=today)
    assert q.view == "calendar" and q.day == today
    q = resolve_timeline_query(day="2026-08-24", today=today)
    assert q.month == dt.date(2026, 8, 1) and q.day == dt.date(2026, 8, 24)
    with pytest.raises(TimelineQueryError):
        resolve_timeline_query(month="2026-07", day="2026-08-24", today=today)
    with pytest.raises(TimelineQueryError):
        resolve_timeline_query(mode="recent", day="2026-08-24", today=today)
    with pytest.raises(TimelineQueryError):
        resolve_timeline_query(unknown="1", day="2026-08-24", today=today)
    with pytest.raises(TimelineQueryError):
        resolve_timeline_query(mode="recent", unknown="1", today=today)
    with pytest.raises(TimelineQueryError):
        resolve_timeline_query(day="2026-02-31", today=today)
    q = resolve_timeline_query(month="2026-02", today=dt.date(2026, 8, 31))
    assert q.day == dt.date(2026, 2, 28)


def test_shift_month_day_clamps():
    assert shift_month_day(dt.date(2026, 1, 31), 1) == dt.date(2026, 2, 28)


def test_resolve_range_from_to(today=None):
    today = dt.date(2026, 8, 25)
    q = resolve_timeline_query(start="2026-08-24", end="2026-08-18", today=today)
    assert q.start == dt.date(2026, 8, 18) and q.end == dt.date(2026, 8, 24)
    assert q.day == dt.date(2026, 8, 24)
    with pytest.raises(TimelineQueryError):
        resolve_timeline_query(start="2026-08-18", today=today)
    with pytest.raises(TimelineQueryError):
        resolve_timeline_query(start="2026-08-18", end="2026-02-31", today=today)
    with pytest.raises(TimelineQueryError):
        resolve_timeline_query(
            start="2026-08-18", end="2026-08-24", mode="recent", today=today
        )


def test_filter_range_excludes_unknown():
    from kairo.timeline import TimelineItem

    def item(oid, occ):
        return TimelineItem(
            workspace="w",
            topic="t",
            id=oid,
            title=oid,
            occurred_at=occ,
            occurred_source="user" if occ else "unknown",
            added_at=dt.datetime(2026, 8, 25, tzinfo=dt.timezone.utc),
        )

    items = [
        item("a", dt.date(2026, 8, 18)),
        item("b", dt.date(2026, 8, 24)),
        item("c", None),
        item("d", dt.date(2026, 8, 10)),
    ]
    got = filter_range(items, dt.date(2026, 8, 18), dt.date(2026, 8, 24))
    assert [it.id for it in got] == ["a", "b"]
    assert range_day_count(dt.date(2026, 8, 1), dt.date(2026, 8, 31)) == 31
    assert range_day_count(dt.date(2026, 8, 1), dt.date(2026, 9, 1)) == 32
    assert format_range_label(dt.date(2026, 8, 18), dt.date(2026, 8, 24), zh=True) == "8月18日 – 8月24日"


def test_cell_href_two_click_and_week():
    today = dt.date(2026, 8, 18)
    q = resolve_timeline_query(day="2026-08-18", today=today)
    assert "from=2026-08-18" in cell_href(q, dt.date(2026, 8, 24))
    assert "to=2026-08-24" in cell_href(q, dt.date(2026, 8, 24))
    q2 = resolve_timeline_query(start="2026-08-18", end="2026-08-24", today=today)
    assert cell_href(q2, dt.date(2026, 8, 20)) == "/timeline?day=2026-08-20"
    assert week_bounds(dt.date(2026, 8, 24)) == (
        dt.date(2026, 8, 24),
        dt.date(2026, 8, 30),
    )


def test_cli_format_unknown_first():
    items = [
        __import__("kairo.timeline", fromlist=["TimelineItem"]).TimelineItem(
            workspace="w",
            topic="t",
            id="notes",
            title="n",
            occurred_at=None,
            occurred_source="unknown",
            added_at=dt.datetime(2026, 8, 25, tzinfo=dt.timezone.utc),
        )
    ]
    text = format_cli_timeline(items)
    assert text.startswith("⚠ 发生时间未知")
