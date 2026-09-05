"""#232 S1 本阶段闭环：CLI + API + Console 走 shipped 入口。"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from kairo.cli import app
from kairo.web.server import create_app
from kairo.workspace import Workspace

runner = CliRunner()


def _stub_cmd(path: Path, source: str) -> str:
    path.write_text(source, encoding="utf-8")
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(path))} {{url}}"


def _cli(args, cwd: Path, monkeypatch):
    monkeypatch.chdir(cwd)
    return runner.invoke(app, args)


def _load(result):
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_s1_cli_api_console_loop(tmp_path, monkeypatch):
    serve = tmp_path / "root"
    serve.mkdir()
    ws_a = Workspace.init(serve / "alpha-ws", topic="阿尔法")
    ws_b = Workspace.init(serve / "beta-ws", topic="贝塔")
    (ws_a.root / "kept.txt").write_text("workspace-content")
    monkeypatch.chdir(serve)
    monkeypatch.setenv("TENCENT_DOCS_TOKEN", "test-token-not-for-project")

    ok_cmd = _stub_cmd(
        tmp_path / "ok.py",
        "import sys\nprint('plant,mw')\nprint('solar,80')\n",
    )
    deny_cmd = _stub_cmd(
        tmp_path / "deny.py",
        "import sys\nsys.stderr.write('403 forbidden')\nsys.exit(1)\n",
    )
    miss_cmd = _stub_cmd(
        tmp_path / "miss.py",
        "import sys\nsys.stderr.write('404 not found')\nsys.exit(1)\n",
    )
    boom_cmd = _stub_cmd(
        tmp_path / "boom.py",
        "import sys\nsys.stderr.write('boom')\nsys.exit(2)\n",
    )

    shown = _load(_cli(["settings", "show"], serve, monkeypatch))
    assert "general" in shown and "projects" in shown
    assert "workspaces" in shown and "timeline" in shown
    assert shown["connections"]["tencent-docs"]["authorized"] is False
    assert "token" not in json.dumps(shown).lower() or shown["connections"]["tencent-docs"]["token_env"]

    auth = _load(
        _cli(
            ["settings", "set", "connections.tencent-docs.authorized", "true"],
            serve,
            monkeypatch,
        )
    )
    assert auth["connections"]["tencent-docs"]["authorized"] is True
    assert auth["connections"]["tencent-docs"]["health"] == "authorized"
    _load(_cli(["settings", "set", "connections.tencent-docs.cmd", ok_cmd], serve, monkeypatch))
    _load(_cli(["settings", "set", "general.locale", "zh"], serve, monkeypatch))

    created = _load(_cli(["project", "create", "综合能源"], serve, monkeypatch))
    pid = created["id"]
    assert created["name"] == "综合能源"
    assert "token" not in json.dumps(created).lower()
    disk = json.loads((serve / ".kairo" / "projects" / pid / "project.json").read_text())
    assert "token" not in json.dumps(disk).lower()
    assert "api_key" not in json.dumps(disk).lower()

    linked = _load(_cli(["project", "link", pid, "alpha-ws", "beta-ws"], serve, monkeypatch))
    assert set(linked["workspace_slugs"]) == {"alpha-ws", "beta-ws"}
    unlinked = _load(_cli(["project", "unlink", pid, "beta-ws"], serve, monkeypatch))
    assert unlinked["workspace_slugs"] == ["alpha-ws"]
    assert (serve / "beta-ws" / "constitution.yaml").is_file()
    assert (serve / "alpha-ws" / "kept.txt").read_text() == "workspace-content"

    other = _load(_cli(["project", "create", "其它项目"], serve, monkeypatch))
    _load(_cli(["project", "link", other["id"], "alpha-ws"], serve, monkeypatch))

    ds = _load(
        _cli(
            [
                "datasource",
                "add",
                pid,
                "--url",
                "https://docs.qq.com/sheet/Denergy",
                "--purpose",
                "装机",
            ],
            serve,
            monkeypatch,
        )
    )
    ds_id = ds["id"]
    assert ds["connection_id"] == "tencent-docs"
    assert ds["reader"] == "tencent-docs"
    read_ok = _load(_cli(["datasource", "read", pid, ds_id], serve, monkeypatch))
    assert read_ok["ok"] is True
    assert "solar,80" in read_ok["content"]

    _load(_cli(["settings", "set", "connections.tencent-docs.authorized", "false"], serve, monkeypatch))
    denied = _cli(["datasource", "read", pid, ds_id], serve, monkeypatch)
    assert denied.exit_code != 0
    denied_payload = json.loads(denied.output)
    assert denied_payload["code"] == "permission"

    _load(_cli(["settings", "set", "connections.tencent-docs.authorized", "true"], serve, monkeypatch))
    _load(_cli(["settings", "set", "connections.tencent-docs.cmd", ok_cmd], serve, monkeypatch))
    bad_add = _cli(
        ["datasource", "add", pid, "--url", "https://example.com/not-docs"],
        serve,
        monkeypatch,
    )
    assert bad_add.exit_code != 0
    assert "invalid_link" in bad_add.output or "无法识别" in bad_add.output
    notion_add = _cli(
        ["datasource", "add", pid, "--url", "https://www.notion.so/page"],
        serve,
        monkeypatch,
    )
    assert notion_add.exit_code != 0
    assert "unsupported" in notion_add.output or "尚未接入" in notion_add.output

    _load(_cli(["settings", "set", "connections.tencent-docs.cmd", deny_cmd], serve, monkeypatch))
    perm2 = json.loads(_cli(["datasource", "read", pid, ds_id, "--refresh"], serve, monkeypatch).output)
    assert perm2["code"] == "permission"

    _load(_cli(["settings", "set", "connections.tencent-docs.cmd", miss_cmd], serve, monkeypatch))
    miss = json.loads(_cli(["datasource", "read", pid, ds_id, "--refresh"], serve, monkeypatch).output)
    assert miss["code"] == "invalid_link"

    _load(_cli(["settings", "set", "connections.tencent-docs.cmd", boom_cmd], serve, monkeypatch))
    boom = json.loads(_cli(["datasource", "read", pid, ds_id, "--refresh"], serve, monkeypatch).output)
    assert boom["code"] == "read_failed"

    _load(_cli(["settings", "set", "connections.tencent-docs.cmd", ok_cmd], serve, monkeypatch))
    task = _load(
        _cli(
            ["task", "create", pid, "--name", "周报", "--datasource", ds_id, "--schedule", "once"],
            serve,
            monkeypatch,
        )
    )
    tid = task["id"]
    assert task["version"] == 1
    run1 = _load(_cli(["task", "run", pid, tid], serve, monkeypatch))
    assert run1["status"] == "succeeded"
    assert run1["task_version"] == 1
    art = _load(_cli(["artifact", "show", pid, run1["id"]], serve, monkeypatch))
    body = art["artifact"]
    assert run1["id"] in body
    assert "Task version: 1" in body
    assert "https://docs.qq.com/sheet/Denergy" in body
    assert "solar,80" in body

    edited = _load(_cli(["task", "edit", pid, tid, "--name", "周报修订"], serve, monkeypatch))
    assert edited["version"] == 2
    run2 = _load(_cli(["task", "run", pid, tid], serve, monkeypatch))
    assert run2["task_version"] == 2
    old = _load(_cli(["artifact", "show", pid, run1["id"]], serve, monkeypatch))
    assert old["run"]["task_version"] == 1
    assert old["run"]["task_name"] == "周报"

    _load(_cli(["settings", "set", "connections.tencent-docs.cmd", boom_cmd], serve, monkeypatch))
    from datetime import UTC, datetime, timedelta

    from kairo.project_materials import set_clock

    set_clock(lambda: datetime.now(UTC) + timedelta(seconds=3601))
    try:
        failed = _cli(["task", "run", pid, tid], serve, monkeypatch)
    finally:
        set_clock(None)
    assert failed.exit_code != 0
    fail_run = json.loads(failed.output)
    assert fail_run["status"] == "failed"
    assert fail_run["reason"] == "read_failed"
    assert fail_run["artifact_path"] is None
    art_dir = serve / ".kairo" / "projects" / pid / "artifacts"
    assert not (art_dir / f"{fail_run['id']}.md").exists()

    _load(_cli(["datasource", "rm", pid, ds_id], serve, monkeypatch))
    after_rm = _load(_cli(["settings", "show"], serve, monkeypatch))
    assert after_rm["connections"]["tencent-docs"]["authorized"] is True

    client = TestClient(create_app(serve))
    listed = client.get("/api/projects").json()
    assert listed["ok"] is True
    names = {p["name"] for p in listed["projects"]}
    assert "综合能源" in names
    api_proj = client.get(f"/api/projects/{pid}").json()
    assert "alpha-ws" in api_proj["project"]["workspace_slugs"]
    settings_api = client.get("/api/settings").json()
    assert settings_api["settings"]["general"]["locale"] == "zh"
    assert settings_api["settings"]["connections"]["tencent-docs"]["authorized"] is True
    assert settings_api["settings"]["connections"]["tencent-docs"]["label"] == "腾讯文档"
    assert settings_api["settings"]["connections"]["wecom"]["live"] is True
    assert settings_api["settings"]["connections"]["notion"]["live"] is False
    assert "test-token-not-for-project" not in json.dumps(settings_api)

    patched = client.patch("/api/settings", json={"path": "general.locale", "value": "en"}).json()
    assert patched["settings"]["general"]["locale"] == "en"
    cli_locale = _load(_cli(["settings", "show"], serve, monkeypatch))
    assert cli_locale["general"]["locale"] == "en"

    html_projects = client.get("/projects")
    assert html_projects.status_code == 200
    assert "综合能源" in html_projects.text
    assert "Projects" in html_projects.text or "项目" in html_projects.text
    assert "Ref" in html_projects.text
    assert "Last run:" in html_projects.text
    assert "Succeeded" in html_projects.text or "Failed" in html_projects.text
    html_proj = client.get(f"/projects/{pid}")
    assert html_proj.status_code == 200
    assert "阿尔法" in html_proj.text
    assert 'name="workspaces"' in html_proj.text
    assert '<details class="topic-picker">' in html_proj.text
    assert '<details class="topic-picker" open>' not in html_proj.text
    assert 'placeholder="slug"' not in html_proj.text
    assert 'name="slug"' not in html_proj.text
    assert 'name="kind"' not in html_proj.text
    assert "<option value=\"spreadsheet\">" not in html_proj.text
    assert "Create project" not in html_proj.text
    both = client.post(
        f"/projects/{pid}/workspaces",
        data={"workspaces": ["alpha-ws", "beta-ws"]},
        follow_redirects=True,
    )
    assert both.status_code == 200
    assert "阿尔法" in both.text and "贝塔" in both.text
    sheet = client.post(
        f"/projects/{pid}/datasources",
        data={"url": "https://docs.qq.com/sheet/Denergy2", "purpose": "装机"},
        follow_redirects=True,
    )
    assert sheet.status_code == 200
    assert "Tencent Docs" in sheet.text or "腾讯文档" in sheet.text
    assert "Create task" in sheet.text
    assert 'class="task-create"' in sheet.text
    assert 'name="kind"' not in sheet.text
    smart = client.post(
        f"/projects/{pid}/datasources",
        data={"url": "https://docs.qq.com/smartsheet/Senergy"},
        follow_redirects=True,
    )
    assert smart.status_code == 200
    silent = client.post(
        f"/projects/{pid}/datasources",
        data={"url": "https://example.com/not-docs"},
        follow_redirects=True,
    )
    assert silent.status_code == 200
    assert "无法识别" in silent.text or "invalid" in silent.text.lower()
    urls = " ".join(
        d["url"] for d in client.get(f"/api/projects/{pid}").json()["project"]["datasources"]
    )
    assert "example.com" not in urls
    assert 'name="kind"' not in silent.text
    html_art = client.get(f"/projects/{pid}/runs/{run1['id']}")
    assert html_art.status_code == 200
    assert 'class="doc"' in html_art.text
    assert 'class="card"' not in html_art.text
    assert "<h1>" in html_art.text
    assert run1["id"] in html_art.text
    assert "Task version: 1" in html_art.text or "v1" in html_art.text

    settings_html = client.get("/settings")
    assert settings_html.status_code == 200
    assert "General" in settings_html.text
    assert "Projects" in settings_html.text
    assert "Topics" in settings_html.text
    assert "Timeline" in settings_html.text
    assert "腾讯文档" in settings_html.text
    assert "企微文档" in settings_html.text
    assert "Notion" in settings_html.text
    assert "周报修订" not in settings_html.text
    assert run1["id"] not in settings_html.text

    dash = client.get("/")
    assert dash.status_code == 200
    assert "alpha-ws" in dash.text and "beta-ws" in dash.text
    tl = client.get("/timeline")
    assert tl.status_code == 200
    assert "Timeline" in tl.text or "时间轴" in tl.text

    public = TestClient(create_app(serve, mode="public-read"))
    assert public.get("/projects").status_code == 404
    assert public.get("/settings").status_code == 404
    assert public.get("/api/projects").status_code == 404
    pub_home = public.get("/")
    assert pub_home.status_code == 200
    assert 'href="/projects"' not in pub_home.text
    assert 'href="/settings"' not in pub_home.text
    assert public.get("/timeline").status_code == 200

    _load(_cli(["settings", "set", "connections.tencent-docs.cmd", ok_cmd], serve, monkeypatch))
    ds2 = client.post(
        f"/api/projects/{pid}/datasources",
        json={
            "url": "https://docs.qq.com/smartsheet/Senergy-api",
            "purpose": "风险",
        },
    ).json()
    assert ds2["ok"] is True
    read_api = client.post(f"/api/projects/{pid}/datasources/{ds2['datasource']['id']}/read").json()
    assert read_api["ok"] is True
    task_api = client.post(
        f"/api/projects/{pid}/tasks",
        json={"name": "风险清单", "datasource_id": ds2["datasource"]["id"], "schedule": "once"},
    ).json()
    run_api = client.post(
        f"/api/projects/{pid}/tasks/{task_api['task']['id']}/run"
    ).json()
    assert run_api["run"]["status"] == "succeeded"
    got = client.get(f"/api/projects/{pid}/runs/{run_api['run']['id']}").json()
    assert "Task version:" in got["artifact"]
    assert "https://docs.qq.com/smartsheet/Senergy" in got["artifact"]
    assert run_api["run"]["id"] in got["artifact"]

    enabled = _load(_cli(["task", "disable", pid, task_api["task"]["id"]], serve, monkeypatch))
    assert enabled["enabled"] is False
    enabled = _load(_cli(["task", "enable", pid, task_api["task"]["id"]], serve, monkeypatch))
    assert enabled["enabled"] is True

    noisy = TestClient(create_app(serve), raise_server_exceptions=False)
    assert noisy.post(f"/projects/{pid}/datasources/ds-missing/delete").status_code == 200
    assert "数据源不存在" in noisy.post(f"/projects/{pid}/datasources/ds-missing/delete").text
    assert noisy.post(f"/projects/{pid}/tasks/tsk-missing/run").status_code == 200
    assert noisy.post("/projects/prj-missing/workspaces/alpha-ws/unlink").status_code == 404
    assert noisy.get("/api/settings").status_code == 200
    pub = TestClient(create_app(serve, mode="public-read"), raise_server_exceptions=False)
    assert pub.get("/api/settings").status_code == 404
    assert pub.get(f"/projects/{pid}/runs/{run1['id']}").status_code == 404


def test_legacy_schedule_metadata_remains_readable_and_preserved(tmp_path):
    from kairo.projects import edit_project, get_project

    project_id = "prj-legacy"
    project_dir = tmp_path / ".kairo" / "projects" / project_id
    project_dir.mkdir(parents=True)
    state = {"tsk-legacy": {"mode": "armed", "next_due_at": "2026-09-03T08:00:00+00:00"}}
    (project_dir / "project.json").write_text(
        json.dumps(
            {
                "id": project_id,
                "name": "旧项目",
                "workspace_slugs": [],
                "datasources": [],
                "tasks": [],
                "schedule_states": state,
                "created_at": "2026-09-03T00:00:00+00:00",
                "updated_at": "2026-09-03T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    assert get_project(tmp_path, project_id).schedule_states == state
    edit_project(tmp_path, project_id, name="已迁移项目")
    saved = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    assert saved["schedule_states"] == state


def test_reader_classifies_generic_cmd_errors_as_read_failed(tmp_path, monkeypatch):
    from kairo.readers import READ_FAILED, ReadError, read_tencent_docs
    from kairo.settings import Connection

    monkeypatch.chdir(tmp_path)
    url = "https://docs.qq.com/sheet/Denergy"
    cases = (
        "import sys\nsys.stderr.write('Error: invalid response from upstream')\nsys.exit(1)\n",
        "import sys\nsys.stderr.write('config file not found')\nsys.exit(1)\n",
    )
    for i, src in enumerate(cases):
        cmd = _stub_cmd(tmp_path / f"generic{i}.py", src)
        conn = Connection(authorized=True, cmd=cmd)
        try:
            read_tencent_docs(url, "spreadsheet", conn)
            raise AssertionError("expected ReadError")
        except ReadError as exc:
            assert exc.code == READ_FAILED


def test_cli_multi_link_is_atomic(tmp_path, monkeypatch):
    from kairo.workspace import Workspace

    serve = tmp_path / "root"
    serve.mkdir()
    Workspace.init(serve / "alpha-ws", topic="阿尔法")
    monkeypatch.chdir(serve)
    created = _load(_cli(["project", "create", "P"], serve, monkeypatch))
    failed = _cli(["project", "link", created["id"], "alpha-ws", "missing-ws"], serve, monkeypatch)
    assert failed.exit_code != 0
    shown = _load(_cli(["project", "show", created["id"]], serve, monkeypatch))
    assert shown["workspace_slugs"] == []


def test_infer_source_classifies_platforms():
    from kairo.readers import INVALID_LINK, UNSUPPORTED, ReadError, infer_source

    sheet = infer_source("https://docs.qq.com/sheet/Denergy")
    assert sheet.reader == "tencent-docs" and sheet.kind == "spreadsheet" and sheet.live
    smart = infer_source("https://docs.qq.com/smartsheet/Senergy")
    assert smart.reader == "tencent-docs" and smart.kind == "smartsheet"
    try:
        infer_source("https://example.com/not-docs")
        raise AssertionError("expected invalid")
    except ReadError as exc:
        assert exc.code == INVALID_LINK
    try:
        infer_source("https://www.notion.so/abc")
        raise AssertionError("expected unsupported")
    except ReadError as exc:
        assert exc.code == UNSUPPORTED
    wecom_doc = infer_source("https://doc.weixin.qq.com/doc/e3doc")
    assert wecom_doc.reader == "wecom" and wecom_doc.kind == "document" and wecom_doc.live
    wecom_sheet = infer_source("https://doc.weixin.qq.com/sheet/e3sheet")
    assert wecom_sheet.reader == "wecom" and wecom_sheet.kind == "spreadsheet" and wecom_sheet.live
    wecom_smart = infer_source("https://doc.weixin.qq.com/smartsheet/s3sheet")
    assert wecom_smart.reader == "wecom" and wecom_smart.kind == "smartsheet"
    wecom_page = infer_source("https://doc.weixin.qq.com/smartpage/a1page")
    assert wecom_page.reader == "wecom" and wecom_page.kind == "smartpage"
    published = infer_source("https://page.weixin.qq.com/smartpage/p/b1page")
    assert published.reader == "wecom" and published.kind == "smartpage" and published.live
    work_host = infer_source("https://work.weixin.qq.com/sheet/e3work")
    assert work_host.reader == "wecom" and work_host.kind == "spreadsheet"
    try:
        infer_source("https://doc.weixin.qq.com/unknown/x")
        raise AssertionError("expected invalid wecom path")
    except ReadError as exc:
        assert exc.code == INVALID_LINK
    for invalid in (
        "https://untrusted.docs.qq.com/sheet/Denergy",
        "https://docs.qq.com/sheetish/Denergy",
        "https://docs.qq.com/smartsheetish/Senergy",
    ):
        try:
            infer_source(invalid)
            raise AssertionError(f"expected invalid: {invalid}")
        except ReadError as exc:
            assert exc.code == INVALID_LINK


def test_datasource_kind_and_reader_follow_url_inference(tmp_path):
    from kairo.projects import ProjectError, add_datasource, create_project, get_project

    project = create_project(tmp_path, "P")
    inferred = add_datasource(
        tmp_path,
        project.id,
        url="https://docs.qq.com/smartsheet/Senergy",
        kind="smartsheet",
        connection_id="tencent-docs",
        reader="tencent-docs",
    )
    assert inferred.kind == "smartsheet"
    assert inferred.reader == "tencent-docs"
    for kwargs in (
        {"kind": "spreadsheet"},
        {"connection_id": "wecom"},
        {"reader": "wecom"},
    ):
        try:
            add_datasource(
                tmp_path,
                project.id,
                url="https://docs.qq.com/smartsheet/Senergy",
                **kwargs,
            )
            raise AssertionError(f"expected mismatch: {kwargs}")
        except ProjectError as exc:
            assert exc.code == "invalid_link"
    saved = get_project(tmp_path, project.id)
    assert all(ds.kind == "smartsheet" for ds in saved.datasources)
    assert all(ds.reader == "tencent-docs" for ds in saved.datasources)
    assert all(ds.connection_id == "tencent-docs" for ds in saved.datasources)


def test_datasource_rejects_url_userinfo_and_does_not_store_secret(tmp_path):
    from kairo.projects import ProjectError, add_datasource, create_project, get_project

    project = create_project(tmp_path, "P")
    secret = "leaked-pass-232"
    try:
        add_datasource(
            tmp_path,
            project.id,
            url=f"https://user:{secret}@docs.qq.com/sheet/Denergy",
        )
        raise AssertionError("expected invalid_link")
    except ProjectError as exc:
        assert exc.code == "invalid_link"
    disk = (tmp_path / ".kairo" / "projects" / project.id / "project.json").read_text(
        encoding="utf-8"
    )
    assert secret not in disk
    assert get_project(tmp_path, project.id).datasources == []
    ok = add_datasource(tmp_path, project.id, url="https://docs.qq.com/sheet/Denergy")
    assert ok.url == "https://docs.qq.com/sheet/Denergy"
    smart = add_datasource(
        tmp_path, project.id, url="https://docs.qq.com/smartsheet/Senergy"
    )
    assert smart.url == "https://docs.qq.com/smartsheet/Senergy"


def test_list_projects_skips_corrupt_record_and_keeps_valid(tmp_path):
    from kairo.projects import create_project, list_projects

    good = create_project(tmp_path, "合法项目")
    broken = tmp_path / ".kairo" / "projects" / "prj-broken"
    broken.mkdir(parents=True)
    (broken / "project.json").write_text("{not-json", encoding="utf-8")
    extra = tmp_path / ".kairo" / "projects" / "prj-extra"
    extra.mkdir(parents=True)
    (extra / "project.json").write_text(
        json.dumps(
            {
                "id": "prj-extra",
                "name": "坏字段项目",
                "workspace_slugs": [],
                "datasources": [],
                "tasks": [],
                "not_a_field": True,
            }
        ),
        encoding="utf-8",
    )
    listed = list_projects(tmp_path)
    assert [p.id for p in listed] == [good.id]
    client = TestClient(create_app(tmp_path), raise_server_exceptions=False)
    api = client.get("/api/projects")
    assert api.status_code == 200
    names = {p["name"] for p in api.json()["projects"]}
    assert names == {"合法项目"}
    html = client.get("/projects")
    assert html.status_code == 200
    assert "合法项目" in html.text
    assert "坏字段项目" not in html.text


def test_reader_timeout_is_read_failed(tmp_path):
    from kairo.readers import READ_FAILED, ReadError, read_tencent_docs
    from kairo.settings import Connection

    hang = tmp_path / "hang.py"
    hang.write_text("import time\ntime.sleep(30)\nprint('late')\n", encoding="utf-8")
    conn = Connection(
        authorized=True,
        cmd=f"{shlex.quote(sys.executable)} {shlex.quote(str(hang))} {{url}}",
    )
    try:
        read_tencent_docs(
            "https://docs.qq.com/sheet/Denergy",
            "spreadsheet",
            conn,
            timeout=0.2,
        )
        raise AssertionError("expected timeout")
    except ReadError as exc:
        assert exc.code == READ_FAILED


def test_reader_rejects_lookalike_hosts_and_bad_cmd_placeholders(tmp_path):
    from kairo.readers import INVALID_LINK, READ_FAILED, ReadError, read_tencent_docs
    from kairo.settings import Connection

    conn = Connection(authorized=True, cmd="true {url}")
    try:
        read_tencent_docs("https://notdocs.qq.com/sheet/Denergy", "spreadsheet", conn)
        raise AssertionError("expected invalid_link")
    except ReadError as exc:
        assert exc.code == INVALID_LINK
    try:
        read_tencent_docs(
            "https://docs.qq.com/sheet/Denergy",
            "spreadsheet",
            Connection(authorized=True, cmd="echo {missing}"),
        )
        raise AssertionError("expected read_failed")
    except ReadError as exc:
        assert exc.code == READ_FAILED


def test_obj_page_actions_are_inline_not_full_width(tmp_path, monkeypatch):
    """Project/Settings 操作按钮不得被 .btn { width:100% } 或 column flex stretch 拉满。"""
    serve = tmp_path / "root"
    serve.mkdir()
    Workspace.init(serve / "alpha-ws", topic="阿尔法")
    created = _load(_cli(["project", "create", "能源团队管理"], serve, monkeypatch))
    client = TestClient(create_app(serve))

    page = client.get("/settings")
    assert page.status_code == 200
    assert 'href="/static/app.css?v=' in page.text
    assert 'class="conn-grid"' in page.text
    assert 'class="conn-card"' in page.text

    css = client.get("/static/app.css")
    assert css.status_code == 200
    text = css.text
    assert ".btn {" in text and "width: 100%" in text
    assert ".obj-page .btn" in text
    brace = text.index("{", text.index(".obj-page .btn"))
    block = text[brace : text.index("}", brace) + 1]
    assert "width: auto" in block
    assert "align-self: flex-start" in block or "align-self: start" in block
    assert "flex: none" in block or "flex:none" in block

    proj = client.get(f"/projects/{created['id']}")
    assert proj.status_code == 200
    assert 'class="chooser"' in proj.text
    assert 'class="btn btn-step btn-inline"' in proj.text
    assert 'class="btn btn-ghost btn-inline"' in proj.text
    settings_btns = page.text
    assert 'class="btn btn-step btn-inline"' in settings_btns
    assert 'class="btn btn-ghost btn-inline"' in settings_btns


_WECOM_URLS = (
    ("https://doc.weixin.qq.com/doc/e3_DocExample", "document"),
    ("https://doc.weixin.qq.com/sheet/e3_SheetExample", "spreadsheet"),
    ("https://doc.weixin.qq.com/smartsheet/s3_SmartExample", "smartsheet"),
    ("https://doc.weixin.qq.com/smartpage/a1_PageExample", "smartpage"),
    ("https://page.weixin.qq.com/smartpage/p/b1_Published", "smartpage"),
)


def test_wecom_datasource_add_read_task_and_settings(tmp_path, monkeypatch):
    serve = tmp_path / "root"
    serve.mkdir()
    Workspace.init(serve / "alpha-ws", topic="阿尔法")
    monkeypatch.chdir(serve)
    ok_cmd = _stub_cmd(
        tmp_path / "wecom-ok.py",
        "import sys\nprint('wecom-body-ok')\n",
    )
    deny_cmd = _stub_cmd(
        tmp_path / "wecom-deny.py",
        "import sys\nsys.stderr.write('403 forbidden')\nsys.exit(1)\n",
    )
    miss_cmd = _stub_cmd(
        tmp_path / "wecom-miss.py",
        "import sys\nsys.stderr.write('404 not found')\nsys.exit(1)\n",
    )
    boom_cmd = _stub_cmd(
        tmp_path / "wecom-boom.py",
        "import sys\nsys.stderr.write('boom')\nsys.exit(2)\n",
    )

    shown = _load(_cli(["settings", "show"], serve, monkeypatch))
    assert shown["connections"]["wecom"]["live"] is True
    assert shown["connections"]["notion"]["live"] is False
    assert shown["connections"]["wecom"]["health"] == "unauthorized"

    auth = _load(
        _cli(["settings", "set", "connections.wecom.authorized", "true"], serve, monkeypatch)
    )
    assert auth["connections"]["wecom"]["authorized"] is True
    assert auth["connections"]["wecom"]["health"] == "authorized"
    _load(_cli(["settings", "set", "connections.wecom.cmd", ok_cmd], serve, monkeypatch))

    created = _load(_cli(["project", "create", "企微资料"], serve, monkeypatch))
    pid = created["id"]
    assert "token" not in json.dumps(created).lower()

    added = []
    for url, kind in _WECOM_URLS:
        ds = _load(_cli(["datasource", "add", pid, "--url", url], serve, monkeypatch))
        assert ds["reader"] == "wecom"
        assert ds["connection_id"] == "wecom"
        assert ds["kind"] == kind
        added.append(ds)
        read_ok = _load(_cli(["datasource", "read", pid, ds["id"]], serve, monkeypatch))
        assert read_ok["ok"] is True
        assert "wecom-body-ok" in read_ok["content"]

    notion_add = _cli(
        ["datasource", "add", pid, "--url", "https://www.notion.so/page"],
        serve,
        monkeypatch,
    )
    assert notion_add.exit_code != 0
    assert "unsupported" in notion_add.output or "尚未接入" in notion_add.output

    ds_id = added[1]["id"]
    _load(_cli(["settings", "set", "connections.wecom.authorized", "false"], serve, monkeypatch))
    denied = json.loads(_cli(["datasource", "read", pid, ds_id], serve, monkeypatch).output)
    assert denied["code"] == "permission"

    _load(_cli(["settings", "set", "connections.wecom.authorized", "true"], serve, monkeypatch))
    _load(_cli(["settings", "set", "connections.wecom.cmd", deny_cmd], serve, monkeypatch))
    perm2 = json.loads(_cli(["datasource", "read", pid, ds_id, "--refresh"], serve, monkeypatch).output)
    assert perm2["code"] == "permission"

    _load(_cli(["settings", "set", "connections.wecom.cmd", miss_cmd], serve, monkeypatch))
    miss = json.loads(_cli(["datasource", "read", pid, ds_id, "--refresh"], serve, monkeypatch).output)
    assert miss["code"] == "invalid_link"

    _load(_cli(["settings", "set", "connections.wecom.cmd", boom_cmd], serve, monkeypatch))
    boom = json.loads(_cli(["datasource", "read", pid, ds_id, "--refresh"], serve, monkeypatch).output)
    assert boom["code"] == "read_failed"

    _load(_cli(["settings", "set", "connections.wecom.cmd", ok_cmd], serve, monkeypatch))
    task = _load(
        _cli(
            ["task", "create", pid, "--name", "企微周报", "--datasource", ds_id, "--schedule", "once"],
            serve,
            monkeypatch,
        )
    )
    run1 = _load(_cli(["task", "run", pid, task["id"]], serve, monkeypatch))
    assert run1["status"] == "succeeded"
    art = _load(_cli(["artifact", "show", pid, run1["id"]], serve, monkeypatch))
    assert "wecom-body-ok" in art["artifact"]
    assert added[1]["url"] in art["artifact"]
    disk = json.loads((serve / ".kairo" / "projects" / pid / "project.json").read_text())
    assert "token" not in json.dumps(disk).lower()
    assert "secret" not in json.dumps(disk).lower()

    client = TestClient(create_app(serve))
    html_proj = client.get(f"/projects/{pid}")
    assert html_proj.status_code == 200
    assert "企微文档" in html_proj.text
    settings_html = client.get("/settings")
    assert settings_html.status_code == 200
    assert "企微文档" in settings_html.text
    assert "Notion" in settings_html.text
    assert "本期未接入" in settings_html.text
    wecom_card = settings_html.text.split("企微文档", 1)[1].split("</li>", 1)[0]
    notion_card = settings_html.text.split("Notion", 1)[1].split("</li>", 1)[0]
    assert "本期未接入" not in wecom_card
    assert "授权" in wecom_card or "Authorize" in wecom_card
    assert "本期未接入" in notion_card or "Not connected this phase" in notion_card

    api_add = client.post(
        f"/api/projects/{pid}/datasources",
        json={"url": "https://doc.weixin.qq.com/sheet/e3_ApiSheet"},
    ).json()
    assert api_add["ok"] is True
    assert api_add["datasource"]["reader"] == "wecom"


def test_wecom_default_adapter_reads_via_injected_runner():
    from kairo.readers import PERMISSION, READ_FAILED, ReadError, read_datasource
    from kairo.settings import Connection

    class Proc:
        def __init__(self, stdout="", returncode=0, stderr=""):
            self.stdout = stdout
            self.stderr = stderr
            self.returncode = returncode

    calls: list[list[str]] = []

    def runner(argv, **_kwargs):
        calls.append(list(argv))
        if argv[:3] == ["wecom-cli", "auth", "show"]:
            return Proc("authorized\n")
        if "doc" in argv and "contents" in argv:
            return Proc(json.dumps({"name": "说明", "content": "doc-markdown"}))
        if argv[1:4] == ["sheet", "ranges", "get"]:
            return Proc(json.dumps({"content": "col,a\n1,2"}))
        if argv[1:3] == ["sheet", "get"]:
            return Proc(json.dumps({"name": "表", "sheets": [{"sheet_id": "sid", "title": "Sheet1"}]}))
        if argv[1:4] == ["smartsheet", "records", "list"]:
            return Proc(json.dumps({"records": [{"标题": "行1"}]}))
        if argv[1:4] == ["smartsheet", "sheets", "list"]:
            return Proc(json.dumps({"sheets": [{"sheet_id": "s1", "title": "需求"}]}))
        if "smartpage" in argv and "pages" in argv:
            payload = json.loads(argv[argv.index("--json") + 1])
            if payload.get("page_id"):
                return Proc(
                    json.dumps(
                        {
                            "doc_title": "文档",
                            "pages": [
                                {
                                    "page_id": payload["page_id"],
                                    "page_title": "首页",
                                    "content": {"markdown_content": "# 页正文"},
                                }
                            ],
                        }
                    )
                )
            return Proc(
                json.dumps(
                    {
                        "doc_title": "文档",
                        "pages": [{"page_id": "p1", "page_title": "首页"}],
                    }
                )
            )
        return Proc("unexpected", returncode=1, stderr="no")

    conn = Connection(authorized=True, cmd=None, token_env="")
    doc = read_datasource(
        "https://doc.weixin.qq.com/doc/e3_Doc",
        "document",
        "wecom",
        conn,
        runner=runner,
    )
    assert "doc-markdown" in doc
    sheet = read_datasource(
        "https://doc.weixin.qq.com/sheet/e3_Sheet",
        "spreadsheet",
        "wecom",
        conn,
        runner=runner,
    )
    assert "1,2" in sheet
    smart = read_datasource(
        "https://doc.weixin.qq.com/smartsheet/s3_Smart",
        "smartsheet",
        "wecom",
        conn,
        runner=runner,
    )
    assert "行1" in smart
    page = read_datasource(
        "https://page.weixin.qq.com/smartpage/p/b1_Pub",
        "smartpage",
        "wecom",
        conn,
        runner=runner,
    )
    assert "页正文" in page
    assert any(c[:1] == ["wecom-cli"] for c in calls)
    assert all(c[1:3] != ["auth", "show"] for c in calls)

    denied_calls: list[list[str]] = []

    def deny_runner(argv, **_kwargs):
        denied_calls.append(list(argv))
        return Proc("no")

    try:
        read_datasource(
            "https://doc.weixin.qq.com/doc/e3_Doc",
            "document",
            "wecom",
            Connection(authorized=False, cmd=None, token_env=""),
            runner=deny_runner,
        )
        raise AssertionError("expected permission")
    except ReadError as exc:
        assert exc.code == PERMISSION
    assert denied_calls == []

    def boom_runner(argv, **_kwargs):
        if argv[:3] == ["wecom-cli", "auth", "show"]:
            return Proc("authorized\n")
        return Proc("", returncode=2, stderr="boom")

    try:
        read_datasource(
            "https://doc.weixin.qq.com/doc/e3_Doc",
            "document",
            "wecom",
            conn,
            runner=boom_runner,
        )
        raise AssertionError("expected read_failed")
    except ReadError as exc:
        assert exc.code == READ_FAILED


def test_wecom_smartpage_title_only_is_read_failed():
    from kairo.readers import READ_FAILED, ReadError, read_datasource
    from kairo.settings import Connection

    class Proc:
        def __init__(self, stdout="", returncode=0, stderr=""):
            self.stdout = stdout
            self.stderr = stderr
            self.returncode = returncode

    def runner(argv, **_kwargs):
        if argv[:3] == ["wecom-cli", "auth", "show"]:
            return Proc("authorized\n")
        if "smartpage" in argv and "pages" in argv:
            payload = json.loads(argv[argv.index("--json") + 1])
            if payload.get("page_id"):
                return Proc(
                    json.dumps(
                        {
                            "doc_title": "仅标题",
                            "pages": [{"page_id": payload["page_id"], "page_title": "空页"}],
                        }
                    )
                )
            return Proc(
                json.dumps(
                    {
                        "doc_title": "仅标题",
                        "pages": [{"page_id": "p1", "page_title": "空页"}],
                    }
                )
            )
        return Proc("unexpected", returncode=1, stderr="no")

    try:
        read_datasource(
            "https://doc.weixin.qq.com/smartpage/a1_Empty",
            "smartpage",
            "wecom",
            Connection(authorized=True, cmd=None, token_env=""),
            runner=runner,
        )
        raise AssertionError("expected read_failed")
    except ReadError as exc:
        assert exc.code == READ_FAILED


def test_wecom_empty_payload_fields_are_read_failed():
    from kairo.readers import READ_FAILED, ReadError, read_datasource
    from kairo.settings import Connection

    class Proc:
        def __init__(self, stdout="", returncode=0, stderr=""):
            self.stdout = stdout
            self.stderr = stderr
            self.returncode = returncode

    def _runner_for(kind: str, payload: dict):
        def runner(argv, **_kwargs):
            if argv[:3] == ["wecom-cli", "auth", "show"]:
                return Proc("authorized\n")
            if kind == "document" and "doc" in argv:
                return Proc(json.dumps(payload))
            if kind == "spreadsheet" and argv[1:3] == ["sheet", "get"]:
                return Proc(json.dumps({"name": "表", "sheets": [{"sheet_id": "sid", "title": "Sheet1"}]}))
            if kind == "spreadsheet" and argv[1:4] == ["sheet", "ranges", "get"]:
                return Proc(json.dumps(payload))
            if kind == "smartsheet" and argv[1:4] == ["smartsheet", "sheets", "list"]:
                return Proc(json.dumps({"sheets": [{"sheet_id": "s1", "title": "需求"}]}))
            if kind == "smartsheet" and argv[1:4] == ["smartsheet", "records", "list"]:
                return Proc(json.dumps(payload))
            return Proc("unexpected", returncode=1, stderr="no")

        return runner

    conn = Connection(authorized=True, cmd=None, token_env="")
    cases = (
        ("https://doc.weixin.qq.com/doc/e3_Empty", "document", {"name": "说明", "content": ""}),
        ("https://doc.weixin.qq.com/sheet/e3_Empty", "spreadsheet", {"content": ""}),
        ("https://doc.weixin.qq.com/smartsheet/s3_Empty", "smartsheet", {"records": []}),
    )
    for url, kind, payload in cases:
        try:
            read_datasource(url, kind, "wecom", conn, runner=_runner_for(kind, payload))
            raise AssertionError(f"expected read_failed for {kind}")
        except ReadError as exc:
            assert exc.code == READ_FAILED


def test_wecom_saved_file_missing_is_read_failed(tmp_path):
    from kairo.readers import READ_FAILED, ReadError, read_datasource
    from kairo.settings import Connection

    class Proc:
        def __init__(self, stdout="", returncode=0, stderr=""):
            self.stdout = stdout
            self.stderr = stderr
            self.returncode = returncode

    missing = tmp_path / "gone.md"

    def runner(argv, **_kwargs):
        if argv[:3] == ["wecom-cli", "auth", "show"]:
            return Proc("authorized\n")
        if "doc" in argv and "contents" in argv:
            return Proc(json.dumps({"file_path": str(missing)}))
        return Proc("unexpected", returncode=1, stderr="no")

    try:
        read_datasource(
            "https://doc.weixin.qq.com/doc/e3_Doc",
            "document",
            "wecom",
            Connection(authorized=True, cmd=None, token_env=""),
            runner=runner,
        )
        raise AssertionError("expected read_failed")
    except ReadError as exc:
        assert exc.code == READ_FAILED


def test_wecom_sheet_tab_reads_only_matching_subsheet():
    from kairo.readers import INVALID_LINK, ReadError, read_datasource
    from kairo.settings import Connection

    class Proc:
        def __init__(self, stdout="", returncode=0, stderr=""):
            self.stdout = stdout
            self.stderr = stderr
            self.returncode = returncode

    range_ids: list[str] = []

    def runner(argv, **_kwargs):
        if argv[1:3] == ["sheet", "get"]:
            return Proc(
                json.dumps(
                    {
                        "name": "表",
                        "sheets": [
                            {"sheet_id": "keepTab", "title": "本页"},
                            {"sheet_id": "otherTab", "title": "其它"},
                        ],
                    }
                )
            )
        if argv[1:4] == ["sheet", "ranges", "get"]:
            payload = json.loads(argv[argv.index("--json") + 1])
            range_ids.append(payload.get("sheet_id"))
            return Proc(json.dumps({"content": f"id,{payload.get('sheet_id')}"}))
        raise AssertionError(f"unexpected argv: {argv}")

    conn = Connection(authorized=True, cmd=None, token_env="")
    body = read_datasource(
        "https://doc.weixin.qq.com/sheet/e3_Sheet?tab=keepTab",
        "spreadsheet",
        "wecom",
        conn,
        runner=runner,
    )
    assert range_ids == ["keepTab"]
    assert "keepTab" in body
    assert "其它" not in body

    try:
        read_datasource(
            "https://doc.weixin.qq.com/sheet/e3_Sheet?tab=missingTab",
            "spreadsheet",
            "wecom",
            conn,
            runner=runner,
        )
        raise AssertionError("expected invalid_link")
    except ReadError as exc:
        assert exc.code == INVALID_LINK
    assert range_ids == ["keepTab"]
