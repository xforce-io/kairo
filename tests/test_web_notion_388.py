"""#388 Console: Notion paste hint, reader label, ProjectError surfacing."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from kairo.project_materials import write_cache
from kairo.projects import DataSource, ProjectError, create_project, get_project, save_project
from kairo.readers import KIND_PAGE
from kairo.web.i18n import CATALOG
from kairo.web.server import create_app
from kairo.web.views import _project_error_text

PAGE_HEX = "a" * 32
NOTION_APP = f"https://app.notion.com/p/{PAGE_HEX}"
NOTION_SO = f"https://www.notion.so/Product-{PAGE_HEX}"


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))


def _client(root):
    return TestClient(create_app(root))


def _fixture_notion_ds(root, *, url=NOTION_SO):
    project = create_project(root, "能源项目")
    ds = DataSource(
        id="ds-notion-1",
        connection_id="notion",
        url=url,
        kind=KIND_PAGE,
        purpose="项目材料",
        name="材料清单",
        reader="notion",
    )
    project.datasources.append(ds)
    save_project(root, project)
    write_cache(root, project.id, ds, "# 材料清单\n\n现场装机 80 MW。\n")
    return project, ds


def test_paste_hint_is_notion_url_en_and_zh():
    en = CATALOG["en"]["proj.paste_url"]
    zh = CATALOG["zh"]["proj.paste_url"]
    for text in (en, zh):
        assert "mail://" not in text
        assert "imap://" not in text
        assert "app.notion.com" in text
        assert "/p/{id}" in text
    assert CATALOG["en"]["proj.reader_notion"] == "Notion"
    assert CATALOG["zh"]["proj.reader_notion"] == "Notion"


def test_add_form_and_settings_copy_en_zh(tmp_path):
    project = create_project(tmp_path, "能源项目")
    client = _client(tmp_path)

    en = client.get(f"/projects/{project.id}")
    assert en.status_code == 200
    assert CATALOG["en"]["proj.paste_url"] in en.text
    assert "mail:// / imap://" not in en.text
    assert 'name="url"' in en.text

    zh = client.get(f"/projects/{project.id}", headers={"Accept-Language": "zh-CN"})
    assert CATALOG["zh"]["proj.paste_url"] in zh.text

    settings_en = client.get("/settings")
    assert settings_en.status_code == 200
    assert "Notion" in settings_en.text
    assert "NOTION_TOKEN" in settings_en.text
    assert CATALOG["en"]["set.notion_token"] in settings_en.text
    notion_card = settings_en.text.split("Notion", 1)[1].split("</li>", 1)[0]
    assert "NOTION_TOKEN" in notion_card
    assert "Authorize" in notion_card or "授权" in notion_card
    assert CATALOG["en"]["set.unavailable"] not in notion_card

    settings_zh = client.get("/settings", headers={"Accept-Language": "zh-CN"})
    assert CATALOG["zh"]["set.notion_token"] in settings_zh.text


def test_reader_label_and_cached_preview_for_fixtured_notion(tmp_path):
    project, ds = _fixture_notion_ds(tmp_path, url=NOTION_APP)
    client = _client(tmp_path)
    page = client.get(f"/projects/{project.id}")
    assert page.status_code == 200
    assert "Notion" in page.text
    assert "材料清单" in page.text
    assert f"app.notion.com/p/{PAGE_HEX}" in page.text
    assert get_project(tmp_path, project.id).datasources[0].kind == KIND_PAGE

    preview = client.get(f"/projects/{project.id}/datasources/{ds.id}")
    assert preview.status_code == 200
    assert "材料清单" in preview.text
    assert "现场装机 80 MW" in preview.text
    assert "<table>" not in preview.text


def test_add_rejects_invalid_and_local_paths_without_fake_success(tmp_path):
    project = create_project(tmp_path, "能源项目")
    client = _client(tmp_path)
    before = len(get_project(tmp_path, project.id).datasources)

    cases = (
        "https://example.com/not-docs",
        "file:///tmp/notes.md",
        "/tmp/notes.md",
        "notes.md",
    )
    for url in cases:
        resp = client.post(
            f"/projects/{project.id}/datasources",
            data={"url": url, "name": "should-not-land"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert 'data-error-code="invalid_link"' in resp.text
        assert "invalid_link" in resp.text
        assert CATALOG["en"]["proj.err_invalid_link"] in resp.text
        assert "should-not-land" not in resp.text
        assert len(get_project(tmp_path, project.id).datasources) == before

    zh = client.post(
        f"/projects/{project.id}/datasources",
        data={"url": "https://example.com/still-bad"},
        headers={"Accept-Language": "zh-CN"},
        follow_redirects=True,
    )
    assert CATALOG["zh"]["proj.err_invalid_link"] in zh.text
    assert 'data-error-code="invalid_link"' in zh.text


def test_add_form_accepts_notion_page_and_rejects_legacy_platforms(tmp_path):
    project = create_project(tmp_path, "能源项目")
    client = _client(tmp_path)
    added = client.post(
        f"/projects/{project.id}/datasources",
        data={"url": NOTION_APP, "name": "材料页"},
        follow_redirects=True,
    )
    assert added.status_code == 200
    assert "data-error-code=" not in added.text
    assert "材料页" in added.text
    assert "Notion" in added.text
    saved = get_project(tmp_path, project.id)
    assert [ds.kind for ds in saved.datasources] == [KIND_PAGE]
    assert saved.datasources[0].reader == "notion"

    rejected = (
        "https://docs.qq.com/sheet/Denergy",
        "https://doc.weixin.qq.com/doc/e3doc",
        "mail://wecom/inbox?keywords=评审会",
        "imap://alice@imap.example.com/INBOX?keywords=x",
    )
    before = list(saved.datasources)
    for url in rejected:
        resp = client.post(
            f"/projects/{project.id}/datasources",
            data={"url": url, "name": "should-not-land"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert 'data-error-code="unsupported_reader"' in resp.text
        assert CATALOG["en"]["proj.err_unsupported_reader"] in resp.text
        assert "should-not-land" not in resp.text
        assert get_project(tmp_path, project.id).datasources == before


def test_project_error_text_maps_add_read_codes():
    en_req = SimpleNamespace(cookies={}, headers={"accept-language": "en"})
    zh_req = SimpleNamespace(cookies={"lang": "zh"}, headers={})
    cases = (
        ("unsupported_reader", "proj.err_unsupported_reader"),
        ("invalid_link", "proj.err_invalid_link"),
        ("permission", "proj.err_permission"),
        ("read_failed", "proj.err_read_failed"),
    )
    for code, key in cases:
        exc = ProjectError("engine-raw-should-not-leak", code=code)
        en = _project_error_text(en_req, exc)
        zh = _project_error_text(zh_req, exc)
        assert CATALOG["en"][key] in en and f"({code})" in en
        assert CATALOG["zh"][key] in zh and f"({code})" in zh
        assert "engine-raw-should-not-leak" not in en
        assert "engine-raw-should-not-leak" not in zh
    unknown = ProjectError("数据源不存在", code="not_found")
    assert _project_error_text(en_req, unknown) == "数据源不存在"


def test_read_surfaces_permission_without_opening_success(tmp_path):
    project, ds = _fixture_notion_ds(tmp_path)
    client = _client(tmp_path)
    failed = client.post(
        f"/projects/{project.id}/datasources/{ds.id}/read",
        follow_redirects=True,
    )
    assert failed.status_code == 200
    assert 'data-error-code="permission"' in failed.text
    assert CATALOG["en"]["proj.err_permission"] in failed.text
    assert "现场装机 80 MW" in failed.text
