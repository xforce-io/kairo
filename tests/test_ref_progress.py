from concurrent.futures import ThreadPoolExecutor
from datetime import date

from fastapi.testclient import TestClient

from kairo.engine import retry_reference
from kairo.provider import StubProvider
from kairo.ref_progress import ProgressEntry, append_progress, read_progress
from kairo.timeline import scan_timeline
from kairo.web.server import create_app
from kairo.workspace import Workspace

from test_public_read import _full_public_root, _public_client


def test_progress_moves_web_ref_and_preserves_occurred_date(tmp_path):
    ws = Workspace.init(tmp_path / "alpha", topic="Alpha")
    source = tmp_path / "meeting.md"
    source.write_text("Original discussion")
    rid = ws.add([source], title="Discussion", occurred_at="2026-08-24")
    path = f"/refs/{rid}"
    href = f'href="{path}?home=alpha'
    client = TestClient(create_app(tmp_path))

    detail = client.get(path, params={"home": "alpha"}).text
    assert "No progress recorded yet." in detail
    assert f'value="{date.today().isoformat()}"' in detail
    assert href in client.get("/timeline?day=2026-08-24").text

    def post(day, content):
        return client.post(
            f"{path}/progress",
            data={"home": "alpha", "day": day, "content": content},
            follow_redirects=False,
        )

    invalid = post("2026-02-31", "Draft to keep")
    assert invalid.status_code == 400
    assert "Draft to keep</textarea>" in invalid.text
    assert post("2026-08-25", "  ").status_code == 400
    assert read_progress(ws.references_dir() / rid) == []

    assert post("2026-08-25", "First update").status_code == 303
    assert post("2026-08-26", "Latest update").status_code == 303
    assert post("2026-08-25", "Backfilled <b>update</b>").status_code == 303
    detail = client.get(path, params={"home": "alpha"}).text
    assert "occurred 2026-08-24" in detail
    assert detail.index("First update") < detail.index("Backfilled &lt;b&gt;")
    assert detail.index("Backfilled &lt;b&gt;") < detail.index("Latest update")
    assert "Backfilled <b>" not in detail

    for old_day in ("2026-08-24", "2026-08-25"):
        assert href not in client.get("/timeline", params={"day": old_day}).text
    week = client.get("/timeline?from=2026-08-24&to=2026-08-26").text
    assert week.count(href) == 1
    assert client.get("/timeline?day=2026-08-26").text.count(href) == 1
    assert client.get("/timeline/run-preview?day=2026-08-26").json()["visible_undigested"] == 1
    assert client.get("/timeline/run-preview?day=2026-08-24").json()["visible_undigested"] == 0
    assert scan_timeline(tmp_path)[0].occurred_at == date(2026, 8, 24)

    ws.set_title(rid, "Renamed discussion")
    source.write_text("Corrected discussion")
    assert retry_reference(ws, StubProvider(), rid)
    assert (ws.references_dir() / rid / "digest.md").is_file()
    assert href in client.get("/timeline?day=2026-08-26").text
    original_range = client.get("/timeline?from=2026-08-24&to=2026-08-25").text
    latest_range = client.get("/timeline?from=2026-08-25&to=2026-08-26").text
    assert href not in original_range
    assert 'action="/timeline/review"' in original_range
    assert href in latest_range
    assert 'action="/timeline/review"' not in latest_range
    assert [entry.day.day for entry in read_progress(ws.references_dir() / rid)] == [25, 25, 26]


def test_progress_does_not_overwrite_a_damaged_store(tmp_path):
    ws = Workspace.init(tmp_path / "alpha")
    source = tmp_path / "plain.txt"
    source.write_text("Discussion")
    rid = ws.add([source], occurred_at="2026-08-24")
    store = ws.references_dir() / rid / "progress.json"
    store.write_text("{ damaged")
    client = TestClient(create_app(tmp_path))

    r = client.post(
        f"/refs/{rid}/progress",
        data={"home": "alpha", "day": "2026-08-25", "content": "Keep my input"},
    )
    assert r.status_code == 500
    assert "Keep my input</textarea>" in r.text
    assert "Progress is unavailable" in r.text
    assert store.read_text() == "{ damaged"
    assert f"/refs/{rid}?home=alpha" in client.get("/timeline?day=2026-08-24").text


def test_progress_writes_accumulate_and_public_cannot_append(tmp_path):
    root, rid, slug = _full_public_root(tmp_path)
    ws = Workspace.open(root / slug)
    ref_dir = ws.references_dir() / rid
    day = date(2026, 8, 25)
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda n: append_progress(ref_dir, ProgressEntry(day, str(n))), range(8)))
    assert {entry.content for entry in read_progress(ref_dir)} == set("01234567")

    client = _public_client(root)
    path = f"/refs/{rid}"
    r = client.get(path, params={"home": slug})
    assert r.status_code == 200
    assert f'action="{path}/progress' not in r.text
    r = client.post(f"{path}/progress", data={"home": slug, "day": day.isoformat(), "content": "Denied"})
    assert r.status_code == 404
    assert len(read_progress(ref_dir)) == 8
