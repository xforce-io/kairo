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
