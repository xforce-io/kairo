"""#140 dashboard: recent order, search/filter, pin."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from fastapi.testclient import TestClient

from kairo.models import ProductState
from kairo.web.server import create_app
from kairo.workspace import Workspace


def _client(root):
    return TestClient(create_app(root))


def _utime(path: Path, ts: float) -> None:
    os.utime(path, (ts, ts))


def _cards(html: str) -> list[str]:
    return re.findall(r'class="card-main"[^>]*href="/w/([^"]+)"', html)


def _mk(root: Path, slug: str, topic: str, age: float) -> Workspace:
    ws = Workspace.init(root / slug, topic=topic)
    _utime(ws.root / "constitution.yaml", time.time() - age)
    state = ws.root / ".kairo" / "state.json"
    if state.is_file():
        _utime(state, time.time() - age)
    return ws


def test_dashboard_unpinned_recent_order_and_time_label(tmp_path):
    _mk(tmp_path, "old-ws", "older topic", 80_000)
    _mk(tmp_path, "new-ws", "newer topic", 30)
    r = _client(tmp_path).get("/")
    assert r.status_code == 200
    assert _cards(r.text) == ["new-ws", "old-ws"]
    assert "today " in r.text or "yesterday" in r.text
    assert "sort=name" not in r.text
    assert 'class="dash-sort"' not in r.text
    assert 'name="q"' in r.text
    assert "Attention" in r.text and "Blocked" in r.text
    assert "New topic" in r.text
    assert 'hx-post="/workspaces"' in r.text


def test_dashboard_search_and_filters(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    a = _mk(tmp_path, "alpha", "Alpha planning", 100)
    b = _mk(tmp_path, "beta", "Beta notes", 50)
    src = tmp_path / "m.txt"
    src.write_text("x")
    a.add([src])
    state = b.read_state()
    state.products["x"] = ProductState(input_hash="h", status="blocked", reason="asr-failed")
    b.write_state(state)

    c = _client(tmp_path)
    r = c.get("/", params={"q": "alpha"})
    assert _cards(r.text) == ["alpha"]
    assert "Beta notes" not in r.text

    r = c.get("/", params={"filter": "blocked"})
    assert _cards(r.text) == ["beta"]

    r = c.get("/", params={"filter": "attention"})
    slugs = _cards(r.text)
    assert "alpha" in slugs and "beta" in slugs

    r = c.get("/", params={"q": "zzzz-nope"})
    assert _cards(r.text) == []
    assert "No workspaces match" in r.text
    assert "Clear filters" in r.text
    assert 'name="q"' in r.text
    assert "No workspace here" not in r.text
    assert r.status_code == 200

    r = c.get("/", params={"filter": "nope", "sort": "name"})
    assert r.status_code == 200
    assert "sort=name" not in r.text
    assert set(_cards(r.text)) == {"alpha", "beta"}


def _filter_nav(html: str) -> str:
    m = re.search(r'<nav class="dash-filter"[^>]*>.*?</nav>', html, re.S)
    assert m, "missing dash-filter nav"
    return m.group(0)


def test_dashboard_all_chip_returns_from_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    _mk(tmp_path, "alpha", "Alpha planning", 100)
    b = _mk(tmp_path, "beta", "Beta notes", 50)
    state = b.read_state()
    state.products["x"] = ProductState(input_hash="h", status="blocked", reason="asr-failed")
    b.write_state(state)
    c = _client(tmp_path)

    blocked = c.get("/", params={"filter": "blocked"})
    assert _cards(blocked.text) == ["beta"]
    nav = _filter_nav(blocked.text)
    assert "All" in nav
    all_href = re.search(r'href="([^"]+)"[^>]*>All', nav)
    assert all_href, nav
    assert "filter=" not in all_href.group(1)

    back = c.get(all_href.group(1))
    assert set(_cards(back.text)) == {"alpha", "beta"}
    home_nav = _filter_nav(back.text)
    assert re.search(r'class="[^"]*\bon\b[^"]*"[^>]*>All', home_nav) or re.search(
        r'<a class="on"[^>]*>All', home_nav
    )


def test_dashboard_pin_prepends_and_sections(tmp_path):
    _mk(tmp_path, "a-ws", "A", 100)
    _mk(tmp_path, "b-ws", "B", 50)
    _mk(tmp_path, "c-ws", "C", 10)
    c = _client(tmp_path)
    assert "Pinned" not in c.get("/").text
    assert "Recent" not in c.get("/").text

    r = c.post("/workspaces/b-ws/pin", follow_redirects=True)
    assert r.status_code == 200
    pins = (tmp_path / "pinned.yaml").read_text()
    assert "b-ws" in pins
    html = r.text
    assert "Pinned" in html and "Recent" in html
    assert html.index("Pinned") < html.index('href="/w/b-ws"') < html.index("Recent")
    cards = _cards(html)
    assert cards[0] == "b-ws"
    assert cards[1:] == ["c-ws", "a-ws"]

    r = c.post("/workspaces/c-ws/pin", follow_redirects=True)
    cards = _cards(r.text)
    assert cards[:2] == ["c-ws", "b-ws"]

    r = c.post("/workspaces/b-ws/pin", follow_redirects=True)
    cards = _cards(r.text)
    assert cards[0] == "c-ws"
    assert "b-ws" in cards[1:]
    assert "Pinned" in r.text and "Recent" in r.text
    r = c.post("/workspaces/c-ws/pin", follow_redirects=True)
    assert "Pinned" not in r.text
    assert "Recent" not in r.text

    missing = c.post("/workspaces/nope/pin")
    assert missing.status_code == 404


def test_pin_does_not_change_last_activity(tmp_path):
    ws = _mk(tmp_path, "ws", "T", 4000)
    before = (ws.root / "constitution.yaml").stat().st_mtime
    _client(tmp_path).post("/workspaces/ws/pin", follow_redirects=True)
    after = (ws.root / "constitution.yaml").stat().st_mtime
    assert after == before
    assert (tmp_path / "pinned.yaml").is_file()


def test_readonly_view_does_not_reorder_unpinned(tmp_path):
    _mk(tmp_path, "old-ws", "old", 9000)
    _mk(tmp_path, "new-ws", "new", 20)
    c = _client(tmp_path)
    before = _cards(c.get("/").text)
    assert before == ["new-ws", "old-ws"]
    assert c.get("/w/old-ws").status_code == 200
    assert _cards(c.get("/").text) == before


def test_write_moves_unpinned_forward_pinned_stays(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    old = _mk(tmp_path, "old-ws", "old", 9000)
    _mk(tmp_path, "new-ws", "new", 20)
    pinned = _mk(tmp_path, "pin-ws", "pin", 12_000)
    c = _client(tmp_path)
    c.post("/workspaces/pin-ws/pin", follow_redirects=True)
    src = tmp_path / "n.txt"
    src.write_text("note")
    old.add([src])
    html = c.get("/").text
    cards = _cards(html)
    assert cards[0] == "pin-ws"
    assert cards[1] == "old-ws"
    assert cards[2] == "new-ws"
    src2 = tmp_path / "p.txt"
    src2.write_text("p")
    pinned.add([src2])
    after = _cards(c.get("/").text)
    assert after[0] == "pin-ws"


def _console_nav(html: str) -> str:
    m = re.search(r'<nav class="console-nav"[^>]*>.*?</nav>', html, re.S)
    assert m, "missing console-nav"
    return m.group(0)


def _header(html: str) -> str:
    m = re.search(r"<header class=\"top\">.*?</header>", html, re.S)
    assert m, "missing header"
    return m.group(0)


def test_glossary_is_header_utility_not_console_nav(tmp_path):
    """#174: 真名册在顶栏弱链，不进主导航，不在 dash-head。"""
    Workspace.init(tmp_path / "ws", topic="t")
    html = _client(tmp_path).get("/").text
    nav = _console_nav(html)
    assert 'href="/glossary"' not in nav
    header = _header(html)
    assert re.search(r'href="/glossary"', header)
    assert "Glossary" in header
    start = html.find('class="dash-head"')
    end = html.find('class="grid"')
    assert start != -1 and end != -1 and start < end
    assert 'href="/glossary"' not in html[start:end]


def test_glossary_page_marks_utility_on(tmp_path):
    """#174: /glossary 弱链为当前项，主导航不选中。"""
    Workspace.init(tmp_path / "ws", topic="t")
    html = _client(tmp_path).get("/glossary").text
    nav = _console_nav(html)
    assert 'href="/glossary"' not in nav
    assert re.search(r'<a href="/" class="on">', nav) is None
    assert re.search(r'<a href="/timeline" class="on">', nav) is None
    assert re.search(r'href="/glossary"[^>]*\bon\b', _header(html))
    crumb = re.search(r'<span class="crumb">(.*?)</span>', html, re.S)
    assert crumb is not None
    assert "Shared glossary" not in crumb.group(1)
    assert "公共真名册" not in crumb.group(1)
