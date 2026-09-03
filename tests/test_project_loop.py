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

    linked = _load(_cli(["project", "link", pid, "alpha-ws"], serve, monkeypatch))
    linked = _load(_cli(["project", "link", pid, "beta-ws"], serve, monkeypatch))
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
                "--kind",
                "spreadsheet",
                "--purpose",
                "装机",
            ],
            serve,
            monkeypatch,
        )
    )
    ds_id = ds["id"]
    assert ds["connection_id"] == "tencent-docs"
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
    bad_ds = _load(
        _cli(
            [
                "datasource",
                "add",
                pid,
                "--url",
                "https://example.com/not-docs",
                "--kind",
                "spreadsheet",
            ],
            serve,
            monkeypatch,
        )
    )
    bad_link = _cli(["datasource", "read", pid, bad_ds["id"]], serve, monkeypatch)
    assert json.loads(bad_link.output)["code"] == "invalid_link"

    _load(_cli(["settings", "set", "connections.tencent-docs.cmd", deny_cmd], serve, monkeypatch))
    perm2 = json.loads(_cli(["datasource", "read", pid, ds_id], serve, monkeypatch).output)
    assert perm2["code"] == "permission"

    _load(_cli(["settings", "set", "connections.tencent-docs.cmd", miss_cmd], serve, monkeypatch))
    miss = json.loads(_cli(["datasource", "read", pid, ds_id], serve, monkeypatch).output)
    assert miss["code"] == "invalid_link"

    _load(_cli(["settings", "set", "connections.tencent-docs.cmd", boom_cmd], serve, monkeypatch))
    boom = json.loads(_cli(["datasource", "read", pid, ds_id], serve, monkeypatch).output)
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
    failed = _cli(["task", "run", pid, tid], serve, monkeypatch)
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
    assert "test-token-not-for-project" not in json.dumps(settings_api)

    patched = client.patch("/api/settings", json={"path": "general.locale", "value": "en"}).json()
    assert patched["settings"]["general"]["locale"] == "en"
    cli_locale = _load(_cli(["settings", "show"], serve, monkeypatch))
    assert cli_locale["general"]["locale"] == "en"

    html_projects = client.get("/projects")
    assert html_projects.status_code == 200
    assert "综合能源" in html_projects.text
    assert "Projects" in html_projects.text or "项目" in html_projects.text
    html_proj = client.get(f"/projects/{pid}")
    assert html_proj.status_code == 200
    assert "alpha-ws" in html_proj.text
    html_art = client.get(f"/projects/{pid}/runs/{run1['id']}")
    assert html_art.status_code == 200
    assert run1["id"] in html_art.text
    assert "Task version: 1" in html_art.text or "v1" in html_art.text

    settings_html = client.get("/settings")
    assert settings_html.status_code == 200
    assert "General" in settings_html.text
    assert "Projects" in settings_html.text
    assert "Workspaces" in settings_html.text
    assert "Timeline" in settings_html.text
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
            "url": "https://docs.qq.com/smartsheet/Senergy",
            "kind": "smartsheet",
            "purpose": "风险",
        },
    ).json()
    assert ds2["ok"] is True
    read_api = client.post(f"/api/projects/{pid}/datasources/{ds2['datasource']['id']}/read").json()
    assert read_api["ok"] is True
    task_api = client.post(
        f"/api/projects/{pid}/tasks",
        json={"name": "风险清单", "datasource_id": ds2["datasource"]["id"], "schedule": "interval"},
    ).json()
    run_api = client.post(
        f"/api/projects/{pid}/tasks/{task_api['task']['id']}/run"
    ).json()
    assert run_api["run"]["status"] == "succeeded"
    got = client.get(f"/api/projects/{pid}/runs/{run_api['run']['id']}").json()
    assert "Task version:" in got["artifact"]
    assert "https://docs.qq.com/smartsheet/Senergy" in got["artifact"]
    assert run_api["run"]["id"] in got["artifact"]
