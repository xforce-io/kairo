"""#287: Timeline list and --recent group by occurred day, not added day."""

from __future__ import annotations

import datetime as dt
import json
import re

import yaml
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from kairo.cli import app
from kairo.timeline import TimelineItem, format_cli_timeline
from kairo.web.server import create_app
from kairo.workspace import Workspace

runner = CliRunner()


def _client(root):
    return TestClient(create_app(root))


def _force_added(ws: Workspace, rid: str, when: str) -> None:
    path = ws.references_dir() / rid / "manifest.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data["added_at"] = when
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _mismatch_root(tmp_path):
    """One Ref occurred 07-28 added 08-31; one occurred 08-31; one unknown."""
    root = tmp_path / "root"
    wsdir = root / "alpha"
    wsdir.mkdir(parents=True)
    ws = Workspace.init(wsdir, topic="数据模型闭环逻辑")
    (tmp_path / "a.txt").write_text("tiansu")
    (tmp_path / "b.txt").write_text("zhang")
    (tmp_path / "c.txt").write_text("loose")
    late = ws.add(
        [tmp_path / "a.txt"],
        ref_id="2026-07-28-tiansu",
        title="tiansu",
        occurred_at="2026-07-28",
    )
    same = ws.add(
        [tmp_path / "b.txt"],
        ref_id="2026-08-31-zhang",
        title="张鹏沟通-260831",
        occurred_at="2026-08-31",
    )
    unknown = ws.add([tmp_path / "c.txt"], ref_id="notes-candidate", title="未标日期")
    _force_added(ws, late, "2026-08-31T12:36:00+08:00")
    _force_added(ws, same, "2026-08-31T12:09:00+08:00")
    _force_added(ws, unknown, "2026-08-31T13:00:00+08:00")
    return root, late, same, unknown


def _list_groups(html: str) -> list[tuple[str, str]]:
    return re.findall(
        r'<section class="tl-day[^"]*">\s*<div class="tl-day-head"><h2>([^<]*)</h2>([\s\S]*?)</section>',
        html,
    )


def _group_for(html: str, needle: str) -> str | None:
    for heading, body in _list_groups(html):
        if needle in body:
            return heading
    return None


def _cli_groups(text: str) -> dict[str, str]:
    groups: dict[str, str] = {}
    current = None
    for line in text.splitlines():
        if line.startswith("  "):
            if current is not None:
                groups[current] += line + "\n"
        else:
            current = line
            groups[current] = ""
    return groups


def test_list_html_groups_by_occurred_not_added(tmp_path):
    root, late, same, unknown = _mismatch_root(tmp_path)
    html = _client(root).get("/timeline").text
    assert _group_for(html, late) == "2026-07-28"
    assert _group_for(html, same) == "2026-08-31"
    assert _group_for(html, late) != "2026-08-31"
    late_body = next(body for heading, body in _list_groups(html) if heading == "2026-07-28")
    assert same not in late_body
    added_day = next((body for heading, body in _list_groups(html) if heading == "2026-08-31"), "")
    assert late not in added_day
    unknown_heading = _group_for(html, unknown)
    assert unknown_heading is not None
    assert not re.fullmatch(r"\d{4}-\d{2}-\d{2}", unknown_heading)


def test_mode_recent_is_list_alias_and_labels(tmp_path):
    root, late, _, _ = _mismatch_root(tmp_path)
    en = _client(root).get("/timeline", params={"mode": "recent"})
    assert en.status_code == 200
    assert _group_for(en.text, late) == "2026-07-28"
    modes = re.search(r'<div class="tl-modes">([\s\S]*?)</div>', en.text)
    assert modes is not None
    assert ">List<" in modes.group(1)
    assert "Recent" not in modes.group(1)
    assert re.search(r'<a class="on" href="/timeline\?mode=recent">', en.text)
    zh = _client(root).get(
        "/timeline", params={"mode": "recent"}, headers={"Accept-Language": "zh-CN"}
    )
    zh_modes = re.search(r'<div class="tl-modes">([\s\S]*?)</div>', zh.text)
    assert zh_modes is not None
    assert ">列表<" in zh_modes.group(1)
    assert "最近加入" not in zh_modes.group(1)
    cal = _client(root).get("/timeline", params={"day": "2026-07-28"})
    assert cal.status_code == 200
    assert 'class="cal-layout"' in cal.text
    assert late in cal.text


