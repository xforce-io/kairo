"""#396：plan_view_run、列表未加工标记、预览、Web 执行，以及 CLI run-view。"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from kairo.cli import app as cli_app
from kairo.refs import add_global_ref, add_tag, create_tag
from kairo.view_run import plan_view_run
from kairo.web.server import create_app
from kairo.web.tasks import TaskRegistry
from kairo.workspace import Workspace

_cli = CliRunner()

_HX = {"HX-Request": "true"}
_STREAM_RE = re.compile(r"/w/([^/\"']+)/step/([0-9a-f]+)/stream")


def _client(root):
    return TestClient(create_app(root))


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _fixture(tmp_path):
    """同一日两个 Topic 可推进，alpha 另有视图外 pending。"""
    root = tmp_path / "root"
    wa = Workspace.init(root / "alpha", topic="北港梳理")
    wb = Workspace.init(root / "beta", topic="招聘")
    wa.add(
        [_write(tmp_path / "a.txt", "A 日")],
        ref_id="in-a",
        title="A 日",
        occurred_at="2026-08-24",
    )
    wb.add(
        [_write(tmp_path / "b.txt", "B 日")],
        ref_id="in-b",
        title="B 日",
        occurred_at="2026-08-24",
    )
    wa.add(
        [_write(tmp_path / "o.txt", "外日")],
        ref_id="out-a",
        title="外日",
        occurred_at="2026-08-10",
    )
    return root, wa, wb


def test_plan_view_run_counts_out_of_view_pending(tmp_path):
    root, _, _ = _fixture(tmp_path)
    day = dt.date(2026, 8, 24)
    plan = plan_view_run(root, day, day)
    assert plan.visible_undigested == 2
    assert plan.topic_count == 2
    assert {t.slug for t in plan.topics} == {"alpha", "beta"}
    assert plan.ref_count == 3
    assert plan.ref_count > plan.visible_undigested
    assert plan.skipped_attention == ()


def test_plan_view_run_tag_filter_narrows(tmp_path):
    root, wa, _ = _fixture(tmp_path)
    create_tag(root, "能源")
    add_tag(root, home="alpha", ref_id="in-a", tag="能源")
    day = dt.date(2026, 8, 24)
    plan = plan_view_run(root, day, day, tags=["能源"])
    assert plan.visible_undigested == 1
    assert plan.topic_count == 1
    assert plan.topics[0].slug == "alpha"
    assert plan.ref_count == 2  # in-a + out-a（同 Topic 视图外）


def test_plan_view_run_global_untagged_not_in_set(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    add_global_ref(
        root,
        [_write(tmp_path / "g.txt", "孤儿")],
        ref_id="orphan",
        title="孤儿",
        occurred_at="2026-08-24",
    )
    plan = plan_view_run(root, dt.date(2026, 8, 24), dt.date(2026, 8, 24))
    assert plan.visible_undigested == 1
    assert plan.topic_count == 0
    assert plan.ref_count == 0


def test_plan_view_run_ignores_corpus_artifact_and_digested(tmp_path):
    from test_timeline_web import _write_project_artifact

    root, wa, _ = _fixture(tmp_path)
    wa.add(
        [_write(tmp_path / "c.txt", "基线")],
        ref_id="whitepaper",
        title="白皮书",
        source_class="corpus",
    )
    _write_project_artifact(root, day="2026-08-24")
    (wa.references_dir() / "in-a" / "digest.md").write_text("纪要", encoding="utf-8")
    plan = plan_view_run(root, dt.date(2026, 8, 24), dt.date(2026, 8, 24))
    assert plan.visible_undigested == 1  # 仅 in-b
    assert {t.slug for t in plan.topics} == {"beta"}
    assert plan.ref_count == 1


def test_timeline_list_marks_undigested_not_brief_or_artifact(tmp_path):
    from test_timeline_web import _write_project_artifact

    root, wa, _ = _fixture(tmp_path)
    _write_project_artifact(root, day="2026-08-24")
    (wa.references_dir() / "in-a" / "digest.md").write_text("纪要", encoding="utf-8")
    html = _client(root).get("/timeline", params={"day": "2026-08-24"}).text
    assert html.count("tl-undigested") == 1
    assert "Undigested" in html
    assert "B 日" in html
    assert 'class="tl-row tl-kind-ref undigested"' in html or "undigested" in html
    assert "in-a" in html
    # 已有 digest 的 A 日无未加工标记
    assert html.split("A 日", 1)[1].split("</a>", 1)[0].find("tl-undigested") == -1
    assert "tl-kind-artifact" in html
    assert "Process" in html
    assert "1 undigested" in html
    assert "3 refs" not in html  # 旁注不得写确认 Ref 数


def test_timeline_get_does_not_call_workspace_run_plan(tmp_path, monkeypatch):
    root, _, _ = _fixture(tmp_path)
    hits = []

    def boom(*_a, **_k):
        hits.append(1)
        raise AssertionError("GET /timeline must not call workspace_run_plan")

    monkeypatch.setattr("kairo.engine.workspace_run_plan", boom)
    monkeypatch.setattr("kairo.view_run.workspace_run_plan", boom)
    r = _client(root).get("/timeline", params={"day": "2026-08-24"})
    assert r.status_code == 200
    assert "Undigested" in r.text
    assert hits == []


def test_run_preview_json_matches_plan(tmp_path):
    root, _, _ = _fixture(tmp_path)
    before = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
    r = _client(root).get("/timeline/run-preview", params={"day": "2026-08-24"})
    assert r.status_code == 200
    body = r.json()
    assert body["visible_undigested"] == 2
    assert body["topic_count"] == 2
    assert body["ref_count"] == 3
    assert body["ref_count"] > body["visible_undigested"]
    slugs = {t["slug"] for t in body["topics"]}
    assert slugs == {"alpha", "beta"}
    after = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
    assert after == before


def test_run_preview_html_lists_counts_and_diff(tmp_path):
    root, _, _ = _fixture(tmp_path)
    r = _client(root).get(
        "/timeline/run-preview",
        params={"day": "2026-08-24"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "2 topics, 3 refs" in r.text
    assert "2 undigested in this view" in r.text
    assert "Confirm" in r.text
    assert 'class="btn" disabled' not in r.text


def test_run_preview_missing_or_bad_date_400(tmp_path):
    root, _, _ = _fixture(tmp_path)
    c = _client(root)
    assert c.get("/timeline/run-preview").status_code == 400
    assert c.get("/timeline/run-preview", params={"from": "2026-08-24"}).status_code == 400
    assert c.get("/timeline/run-preview", params={"day": "2026-02-31"}).status_code == 400


def test_run_preview_public_read_403_and_no_button(tmp_path):
    root, _, _ = _fixture(tmp_path)
    pub = TestClient(create_app(root, mode="public-read"))
    r = pub.get("/timeline", params={"day": "2026-08-24"})
    assert r.status_code == 200
    assert "Process" not in r.text
    assert "推进" not in r.text
    prev = pub.get("/timeline/run-preview", params={"day": "2026-08-24"})
    assert prev.status_code == 403


def test_run_preview_empty_set_disables_confirm(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    add_global_ref(
        root,
        [_write(tmp_path / "g.txt", "孤儿")],
        ref_id="orphan",
        title="孤儿",
        occurred_at="2026-08-24",
    )
    html = _client(root).get("/timeline", params={"day": "2026-08-24"}).text
    assert "Process" in html
    assert "1 undigested" in html
    prev = _client(root).get(
        "/timeline/run-preview",
        params={"day": "2026-08-24"},
        headers={"HX-Request": "true"},
    )
    assert prev.status_code == 200
    assert "0 topics, 0 refs" in prev.text
    assert 'class="btn" disabled' in prev.text
    assert "Ordinary process cannot clear these." in prev.text


def _drain_view_run(client, html=None, timeout=60):
    deadline = time.time() + timeout
    seen: set[str] = set()
    if html:
        m = _STREAM_RE.search(html)
        if m:
            client.get(f"/w/{m.group(1)}/step/{m.group(2)}/stream")
            seen.add(m.group(2))
    last = None
    while time.time() < deadline:
        last = client.get("/timeline/run-status")
        if "tl-run-summary" in last.text:
            return last
        m = _STREAM_RE.search(last.text)
        if m and m.group(2) not in seen:
            client.get(f"/w/{m.group(1)}/step/{m.group(2)}/stream")
            seen.add(m.group(2))
            continue
        time.sleep(0.05)
    raise AssertionError(last.text if last is not None else "no status")


def test_view_run_progress_keeps_confirm_clock():
    from kairo.web.i18n import translator
    from kairo.web.tasks import StepTask, render_progress_html

    t = translator("en")
    now = 1_000_000.0
    task = StepTask(task_id="t", slug="alpha", created_at=now - 1)
    html = render_progress_html(
        task,
        t,
        now=now,
        elapsed_from=now - 70,
        headline="北港梳理",
        index_label="1 / 2",
    )
    assert "北港梳理" in html
    assert "1 / 2" in html
    assert "1 min" in html
    assert "1s" not in html.split("run-progress-text", 1)[1]


def test_timeline_run_public_read_forbidden(tmp_path):
    root, _, _ = _fixture(tmp_path)
    pub = TestClient(create_app(root, mode="public-read"))
    r = pub.post("/timeline/run", data={"day": "2026-08-24"})
    # #200 public-read 写操作统一 fail-closed 404；GET 预览仍由本片 403。
    assert r.status_code == 404
    assert pub.get("/timeline/run-status").status_code == 403
    assert not (root / "alpha" / "references" / "in-a" / "digest.md").is_file()


def test_timeline_run_empty_set_400(tmp_path, monkeypatch):
    started: list[str] = []
    real = TaskRegistry.start

    def start(self, slug, cwd, argv, **kw):
        started.append(slug)
        return real(self, slug, cwd, argv, **kw)

    monkeypatch.setattr(TaskRegistry, "start", start)
    root = tmp_path / "root"
    root.mkdir()
    add_global_ref(
        root,
        [_write(tmp_path / "g.txt", "孤儿")],
        ref_id="orphan",
        title="孤儿",
        occurred_at="2026-08-24",
    )
    app = create_app(root)
    r = TestClient(app).post(
        "/timeline/run", data={"day": "2026-08-24"}, headers=_HX
    )
    assert r.status_code == 400
    assert "Nothing to process." in r.text
    assert started == []
    assert app.state.view_run is None


def test_timeline_run_missing_date_400(tmp_path):
    root, _, _ = _fixture(tmp_path)
    c = _client(root)
    assert c.post("/timeline/run", data={}, headers=_HX).status_code == 400
    assert c.post(
        "/timeline/run", data={"from": "2026-08-24"}, headers=_HX
    ).status_code == 400
    assert c.post(
        "/timeline/run", data={"day": "2026-02-31"}, headers=_HX
    ).status_code == 400


def test_timeline_run_processes_out_of_view_and_stays(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    root, wa, wb = _fixture(tmp_path)
    wg = Workspace.init(root / "gamma", topic="旁路")
    wg.add(
        [_write(tmp_path / "g.txt", "旁路")],
        ref_id="other-day",
        title="旁路",
        occurred_at="2026-08-11",
    )
    app = create_app(root)
    c = TestClient(app)
    r = c.post("/timeline/run", data={"day": "2026-08-24"}, headers=_HX)
    assert r.status_code == 200
    assert r.headers.get("location") is None
    assert "/w/" in r.text  # sse-connect 指向当前 slug，不是整页跳转
    assert "run-progress" in r.text
    assert "北港梳理" in r.text or "招聘" in r.text
    assert "Raw run log" in r.text
    summary = _drain_view_run(c, r.text)
    assert "Done 2" in summary.text
    assert "failed 0" in summary.text
    assert (wa.references_dir() / "in-a" / "digest.md").is_file()
    assert (wa.references_dir() / "out-a" / "digest.md").is_file()
    assert (wb.references_dir() / "in-b" / "digest.md").is_file()
    assert not (wg.references_dir() / "other-day" / "digest.md").is_file()
    page = c.get("/timeline", params={"day": "2026-08-24"})
    assert page.status_code == 200
    assert "tl-run-summary" in page.text
    assert page.text.count("tl-undigested") == 0
    assert "Process" not in page.text


def test_timeline_run_partial_failure_continues(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    root, wa, wb = _fixture(tmp_path)
    orig = TaskRegistry.start

    def start(self, slug, cwd, argv, **kw):
        if slug == "alpha":
            argv = [sys.executable, "-c", "import sys; sys.exit(1)"]
        return orig(self, slug, cwd, argv, **kw)

    monkeypatch.setattr("kairo.web.tasks.TaskRegistry.start", start)
    c = TestClient(create_app(root))
    r = c.post("/timeline/run", data={"day": "2026-08-24"}, headers=_HX)
    assert r.status_code == 200
    summary = _drain_view_run(c, r.text)
    assert "failed 1" in summary.text
    assert "Done 1" in summary.text
    assert "Failed topics" in summary.text
    assert 'href="/w/alpha"' in summary.text
    assert not (wa.references_dir() / "in-a" / "digest.md").is_file()
    assert (wb.references_dir() / "in-b" / "digest.md").is_file()


def test_timeline_run_same_view_attaches_mismatch_400(tmp_path, monkeypatch):
    orig = TaskRegistry.start

    def start(self, slug, cwd, argv, **kw):
        argv = [sys.executable, "-c", "import time; time.sleep(30)"]
        return orig(self, slug, cwd, argv, **kw)

    monkeypatch.setattr("kairo.web.tasks.TaskRegistry.start", start)
    root, _, _ = _fixture(tmp_path)
    app = create_app(root)
    c = TestClient(app)
    try:
        first = c.post("/timeline/run", data={"day": "2026-08-24"}, headers=_HX)
        assert first.status_code == 200
        tid = _STREAM_RE.search(first.text)
        assert tid
        session = app.state.view_run
        again = c.post("/timeline/run", data={"day": "2026-08-24"}, headers=_HX)
        assert again.status_code == 200
        again_tid = _STREAM_RE.search(again.text)
        assert again_tid and again_tid.group(2) == tid.group(2)
        assert app.state.view_run is session
        other = c.post(
            "/timeline/run",
            data={"from": "2026-08-10", "to": "2026-08-11"},
            headers=_HX,
        )
        assert other.status_code == 400
        page = c.get("/timeline", params={"day": "2026-08-24"})
        assert 'id="tl-run-open"' in page.text
        assert "disabled" in page.text.split('id="tl-run-open"', 1)[1].split(">", 1)[0]
        assert "run-progress" in page.text
        cancel = c.post(f"/w/{tid.group(1)}/step/{tid.group(2)}/cancel", headers=_HX)
        assert cancel.status_code == 200
        summary = _drain_view_run(c)
        assert "tl-run-summary" in summary.text
        assert "cancelled" in summary.text
        snap = app.state.view_run.snapshot()
        assert {t.slug for t in snap.cancelled_pending} == {"beta"}
    finally:
        sess = getattr(app.state, "view_run", None)
        if sess is not None:
            snap = sess.snapshot()
            if snap.current_task_id:
                sess.note_cancel(snap.current_task_id)
                app.state.registry.cancel(snap.current_task_id)
        for task in list(getattr(app.state.registry, "_tasks", {}).values()):
            if not task.done:
                app.state.registry.cancel(task.task_id)


def test_view_run_post_oob_disables_launch(tmp_path, monkeypatch):
    orig = TaskRegistry.start

    def start(self, slug, cwd, argv, **kw):
        argv = [sys.executable, "-c", "import time; time.sleep(30)"]
        return orig(self, slug, cwd, argv, **kw)

    monkeypatch.setattr("kairo.web.tasks.TaskRegistry.start", start)
    root, _, _ = _fixture(tmp_path)
    app = create_app(root)
    c = TestClient(app)
    try:
        first = c.post("/timeline/run", data={"day": "2026-08-24"}, headers=_HX)
        assert first.status_code == 200
        chunk = first.text.split('id="tl-run-open"', 1)
        assert len(chunk) == 2
        attrs = chunk[1].split(">", 1)[0]
        assert "disabled" in attrs
        assert 'hx-swap-oob="true"' in first.text
        assert 'id="tl-run-open-wrap"' in first.text
    finally:
        sess = getattr(app.state, "view_run", None)
        if sess is not None:
            snap = sess.snapshot()
            if snap.current_task_id:
                sess.note_cancel(snap.current_task_id)
                app.state.registry.cancel(snap.current_task_id)
        for task in list(getattr(app.state.registry, "_tasks", {}).values()):
            if not task.done:
                app.state.registry.cancel(task.task_id)


def test_cancel_finished_topic_does_not_abort_rest(tmp_path, monkeypatch):
    orig = TaskRegistry.start

    def start(self, slug, cwd, argv, **kw):
        if slug == "alpha":
            argv = [sys.executable, "-c", "import sys; sys.exit(0)"]
        else:
            argv = [sys.executable, "-c", "import time; time.sleep(0.4)"]
        return orig(self, slug, cwd, argv, **kw)

    monkeypatch.setattr("kairo.web.tasks.TaskRegistry.start", start)
    root, _, wb = _fixture(tmp_path)
    app = create_app(root)
    c = TestClient(app)
    try:
        r = c.post("/timeline/run", data={"day": "2026-08-24"}, headers=_HX)
        assert r.status_code == 200
        first = _STREAM_RE.search(r.text)
        assert first
        alpha_tid = first.group(2)
        deadline = time.time() + 10
        while time.time() < deadline:
            task = app.state.registry.get(alpha_tid)
            if task is not None and task.done:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("alpha never finished")
        cancel = c.post(f"/w/alpha/step/{alpha_tid}/cancel", headers=_HX)
        assert cancel.status_code == 200
        summary = _drain_view_run(c, r.text)
        assert "tl-run-summary" in summary.text
        snap = app.state.view_run.snapshot()
        assert {t.slug for t in snap.cancelled_pending} == set()
        assert "beta" in {t.slug for t in snap.ran} or (
            wb.references_dir() / "in-b" / "digest.md"
        ).is_file() or snap.current_slug == "beta" or any(
            t.slug == "beta" for t in snap.ran + snap.failed
        )
        assert not snap.cancelled_pending
    finally:
        sess = getattr(app.state, "view_run", None)
        if sess is not None:
            snap = sess.snapshot()
            if snap.current_task_id:
                sess.note_cancel(snap.current_task_id)
                app.state.registry.cancel(snap.current_task_id)
        for task in list(getattr(app.state.registry, "_tasks", {}).values()):
            if not task.done:
                app.state.registry.cancel(task.task_id)


def test_timeline_run_attaches_existing_slug_job(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    from kairo.web.tasks import StepTask

    root, _, _ = _fixture(tmp_path)
    app = create_app(root)
    fake = StepTask(task_id="attached", slug="alpha", done=False)
    app.state.registry._tasks["attached"] = fake
    app.state.registry._running_by_slug["alpha"] = "attached"
    c = TestClient(app)
    r = c.post("/timeline/run", data={"day": "2026-08-24"}, headers=_HX)
    assert r.status_code == 200
    assert "attached" in r.text
    fake.done = True
    fake.exit_code = 0
    summary = _drain_view_run(c, r.text)
    assert "tl-run-summary" in summary.text
    assert (root / "beta" / "references" / "in-b" / "digest.md").is_file()


def test_timeline_run_does_not_write_review(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    root, wa, _ = _fixture(tmp_path)
    before = {
        p.name
        for p in wa.references_dir().iterdir()
        if p.is_dir()
    }
    c = TestClient(create_app(root))
    r = c.post("/timeline/run", data={"day": "2026-08-24"}, headers=_HX)
    _drain_view_run(c, r.text)
    after = {
        p.name
        for p in wa.references_dir().iterdir()
        if p.is_dir()
    }
    assert after == before
    assert not any("review" in p.name.lower() for p in wa.references_dir().iterdir())


def test_timeline_run_and_review_are_separate_forms(tmp_path):
    root, wa, _ = _fixture(tmp_path)
    (wa.references_dir() / "in-a" / "digest.md").write_text("纪要", encoding="utf-8")
    c = _client(root)
    page = c.get(
        "/timeline", params={"from": "2026-08-10", "to": "2026-08-24"}
    ).text
    assert 'action="/timeline/review"' in page
    assert "Process" in page
    prev = c.get(
        "/timeline/run-preview",
        params={"from": "2026-08-10", "to": "2026-08-24"},
        headers=_HX,
    )
    assert prev.status_code == 200
    assert 'action="/timeline/run"' in prev.text
    assert 'hx-post="/timeline/run"' in prev.text
    assert 'hx-target="#tl-run-area"' in prev.text
    assert "/timeline/review" not in prev.text


def test_timeline_run_tag_narrows_started_topics(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    root, _, _ = _fixture(tmp_path)
    create_tag(root, "能源")
    add_tag(root, home="alpha", ref_id="in-a", tag="能源")
    started: list[str] = []
    orig = TaskRegistry.start

    def start(self, slug, cwd, argv, **kw):
        started.append(slug)
        argv = [sys.executable, "-c", "print('ok')"]
        return orig(self, slug, cwd, argv, **kw)

    monkeypatch.setattr(TaskRegistry, "start", start)
    app = create_app(root)
    c = TestClient(app)
    r = c.post(
        "/timeline/run",
        data={"day": "2026-08-24", "tag": "能源"},
        headers=_HX,
    )
    assert r.status_code == 200
    summary = _drain_view_run(c, r.text)
    assert started == ["alpha"]
    assert "Done 1" in summary.text


def _in_a(root):
    return root / "alpha" / "references" / "in-a" / "digest.md"


def _forbid_run(monkeypatch):
    """Preview / usage paths must not call provider or run_workspace."""
    import kairo.cli as cli

    called: list[str] = []

    def provider(**_kw):
        called.append("provider")
        raise AssertionError("select_provider")

    def run(*_a, **_kw):
        called.append("run")
        raise AssertionError("run_workspace")

    monkeypatch.setattr(cli, "select_provider", provider)
    monkeypatch.setattr(cli, "engine_run_workspace", run)
    return called


def test_run_view_non_tty_without_yes_exits_2_zero_consume(tmp_path, monkeypatch):
    root, _, _ = _fixture(tmp_path)
    called = _forbid_run(monkeypatch)
    result = _cli.invoke(cli_app, ["run-view", str(root), "--day", "2026-08-24"])
    assert result.exit_code == 2, result.output
    assert "2 topics, 3 refs" in result.output
    assert "alpha" in result.output and "beta" in result.output
    assert called == []
    assert not _in_a(root).is_file()
    assert not (root / "alpha" / "references" / "out-a" / "digest.md").is_file()
    assert not (root / "beta" / "references" / "in-b" / "digest.md").is_file()


def test_run_view_empty_set_exits_0(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    add_global_ref(
        root,
        [_write(tmp_path / "g.txt", "孤儿")],
        ref_id="orphan",
        title="孤儿",
        occurred_at="2026-08-24",
    )
    called = _forbid_run(monkeypatch)
    result = _cli.invoke(cli_app, ["run-view", str(root), "--day", "2026-08-24"])
    assert result.exit_code == 0, result.output
    assert "0 topics, 0 refs" in result.output
    assert called == []


def test_run_view_missing_or_partial_or_illegal_date_exits_2(tmp_path, monkeypatch):
    root, _, _ = _fixture(tmp_path)
    called = _forbid_run(monkeypatch)
    missing = _cli.invoke(cli_app, ["run-view", str(root)])
    assert missing.exit_code == 2
    only_from = _cli.invoke(
        cli_app, ["run-view", str(root), "--from", "2026-08-24"]
    )
    assert only_from.exit_code == 2
    only_to = _cli.invoke(cli_app, ["run-view", str(root), "--to", "2026-08-24"])
    assert only_to.exit_code == 2
    bad = _cli.invoke(cli_app, ["run-view", str(root), "--day", "2026-02-31"])
    assert bad.exit_code == 2
    assert called == []
    assert not _in_a(root).is_file()


def test_run_view_day_and_range_are_mutex(tmp_path, monkeypatch):
    root, _, _ = _fixture(tmp_path)
    called = _forbid_run(monkeypatch)
    result = _cli.invoke(
        cli_app,
        [
            "run-view",
            str(root),
            "--day",
            "2026-08-24",
            "--from",
            "2026-08-10",
            "--to",
            "2026-08-24",
        ],
    )
    assert result.exit_code == 2
    assert called == []
    assert not _in_a(root).is_file()


def test_run_view_yes_runs_in_set_including_out_of_view(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    root, wa, wb = _fixture(tmp_path)
    wg = Workspace.init(root / "gamma", topic="旁路")
    wg.add(
        [_write(tmp_path / "g.txt", "旁路")],
        ref_id="other-day",
        title="旁路",
        occurred_at="2026-08-11",
    )
    import kairo.cli as cli

    called: list[str] = []
    real = cli.engine_run_workspace

    def wrap(ws, provider, **kw):
        called.append(ws.root.name)
        return real(ws, provider, **kw)

    monkeypatch.setattr(cli, "engine_run_workspace", wrap)
    result = _cli.invoke(
        cli_app, ["run-view", str(root), "--day", "2026-08-24", "--yes"]
    )
    assert result.exit_code == 0, result.output
    assert called == ["alpha", "beta"]
    assert (wa.references_dir() / "in-a" / "digest.md").is_file()
    assert (wa.references_dir() / "out-a" / "digest.md").is_file()
    assert (wb.references_dir() / "in-b" / "digest.md").is_file()
    assert not (wg.references_dir() / "other-day" / "digest.md").is_file()


def test_run_view_json_preview_matches_plan(tmp_path, monkeypatch):
    root, _, _ = _fixture(tmp_path)
    day = dt.date(2026, 8, 24)
    plan = plan_view_run(root, day, day)
    called = _forbid_run(monkeypatch)
    result = _cli.invoke(
        cli_app, ["run-view", str(root), "--day", "2026-08-24", "--json"]
    )
    assert result.exit_code == 2, result.output
    body = json.loads(result.output)
    assert body == plan.as_json()
    assert called == []
    assert not _in_a(root).is_file()


def test_run_view_partial_failure_continues(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    root, _, _ = _fixture(tmp_path)
    import kairo.cli as cli

    called: list[str] = []
    real = cli.engine_run_workspace

    def wrap(ws, provider, **kw):
        called.append(ws.root.name)
        if ws.root.name == "alpha":
            raise RuntimeError("boom")
        return real(ws, provider, **kw)

    monkeypatch.setattr(cli, "engine_run_workspace", wrap)
    result = _cli.invoke(
        cli_app, ["run-view", str(root), "--day", "2026-08-24", "--yes"]
    )
    assert result.exit_code == 1, result.output
    assert called == ["alpha", "beta"]


def test_run_view_help_has_no_tag_option():
    result = _cli.invoke(cli_app, ["run-view", "--help"])
    assert result.exit_code == 0, result.output
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert "--tag" not in plain
    assert "run --all" in plain


def test_run_view_yes_json_includes_ran_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    root, _, _ = _fixture(tmp_path)
    result = _cli.invoke(
        cli_app, ["run-view", str(root), "--day", "2026-08-24", "--yes", "--json"]
    )
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["topic_count"] == 2
    assert body["ref_count"] == 3
    assert body["ran"] == ["alpha", "beta"]
    assert body["failed"] == []
    assert body["cancelled"] == []
    assert body["skipped_attention"] == []


def test_run_view_yes_all_attention_exits_0(tmp_path, monkeypatch):
    root, _, _ = _fixture(tmp_path)
    monkeypatch.setattr(
        "kairo.view_run.workspace_run_plan",
        lambda ws, catalog=None: {"mode": "attention"},
    )
    called = _forbid_run(monkeypatch)
    result = _cli.invoke(
        cli_app, ["run-view", str(root), "--day", "2026-08-24", "--yes"]
    )
    assert result.exit_code == 0, result.output
    assert "0 topics, 0 refs" in result.output
    plan = plan_view_run(root, dt.date(2026, 8, 24), dt.date(2026, 8, 24))
    assert plan.topic_count == 0
    assert plan.skipped_attention
    assert called == []


def test_run_view_yes_does_not_use_task_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    root, _, _ = _fixture(tmp_path)
    hits: list[str] = []

    def boom(*_a, **_k):
        hits.append("start")
        raise AssertionError("CLI run-view must not use TaskRegistry")

    monkeypatch.setattr("kairo.web.tasks.TaskRegistry.start", boom)
    result = _cli.invoke(
        cli_app, ["run-view", str(root), "--day", "2026-08-24", "--yes"]
    )
    assert result.exit_code == 0, result.output
    assert hits == []
    assert _in_a(root).is_file()


def test_run_view_tty_decline_exits_0_zero_consume(tmp_path, monkeypatch):
    root, _, _ = _fixture(tmp_path)
    called = _forbid_run(monkeypatch)
    monkeypatch.setattr("kairo.cli._stdin_isatty", lambda: True)
    result = _cli.invoke(
        cli_app, ["run-view", str(root), "--day", "2026-08-24"], input="n\n"
    )
    assert result.exit_code == 0, result.output
    assert "2 topics, 3 refs" in result.output
    assert called == []
    assert not _in_a(root).is_file()
