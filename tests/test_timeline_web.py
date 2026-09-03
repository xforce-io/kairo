import re

from fastapi.testclient import TestClient

from kairo.web.discovery import scan_workspaces
from kairo.web.server import create_app
from kairo.workspace import Workspace


def _meta_cells(html: str) -> list[str]:
    return re.findall(r'<span class="tl-meta">([^<]*)</span>', html)


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
    wa.add(
        [tmp_path / "m.txt"],
        ref_id="2026-08-25-weekly",
        title="候选人沟通",
        occurred_at="2026-08-24",
    )
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


def test_timeline_range_lists_inclusive_and_hides_unknown(tmp_path):
    root, wa, _ = _two_ws(tmp_path)
    (wa.references_dir() / "2026-08-25-weekly" / "digest.md").write_text("周会")
    c = _client(root)
    r = c.get("/timeline", params={"from": "2026-08-24", "to": "2026-08-25"})
    assert r.status_code == 200
    assert "候选人沟通" in r.text
    assert "能源梳理" in r.text
    assert "2026-08-24" in r.text
    assert 'href="/w/alpha?ref=2026-08-25-weekly"' in r.text
    assert "2026-08-25-weekly" not in _meta_cells(r.text)
    assert "notes-candidate" not in r.text
    assert "from=2026-08-24" in r.text
    assert "写这段回顾" in r.text or "Write this review" in r.text
    assert "<select" not in r.text
    assert 'name="workspace"' not in r.text
    assert 'name="topic"' not in r.text
    assert 'id="rev-topic"' not in r.text
    day = c.get("/timeline", params={"day": "2026-08-24"})
    assert day.status_code == 200
    assert "08-24" in _meta_cells(day.text)


def test_timeline_empty_range_has_no_write_button(tmp_path):
    root, _, _ = _two_ws(tmp_path)
    r = _client(root).get("/timeline", params={"from": "2026-07-01", "to": "2026-07-02"})
    assert r.status_code == 200
    assert "这段时间没有观测" in r.text or "Nothing in this range" in r.text
    assert "写这段回顾" not in r.text
    assert "Write this review" not in r.text


def test_timeline_too_long_disables_write(tmp_path):
    root, wa, _ = _two_ws(tmp_path)
    (wa.references_dir() / "2026-08-25-weekly" / "digest.md").write_text("周会")
    r = _client(root).get("/timeline", params={"from": "2026-08-01", "to": "2026-09-01"})
    assert r.status_code == 200
    assert "2026-08-25-weekly" in r.text
    assert "disabled" in r.text
    assert "31" in r.text
    assert 'action="/timeline/review"' not in r.text


def test_timeline_range_missing_bound_400(tmp_path):
    root, _, _ = _two_ws(tmp_path)
    assert _client(root).get("/timeline", params={"from": "2026-08-18"}).status_code == 400


