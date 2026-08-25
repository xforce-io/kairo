import datetime as dt

from fastapi.testclient import TestClient

from kairo.web.server import create_app
from kairo.workspace import Workspace


def _client(root):
    return TestClient(create_app(root))


def _two_ws(tmp_path):
    root = tmp_path / "root"
    a = root / "alpha"
    b = root / "beta"
    a.mkdir(parents=True)
    b.mkdir()
    wa = Workspace.init(a, topic="能源梳理")
    wb = Workspace.init(b, topic="招聘")
    (tmp_path / "m.txt").write_text("会议")
    (tmp_path / "n.txt").write_text("笔记")
    (tmp_path / "c.txt").write_text("基线")
    wa.add([tmp_path / "m.txt"], ref_id="2026-08-25-weekly", occurred_at="2026-08-24")
    wb.add([tmp_path / "n.txt"], ref_id="notes-candidate")
    wa.add([tmp_path / "c.txt"], ref_id="whitepaper", source_class="corpus")
    return root, wa, wb


def test_timeline_calendar_shows_day_and_unknown(tmp_path):
    root, _, _ = _two_ws(tmp_path)
    c = _client(root)
    r = c.get("/timeline", params={"day": "2026-08-24"})
    assert r.status_code == 200
    assert "2026-08-25-weekly" in r.text
    assert "notes-candidate" not in r.text
    assert "whitepaper" not in r.text
    r2 = c.get("/timeline", params={"unknown": "1"})
    assert r2.status_code == 200
    assert "notes-candidate" in r2.text
    assert "2026-08-25-weekly" not in r2.text


def test_timeline_query_mutex_400(tmp_path):
    root, _, _ = _two_ws(tmp_path)
    c = _client(root)
    assert c.get("/timeline", params={"month": "2026-07", "day": "2026-08-24"}).status_code == 400
    assert c.get("/timeline", params={"mode": "recent", "day": "2026-08-24"}).status_code == 400
    assert c.get("/timeline", params={"unknown": "1", "day": "2026-08-24"}).status_code == 400
    assert c.get("/timeline", params={"mode": "recent", "unknown": "1"}).status_code == 400
    assert c.get("/timeline", params={"day": "2026-02-31"}).status_code == 400


def test_timeline_recent_lists_by_added(tmp_path):
    root, _, _ = _two_ws(tmp_path)
    r = _client(root).get("/timeline", params={"mode": "recent"})
    assert r.status_code == 200
    assert "2026-08-25-weekly" in r.text
    assert "notes-candidate" in r.text


def test_fold_false_cannot_post_occurred(tmp_path):
    root, wa, _ = _two_ws(tmp_path)
    c = _client(root)
    r = c.post("/w/alpha/ref/whitepaper/occurred", data={"occurred_at": "2026-08-01"})
    assert r.status_code == 400
    r = c.post("/w/alpha/ref/2026-08-25-weekly/occurred", data={"occurred_at": "2026-02-31"})
    assert r.status_code == 400
    r = c.post("/w/alpha/ref/2026-08-25-weekly/occurred", data={"occurred_at": "2026-08-21"})
    assert r.status_code == 200
    assert wa.read_manifest("2026-08-25-weekly").occurred_at == "2026-08-21"
    cal = c.get("/timeline", params={"day": "2026-08-21"})
    assert "2026-08-25-weekly" in cal.text
    r = c.post("/w/alpha/ref/2026-08-25-weekly/occurred", data={"occurred_at": ""})
    assert r.status_code == 200
    # id prefix 2026-08-25
    assert "2026-08-25-weekly" in c.get("/timeline", params={"day": "2026-08-25"}).text


def test_workspace_ref_query_selects(tmp_path):
    root, _, _ = _two_ws(tmp_path)
    r = _client(root).get("/w/alpha", params={"ref": "2026-08-25-weekly"})
    assert r.status_code == 200
    assert 'hx-get="/w/alpha/ref/2026-08-25-weekly"' in r.text


def test_nav_links_on_dashboard(tmp_path):
    r = _client(tmp_path).get("/")
    assert r.status_code == 200
    assert 'href="/timeline"' in r.text
