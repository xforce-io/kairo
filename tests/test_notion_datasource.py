"""#388 Notion-only Project Data Source: infer, add gate, read, cache, Task provenance."""

from __future__ import annotations

import json
import os

from typer.testing import CliRunner

from kairo.cli import app
from kairo.project_materials import load_cache, read_cached_datasource, write_cache
from kairo.projects import (
    ProjectError,
    add_datasource,
    create_project,
    create_task,
    get_project,
    read_artifact,
    run_task,
)
from kairo.provider import AgentConfig, AgentResult
from kairo.readers import (
    INVALID_LINK,
    KIND_PAGE,
    PERMISSION,
    READ_FAILED,
    ReadError,
    infer_source,
    parse_notion_page_id,
    read_datasource,
    read_notion_page,
)
from kairo.settings import Connection, SettingsDoc, as_public_dict
from tests.conftest import seed_existing_datasource


runner = CliRunner()

PAGE_HEX = "a" * 32
PAGE_UUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
NOTION_SO = f"https://www.notion.so/Product-{PAGE_HEX}"
NOTION_APP = f"https://app.notion.com/p/{PAGE_HEX}"
NOTION_APP_SLUG = f"https://app.notion.com/p/Product-{PAGE_HEX}"
NOTION_SITE = f"https://team.notion.site/Product-{PAGE_HEX}"
NOTION_NAKED = f"https://notion.so/{PAGE_HEX}"
NOTION_QUERY = f"https://www.notion.so/workspace?p={PAGE_HEX}"


