"""#292 Console GET pages: identity lists stay cheap; catalog/Tag fan-out is bounded."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from fastapi.testclient import TestClient

from kairo.engine import pending as engine_pending
from kairo.models import ProductState
from kairo.projects import create_project
from kairo.refs import add_tag, create_tag, set_include_tags
from kairo.web import discovery as discovery_mod
from kairo.web.server import create_app
from kairo.workspace import Workspace

TOPIC_N = 8
EXTRA_TAGS = ("tag-a", "tag-b", "tag-c", "tag-d", "tag-e", "tag-f")
# One GET may scan the full Ref catalog a couple of times (members + pending).
# It must not scale with Topic count or pipeline rule count.
MAX_CATALOG_SCANS = 3


def _client(root: Path) -> TestClient:
    return TestClient(create_app(root))


def _seed(root: Path) -> dict:
    workspaces = []
    for i in range(TOPIC_N):
        workspaces.append(Workspace.init(root / f"topic-{i}", topic=f"Topic {i}"))
    note = root / "note.txt"
    note.write_text("meeting notes", encoding="utf-8")
    ref_id = workspaces[0].add([note])
    state = workspaces[1].read_state()
    state.products["x"] = ProductState(input_hash="h", status="blocked", reason="asr-failed")
    workspaces[1].write_state(state)
    for tag in EXTRA_TAGS:
        create_tag(root, tag)
    create_tag(root, "Topic 0")
    create_tag(root, "Topic 2")
    add_tag(root, home="topic-0", ref_id=ref_id, tag="tag-a")
    set_include_tags(root, "topic-2", ["tag-a"])
    project = create_project(root, "Latency Project")
    return {"ref_id": ref_id, "project_id": project.id}


def _install_counters(monkeypatch, serve: Path) -> Counter:
    counts: Counter = Counter()
    orig_pending = engine_pending
    orig_summarize = discovery_mod.summarize
    orig_list_all_refs = __import__("kairo.refs", fromlist=["list_all_refs"]).list_all_refs
    orig_open = Workspace.open

    def counting_pending(ws, *args, **kwargs):
        counts["pending"] += 1
        return orig_pending(ws, *args, **kwargs)

    def counting_summarize(ws, *args, **kwargs):
        counts["summarize"] += 1
        return orig_summarize(ws, *args, **kwargs)

    def counting_list_all_refs(root, *args, **kwargs):
        counts["list_all_refs"] += 1
        return orig_list_all_refs(root, *args, **kwargs)

    def counting_open(path):
        opened = Path(path)
        if opened.parent.resolve() == Path(serve).resolve() and not opened.name.startswith("."):
            counts["topic_open"] += 1
        return orig_open(path)

    monkeypatch.setattr("kairo.engine.pending", counting_pending)
    monkeypatch.setattr(discovery_mod, "pending", counting_pending)
    monkeypatch.setattr(discovery_mod, "summarize", counting_summarize)
    monkeypatch.setattr("kairo.refs.list_all_refs", counting_list_all_refs)
    monkeypatch.setattr(Workspace, "open", counting_open)
    return counts


def test_identity_pages_skip_per_topic_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    seed = _seed(tmp_path)
    counts = _install_counters(monkeypatch, tmp_path)
    client = _client(tmp_path)

    knowledge = client.get("/knowledge")
    glossary = client.get("/glossary", follow_redirects=True)
    project = client.get(f"/projects/{seed['project_id']}")

    assert knowledge.status_code == 200
    assert glossary.status_code == 200
    assert project.status_code == 200
    for i in range(TOPIC_N):
        slug = f"topic-{i}"
        name = f"Topic {i}"
        assert slug in knowledge.text
        assert slug in glossary.text
        assert name in project.text
        assert f'value="{slug}"' in project.text

    assert counts["pending"] == 0
    assert counts["summarize"] == 0


def test_get_pages_bound_full_catalog_scans(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    seed = _seed(tmp_path)
    counts = _install_counters(monkeypatch, tmp_path)
    client = _client(tmp_path)

    pages = {
        "home": client.get("/"),
        "knowledge": client.get("/knowledge"),
        "project": client.get(f"/projects/{seed['project_id']}"),
        "topic": client.get("/w/topic-0"),
    }
    for name, response in pages.items():
        assert response.status_code == 200, name

    # Re-count per request with a fresh counter by hitting one at a time.
    per_request = {}
    for path, key in (
        ("/", "home"),
        ("/knowledge", "knowledge"),
        (f"/projects/{seed['project_id']}", "project"),
        ("/w/topic-0", "topic"),
    ):
        counts.clear()
        assert client.get(path).status_code == 200
        per_request[key] = counts["list_all_refs"]
        assert counts["list_all_refs"] <= MAX_CATALOG_SCANS, (key, dict(counts))
        assert counts["list_all_refs"] < TOPIC_N, (key, dict(counts))

    home = pages["home"].text
    assert "Topic 0" in home
    assert "1 Ref" in home
    assert "Needs attention" in home
    assert "New or changed materials are ready to be processed." in home
    assert "asr-failed" not in home or "Needs attention" in home

    topic = pages["topic"].text
    assert "▶ Run" in topic
    assert "topic-0" in topic or "Topic 0" in topic

    assert per_request["home"] <= MAX_CATALOG_SCANS
    assert per_request["knowledge"] <= MAX_CATALOG_SCANS
    assert per_request["project"] <= MAX_CATALOG_SCANS
    assert per_request["topic"] <= MAX_CATALOG_SCANS


def test_settings_tag_usage_opens_each_topic_once(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    _seed(tmp_path)
    counts = _install_counters(monkeypatch, tmp_path)
    client = _client(tmp_path)
    response = client.get("/settings")
    assert response.status_code == 200
    html = response.text
    assert "tag-a" in html
    assert "Ref 1" in html
    assert "规则 1" in html
    assert "Topic 名称" in html
    n_tags = html.count("<strong>tag-") + html.count("<strong>Topic ")
    assert n_tags >= len(EXTRA_TAGS)
    assert counts["topic_open"] <= TOPIC_N * 2
    assert counts["topic_open"] < n_tags * TOPIC_N
    assert counts["topic_open"] < len(EXTRA_TAGS) * TOPIC_N
