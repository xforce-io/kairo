"""#396 只读层：plan_view_run、列表未加工标记、GET /timeline/run-preview。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from fastapi.testclient import TestClient

from kairo.refs import add_global_ref, add_tag, create_tag
from kairo.view_run import plan_view_run
from kairo.web.server import create_app
from kairo.workspace import Workspace


def _client(root):
    return TestClient(create_app(root))


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _fixture(tmp_path):
    """同一日两个 Topic 可推进，alpha 另有视图外 pending。"""
    root = tmp_path / "root"
    wa = Workspace.init(root / "alpha", topic="能源梳理")
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
    assert "disabled" not in r.text


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
    assert "disabled" in prev.text
    assert "Ordinary process cannot clear these." in prev.text