class NotionCliProvider:
    name = "notion-cli-test"
    model = "test"
    supports_read_dirs = True
    supports_project_cli = True

    def run(self, config: AgentConfig, signal=None) -> AgentResult:
        import re
        import subprocess
        import sys

        ctx = config.context
        serve = re.search(r"serve_root: (.+)", ctx).group(1).strip()
        pid = re.search(r"project_id: (.+)", ctx).group(1).strip()
        rid = re.search(r"run_id: (.+)", ctx).group(1).strip()
        env = os.environ.copy()
        env["KAIRO_SERVE_ROOT"] = serve

        def kairo(*args: str) -> dict:
            proc = subprocess.run(
                [sys.executable, "-m", "kairo", *args],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            if proc.returncode != 0:
                raise RuntimeError(proc.stdout + proc.stderr)
            return json.loads(proc.stdout)

        catalog = kairo("project", "context", pid, "--run", rid, "--root", serve)
        ds = next(item for item in catalog["items"] if item["type"] == "datasource")
        read = kairo("project", "read", pid, ds["source_id"], "--run", rid, "--root", serve)
        body = (
            f"# notion-run\n\n"
            f"[{ds['title']}](input:{read['input_id']})\n\n"
            f"{read['content']}\n"
        )
        dest = config.artifact_dir / "artifact.md"
        dest.write_text(body, encoding="utf-8")
        return AgentResult(artifacts=[dest], result_text=body)


def _fixture_transport(title: str = "能量清单", paragraph: str = "装机 80MW"):
    page = {
        "object": "page",
        "id": PAGE_UUID,
        "properties": {"title": {"type": "title", "title": [{"plain_text": title}]}},
    }
    children = {
        "object": "list",
        "results": [
            {
                "id": "blk-1",
                "type": "paragraph",
                "has_children": False,
                "paragraph": {"rich_text": [{"plain_text": paragraph}]},
            }
        ],
        "has_more": False,
    }

    def transport(method: str, path: str):
        assert method == "GET"
        if path.startswith(f"/v1/pages/{PAGE_UUID}"):
            return page
        if path.startswith(f"/v1/blocks/{PAGE_UUID}/children"):
            return children
        raise ReadError(READ_FAILED, f"unexpected Notion path:{path}")

    return transport


def test_infer_source_accepts_notion_hosts_and_app_p_path():
    for url in (NOTION_SO, NOTION_APP, NOTION_APP_SLUG, NOTION_SITE, NOTION_NAKED, NOTION_QUERY):
        inferred = infer_source(url)
        assert inferred.reader == "notion"
        assert inferred.connection_id == "notion"
        assert inferred.kind == KIND_PAGE
        assert inferred.live is True
        assert inferred.label == "Notion"
        assert parse_notion_page_id(url) == PAGE_UUID


def test_infer_source_rejects_unknown_notion_com_host():
    try:
        infer_source(f"https://www.notion.com/p/{PAGE_HEX}")
        raise AssertionError("expected invalid")
    except ReadError as exc:
        assert exc.code == INVALID_LINK


def test_infer_source_rejects_notion_host_without_page_id():
    try:
        infer_source("https://www.notion.so/page")
        raise AssertionError("expected invalid")
    except ReadError as exc:
        assert exc.code == INVALID_LINK
    try:
        infer_source("https://app.notion.com/p/not-an-id")
        raise AssertionError("expected invalid")
    except ReadError as exc:
        assert exc.code == INVALID_LINK


def test_infer_source_non_notion_platforms_are_not_live():
    sheet = infer_source("https://docs.qq.com/sheet/Denergy")
    assert sheet.reader == "tencent-docs" and sheet.live is False
    wecom = infer_source("https://doc.weixin.qq.com/doc/e3doc")
    assert wecom.reader == "wecom" and wecom.live is False
    mail = infer_source("mail://wecom/inbox?keywords=x")
    assert mail.reader == "wecom" and mail.live is False
    imap = infer_source("imap://alice@imap.example.com/INBOX?keywords=x")
    assert imap.reader == "imap" and imap.live is False


def test_add_datasource_accepts_notion_and_rejects_new_non_notion(tmp_path):
    project = create_project(tmp_path, "P")
    added = add_datasource(tmp_path, project.id, url=NOTION_APP, name="材料页")
    assert added.reader == "notion"
    assert added.kind == KIND_PAGE
    assert added.connection_id == "notion"
    saved = get_project(tmp_path, project.id)
    assert [ds.url for ds in saved.datasources] == [NOTION_APP]

    cases = (
        ("https://docs.qq.com/sheet/Denergy", "unsupported_reader"),
        ("https://doc.weixin.qq.com/doc/e3doc", "unsupported_reader"),
        ("mail://wecom/inbox?keywords=评审会", "unsupported_reader"),
        ("imap://alice@imap.example.com/INBOX?keywords=x", "unsupported_reader"),
        ("file:///tmp/notes.md", "invalid_link"),
        (str(tmp_path / "notes.md"), "invalid_link"),
        ("https://example.com/not-docs", "invalid_link"),
    )
    for url, code in cases:
        before = list(get_project(tmp_path, project.id).datasources)
        try:
            add_datasource(tmp_path, project.id, url=url)
            raise AssertionError(f"expected fail: {url}")
        except ProjectError as exc:
            assert exc.code == code
        after = get_project(tmp_path, project.id)
        assert after.datasources == before


def test_existing_non_notion_row_still_lists(tmp_path):
    project = create_project(tmp_path, "P")
    seeded = seed_existing_datasource(
        tmp_path,
        project.id,
        url="https://docs.qq.com/sheet/Denergy",
        reader="tencent-docs",
        kind="spreadsheet",
        purpose="存量",
    )
    listed = get_project(tmp_path, project.id)
    assert [ds.id for ds in listed.datasources] == [seeded.id]


def test_settings_catalog_notion_live_others_not():
    public = as_public_dict(SettingsDoc())
    assert public["connections"]["notion"]["live"] is True
    assert public["connections"]["notion"]["token_env"] == "NOTION_TOKEN"
    assert public["connections"]["tencent-docs"]["live"] is False
    assert public["connections"]["wecom"]["live"] is False
    assert public["connections"]["imap"]["live"] is False
    assert public["connections"]["notion"]["health"] == "unauthorized"
    assert public["connections"]["wecom"]["health"] == "unavailable"
    dumped = json.dumps(public)
    assert "secret" not in dumped.lower()
    assert "ntn_" not in dumped


def test_read_notion_page_uses_recorded_fixture():
    conn = Connection(authorized=True, token_env="NOTION_TOKEN")
    text = read_notion_page(
        NOTION_APP,
        conn,
        transport=_fixture_transport(),
    )
    assert "# 能量清单" in text
    assert "装机 80MW" in text


def test_read_datasource_permission_without_auth_or_token(monkeypatch):
    try:
        read_datasource(NOTION_APP, KIND_PAGE, "notion", Connection(authorized=False, token_env="NOTION_TOKEN"))
        raise AssertionError("expected permission")
    except ReadError as exc:
        assert exc.code == PERMISSION

    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    try:
        read_datasource(
            NOTION_APP,
            KIND_PAGE,
            "notion",
            Connection(authorized=True, token_env="NOTION_TOKEN"),
            transport=_fixture_transport(),
        )
        raise AssertionError("expected permission")
    except ReadError as exc:
        assert exc.code == PERMISSION


def test_permission_does_not_overwrite_successful_cache(tmp_path, monkeypatch):
    from kairo.settings import set_dotted

    project = create_project(tmp_path, "P")
    ds = add_datasource(tmp_path, project.id, url=NOTION_APP, name="材料页")
    monkeypatch.setenv("NOTION_TOKEN", "ntn_test_not_a_real_token")
    set_dotted("connections.notion.authorized", True)
    write_cache(tmp_path, project.id, ds, "# cached-success\n\nkeep-me")
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    try:
        read_cached_datasource(tmp_path, project.id, ds.id, refresh=True)
        raise AssertionError("expected permission")
    except ReadError as exc:
        assert exc.code == PERMISSION
    cached = load_cache(tmp_path, project.id, ds.id)
    assert cached is not None
    assert "keep-me" in cached.content


def test_cli_datasource_add_read_and_settings(tmp_path, monkeypatch):
    from kairo.project_materials import read_datasource as materials_read

    serve = tmp_path / "root"
    serve.mkdir()
    monkeypatch.chdir(serve)
    monkeypatch.setenv("NOTION_TOKEN", "ntn_test_not_a_real_token")

    def fake_read(url, kind, reader, connection, **kwargs):
        if reader == "notion":
            return read_notion_page(url, connection, transport=_fixture_transport())
        return materials_read(url, kind, reader, connection, **kwargs)

    monkeypatch.setattr("kairo.project_materials.read_datasource", fake_read)

    shown = json.loads(runner.invoke(app, ["settings", "show"]).output)
    assert shown["connections"]["notion"]["live"] is True
    assert shown["connections"]["tencent-docs"]["live"] is False
    auth = json.loads(
        runner.invoke(app, ["settings", "set", "connections.notion.authorized", "true"]).output
    )
    assert auth["connections"]["notion"]["authorized"] is True
    assert auth["connections"]["notion"]["health"] == "authorized"
    created = json.loads(runner.invoke(app, ["project", "create", "Notion项目"]).output)
    pid = created["id"]
    added = json.loads(
        runner.invoke(app, ["datasource", "add", pid, "--url", NOTION_APP, "--name", "材料页"]).output
    )
    assert added["reader"] == "notion"
    read = json.loads(runner.invoke(app, ["datasource", "read", pid, added["id"]]).output)
    assert read["ok"] is True
    assert "装机 80MW" in read["content"]

    rejected = runner.invoke(app, ["datasource", "add", pid, "--url", "https://docs.qq.com/sheet/Dx"])
    assert rejected.exit_code != 0
    assert "尚未接入" in rejected.output or "unsupported" in rejected.output
    assert len(get_project(serve, pid).datasources) == 1


def test_agent_task_provenance_includes_notion_datasource(tmp_path, monkeypatch):
    from kairo.project_materials import load_run_inputs
    from kairo.settings import set_dotted

    monkeypatch.setenv("NOTION_TOKEN", "ntn_test_not_a_real_token")
    set_dotted("connections.notion.authorized", True)

    def fake_read(url, kind, reader, connection, **kwargs):
        return read_notion_page(url, connection, transport=_fixture_transport())

    monkeypatch.setattr("kairo.project_materials.read_datasource", fake_read)
    project = create_project(tmp_path, "仅Notion")
    ds = add_datasource(tmp_path, project.id, url=NOTION_APP, name="材料页")
    assert get_project(tmp_path, project.id).datasources[0].reader == "notion"
    warmed = read_cached_datasource(tmp_path, project.id, ds.id)
    assert "装机 80MW" in warmed.content
    task = create_task(tmp_path, project.id, name="整理", prompt="总结材料")
    record = run_task(tmp_path, project.id, task.id, provider=NotionCliProvider())
    assert record.status == "succeeded", record.reason
    body = read_artifact(tmp_path, project.id, record.id)
    assert "装机 80MW" in body
    inputs = load_run_inputs(tmp_path, project.id, record.id)
    assert any(
        str(item.get("type") or "") == "datasource"
        or str(item.get("source_id") or "").startswith("datasource:")
        for item in inputs
    )
    assert any(ds.id in str(item.get("source_id") or "") for item in inputs)


def test_live_http_add_read_when_token_present():
    import pytest

    token = os.environ.get("NOTION_TOKEN", "").strip()
    live_url = os.environ.get("KAIRO_NOTION_PAGE_URL", "").strip()
    if not token or not live_url:
        pytest.skip(
            "CI has no NOTION_TOKEN / KAIRO_NOTION_PAGE_URL; recorded fixtures cover add+read+permission"
        )
    inferred = infer_source(live_url)
    assert inferred.reader == "notion" and inferred.live
    text = read_notion_page(live_url, Connection(authorized=True, token_env="NOTION_TOKEN"))
    assert text.strip()
