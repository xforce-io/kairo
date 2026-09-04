"""#262: Topic 包含规则在右栏底部默认折叠，不占左栏导航。"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from kairo.refs import create_tag, include_tags_of, set_include_tags
from kairo.web.server import create_app
from kairo.workspace import Workspace
from test_public_read import _full_public_root, _public_client


def _client(root):
    return TestClient(create_app(root))


def _topic(tmp_path):
    serve = tmp_path / "root"
    serve.mkdir()
    Workspace.init(serve / "energy", topic="能源梳理")
    create_tag(serve, "流程质量")
    create_tag(serve, "能源梳理")
    set_include_tags(serve, "energy", ["能源梳理"])
    return serve


def _aside(html: str, cls: str) -> str:
    match = re.search(rf'<aside class="{cls}">([\s\S]*?)</aside>', html)
    assert match is not None, f"missing aside.{cls}"
    return match.group(1)


def test_include_rules_are_collapsed_at_bottom_of_right_panel(tmp_path):
    html = _client(_topic(tmp_path)).get("/w/energy").text
    nav = _aside(html, "pane-nav")
    panel = _aside(html, "pane-panel")
    assert 'action="/w/energy/include-tags"' not in nav
    assert 'id="include-tags"' not in nav
    assert "保存规则" not in nav
    assert 'action="/w/energy/include-tags"' in panel
    assert 'id="include-tags"' in panel
    assert "保存规则" in panel
    details = re.search(r"<details\b[^>]*>", panel)
    assert details is not None
    assert "open" not in details.group(0)
    inner = re.search(r"<details\b[^>]*>([\s\S]*?)</details>", panel)
    assert inner is not None
    assert 'id="include-tags"' in inner.group(1)
    assert "保存规则" in inner.group(1)
    assert 'action="/w/energy/include-tags"' in inner.group(1)
    meta_at = panel.find('id="meta"')
    details_at = panel.find("<details")
    assert meta_at != -1 and details_at != -1 and details_at > meta_at


def test_include_tags_form_post_still_saves(tmp_path):
    serve = _topic(tmp_path)
    r = _client(serve).post("/w/energy/include-tags", data={"tag": ["流程质量"]})
    assert r.status_code == 200
    assert r.headers.get("hx-redirect") == "/w/energy"
    assert include_tags_of(Workspace.open(serve / "energy")) == ["流程质量"]


def test_public_read_topic_page_has_no_include_rules_editor(tmp_path):
    root, _rid, slug = _full_public_root(tmp_path)
    create_tag(root, "shared")
    set_include_tags(root, slug, ["shared"])
    page = _public_client(root).get(f"/w/{slug}")
    assert page.status_code == 200
    assert "保存规则" not in page.text
    assert 'id="include-tags"' not in page.text
    assert "/include-tags" not in page.text
