"""#348 四个一级入口共用 page-toolbar：左找/筛，右主操作，教学文案不进页头。"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from kairo.web.server import create_app
from kairo.workspace import Workspace


def _html(root, path: str) -> str:
    return TestClient(create_app(root)).get(
        path, headers={"accept-language": "en"}
    ).text


def _class_block(html: str, cls: str) -> str:
    m = re.search(
        rf'<([a-z0-9]+)([^>]*\sclass="(?:[^"]*\s)?{re.escape(cls)}(?:\s[^"]*)?"[^>]*)>',
        html,
        re.I,
    )
    assert m, f"missing class {cls}"
    tag = m.group(1).lower()
    start = m.start()
    if m.group(0).endswith("/>"):
        return html[start : m.end()]
    depth = 1
    for mm in re.compile(rf"</?{tag}\b[^>]*>", re.I).finditer(html, m.end()):
        token = mm.group(0)
        if token.startswith("</"):
            depth -= 1
            if depth == 0:
                return html[start : mm.end()]
        elif not token.endswith("/>"):
            depth += 1
    raise AssertionError(f"unclosed {cls}")


def _visible_text(html: str) -> str:
    html = re.sub(
        r'<[^>]*class="[^"]*\bsr-only\b[^"]*"[^>]*>.*?</[^>]+>',
        " ",
        html,
        flags=re.S,
    )
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html).strip()


def test_list_pages_share_one_page_toolbar(tmp_path):
    Workspace.init(tmp_path / "energy", topic="能源梳理")
    for path in ("/", "/projects", "/timeline", "/knowledge"):
        html = _html(tmp_path, path)
        assert html.count('class="page-toolbar"') == 1
        _class_block(html, "page-toolbar")


def test_topics_toolbar_find_left_create_right_without_tag_lesson(tmp_path):
    Workspace.init(tmp_path / "energy", topic="能源梳理")
    html = _html(tmp_path, "/")
    bar = _class_block(html, "page-toolbar")
    find = _class_block(bar, "page-toolbar-find")
    action = _class_block(bar, "page-toolbar-action")
    assert bar.find("page-toolbar-find") < bar.find("page-toolbar-action")
    assert 'name="q"' in find
    assert "Attention" in find and "Blocked" in find
    assert 'name="topic"' in action
    assert 'placeholder="New topic"' in action
    visible = _visible_text(bar)
    assert "same-named Tag" not in visible
    assert "Creating a topic also" not in visible
    assert "same-named Tag" in html


def test_projects_toolbar_create_without_repeating_nav_title(tmp_path):
    Workspace.init(tmp_path / "energy", topic="能源梳理")
    html = _html(tmp_path, "/projects")
    bar = _class_block(html, "page-toolbar")
    action = _class_block(bar, "page-toolbar-action")
    assert "Create project" in _visible_text(action)
    assert "page-toolbar-find" not in bar
    body = html.split("</header>", 1)[1]
    assert 'class="obj-kicker"' not in body
    assert 'class="obj-title"' not in body
    assert "Connect research topics" not in _visible_text(bar)


def test_projects_create_from_toolbar(tmp_path):
    client = TestClient(create_app(tmp_path))
    created = client.post(
        "/projects",
        data={"name": "能源团队管理"},
        headers={"accept-language": "en"},
        follow_redirects=False,
    )
    assert created.status_code in (200, 302, 303)
    html = client.get("/projects", headers={"accept-language": "en"}).text
    assert "能源团队管理" in html
    bar = _class_block(html, "page-toolbar")
    assert "Create project" in _visible_text(bar)


def test_timeline_tag_filter_sits_in_find_cluster(tmp_path):
    Workspace.init(tmp_path / "energy", topic="能源梳理")
    html = _html(tmp_path, "/timeline")
    bar = _class_block(html, "page-toolbar")
    find = _class_block(bar, "page-toolbar-find")
    assert 'class="tl-modes"' in find
    assert 'class="tl-tags"' in find
    assert find.find("tl-modes") < find.find("tl-tags")
    assert "page-toolbar-action" not in bar
    form = find.split('<form class="tl-tags"', 1)[1].split("</form>", 1)[0]
    assert form.count('type="submit"') == 1


def test_knowledge_toolbar_filters_without_injection_lesson(tmp_path):
    Workspace.init(tmp_path / "energy", topic="能源梳理")
    html = _html(tmp_path, "/knowledge")
    bar = _class_block(html, "page-toolbar")
    find = _class_block(bar, "page-toolbar-find")
    assert 'class="knowledge-picker"' in find
    assert 'class="queue-nav"' in find
    visible = _visible_text(bar)
    assert "injected only when text matches" not in visible
    assert 'class="dash-head"' not in html
    assert "Confirmed knowledge is injected only when text matches" in html