def test_timeline_review_writes_stream(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    root, wa, _ = _two_ws(tmp_path)
    rid = "2026-08-25-weekly"
    (wa.references_dir() / rid / "digest.md").write_text("周会结论")
    before = len(wa.list_reference_ids())
    c = _client(root)
    r = c.post(
        "/timeline/review",
        data={"from": "2026-08-24", "to": "2026-08-24"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/w/%E6%80%BB%E7%BB%93") or r.headers[
        "location"
    ].startswith("/w/总结")
    dest = Workspace.open(root / "总结")
    ids = dest.list_reference_ids()
    assert ids
    man = dest.read_manifest(ids[-1])
    assert man.occurred_at == "2026-08-24"
    assert "2026-08-24" in (man.title or "")
    assert any(f.role == "source_text" for f in man.forms)
    orig = (wa.references_dir() / rid / "digest.md").read_text()
    assert orig == "周会结论"
    assert len(wa.list_reference_ids()) == before
    r2 = c.post(
        "/timeline/review",
        data={"from": "2026-08-24", "to": "2026-08-24"},
        follow_redirects=False,
    )
    assert r2.status_code == 303
    journals = [s for s in scan_workspaces(root) if s.slug == "总结" or s.topic == "总结"]
    assert len(journals) == 1
    dest = Workspace.open(root / journals[0].slug)
    assert dest.list_reference_ids() == ids
    assert r2.headers["location"].endswith("ref=" + ids[-1]) or ids[-1] in r2.headers["location"]


def test_timeline_range_omits_journal_reviews(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    root, wa, _ = _two_ws(tmp_path)
    (wa.references_dir() / "2026-08-25-weekly" / "digest.md").write_text("周会")
    c = _client(root)
    c.post(
        "/timeline/review",
        data={"from": "2026-08-24", "to": "2026-08-25"},
        follow_redirects=False,
    )
    r = c.get("/timeline", params={"from": "2026-08-24", "to": "2026-08-25"})
    assert r.status_code == 200
    assert "候选人沟通" in r.text
    assert "2026-08-24～2026-08-25 回顾" not in r.text
    assert ">总结<" not in r.text


def test_timeline_review_workspace_escape(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    root, wa, _ = _two_ws(tmp_path)
    (wa.references_dir() / "2026-08-25-weekly" / "digest.md").write_text("周会结论")
    dest = Workspace.init(root / "回顾", topic="回顾")
    r = _client(root).post(
        "/timeline/review",
        data={"from": "2026-08-24", "to": "2026-08-24", "workspace": "回顾"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/w/")
    ids = dest.list_reference_ids()
    assert ids
    assert dest.read_manifest(ids[-1]).occurred_at == "2026-08-24"
    assert not (root / "总结").exists()


def test_timeline_review_too_long_skips_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    called = []

    def boom(*_a, **_k):
        called.append(1)
        raise AssertionError("provider must not run")

    monkeypatch.setattr("kairo.web.views.generate_review_body", boom)
    root, wa, _ = _two_ws(tmp_path)
    (wa.references_dir() / "2026-08-25-weekly" / "digest.md").write_text("周会")
    r = _client(root).post(
        "/timeline/review",
        data={"from": "2026-08-01", "to": "2026-09-01"},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert called == []
    assert not (root / "总结").exists()


def test_timeline_review_failure_stays_in_console_shell(tmp_path, monkeypatch):
    """#228 S3:浏览器原生表单失败返回本地化完整页，机器请求仍是 JSON。"""
    root, wa, _ = _two_ws(tmp_path)
    (wa.references_dir() / "2026-08-25-weekly" / "digest.md").write_text("周会")

    def fail(*_args, **_kwargs):
        from kairo.review import ReviewError

        raise ReviewError("empty")

    monkeypatch.setattr("kairo.web.views.generate_review_body", fail)
    data = {"from": "2026-08-24", "to": "2026-08-25"}
    client = _client(root)
    html = client.post(
        "/timeline/review",
        data=data,
        headers={"accept": "text/html", "accept-language": "zh"},
    )
    assert html.status_code == 400
    assert "<!doctype html>" in html.text and "kairo" in html.text
    assert "操作未完成" in html.text and "返回主题" in html.text
    assert not html.text.lstrip().startswith("{")

    machine = client.post(
        "/timeline/review", data=data, headers={"accept": "application/json"}
    )
    assert machine.status_code == 400
    assert machine.json()["detail"]


def test_workspace_ref_query_selects(tmp_path):
    root, _, _ = _two_ws(tmp_path)
    r = _client(root).get("/w/alpha", params={"ref": "2026-08-25-weekly"})
    assert r.status_code == 200
    assert 'hx-get="/w/alpha/ref/2026-08-25-weekly"' in r.text


def test_nav_links_on_dashboard(tmp_path):
    r = _client(tmp_path).get("/")
    assert r.status_code == 200
    assert 'href="/timeline"' in r.text


def test_timeline_review_submit_label_restores_on_pageshow(tmp_path):
    """#210 S2: 提交失败/Back 不得把写回顾按钮冻在 Running…。"""
    root, wa, _ = _two_ws(tmp_path)
    (wa.references_dir() / "2026-08-25-weekly" / "digest.md").write_text("周会")
    r = _client(root).get("/timeline", params={"from": "2026-08-24", "to": "2026-08-25"})
    assert r.status_code == 200
    html = r.text
    assert 'action="/timeline/review"' in html
    assert "Write this review" in html or "写这段回顾" in html
    assert 'data-review-label="' in html
    assert "pageshow" in html
    assert "data-review-label" in html.split("pageshow", 1)[-1]
    btn = re.search(
        r'<button[^>]*type="submit"[^>]*data-review-label="([^"]+)"[^>]*>',
        html,
    )
    assert btn is not None
    label = btn.group(1)
    assert label in ("Write this review", "写这段回顾")
    assert "Running" not in label
    assert "运行中" not in label