def test_cli_recent_matches_default_occurred_groups(tmp_path):
    root, late, same, unknown = _mismatch_root(tmp_path)
    default = runner.invoke(app, ["timeline", str(root)])
    recent = runner.invoke(app, ["timeline", str(root), "--recent"])
    assert default.exit_code == 0, default.output
    assert recent.exit_code == 0, recent.output
    d_groups = _cli_groups(default.output)
    r_groups = _cli_groups(recent.output)
    assert late in d_groups["2026-07-28"]
    assert late in r_groups["2026-07-28"]
    assert same in d_groups["2026-08-31"]
    assert same in r_groups["2026-08-31"]
    assert late not in d_groups.get("2026-08-31", "")
    assert late not in r_groups.get("2026-08-31", "")
    assert unknown in d_groups["⚠ 发生时间未知"]
    assert unknown in r_groups["⚠ 发生时间未知"]
    mutex_day = runner.invoke(app, ["timeline", str(root), "--day", "2026-07-28", "--recent"])
    assert mutex_day.exit_code == 1
    mutex_range = runner.invoke(
        app, ["timeline", str(root), "--from", "2026-07-28", "--to", "2026-08-31", "--recent"]
    )
    assert mutex_range.exit_code == 1


def test_json_recent_is_not_ordered_by_added_at(tmp_path):
    root, late, same, unknown = _mismatch_root(tmp_path)
    raw = runner.invoke(app, ["timeline", str(root), "--json", "--recent"])
    assert raw.exit_code == 0, raw.output
    rows = json.loads(raw.output)
    ids = [row["id"] for row in rows]
    added_desc = [
        row["id"]
        for row in sorted(rows, key=lambda r: r["added_at"], reverse=True)
    ]
    assert ids != added_desc
    assert ids[0] == unknown
    dated = [row for row in rows if row["occurred_at"]]
    occurred = [row["occurred_at"] for row in dated]
    assert occurred == sorted(occurred, reverse=True)
    assert late in ids and same in ids


def test_format_cli_timeline_recent_uses_occurred_headings():
    items = [
        TimelineItem(
            workspace="w",
            topic="t",
            id="2026-07-28-tiansu",
            title="tiansu",
            occurred_at=dt.date(2026, 7, 28),
            occurred_source="user",
            added_at=dt.datetime(2026, 8, 31, 12, 36, tzinfo=dt.timezone(dt.timedelta(hours=8))),
        ),
        TimelineItem(
            workspace="w",
            topic="t",
            id="2026-08-31-zhang",
            title="zhang",
            occurred_at=dt.date(2026, 8, 31),
            occurred_source="user",
            added_at=dt.datetime(2026, 8, 31, 12, 9, tzinfo=dt.timezone(dt.timedelta(hours=8))),
        ),
        TimelineItem(
            workspace="w",
            topic="t",
            id="notes-candidate",
            title="loose",
            occurred_at=None,
            occurred_source="unknown",
            added_at=dt.datetime(2026, 8, 31, 13, 0, tzinfo=dt.timezone(dt.timedelta(hours=8))),
        ),
    ]
    default = format_cli_timeline(items)
    recent = format_cli_timeline(items, recent=True)
    assert default == recent
    groups = _cli_groups(recent)
    assert "2026-07-28-tiansu" in groups["2026-07-28"]
    assert "2026-07-28-tiansu" not in groups.get("2026-08-31", "")
    assert "notes-candidate" in groups["⚠ 发生时间未知"]
