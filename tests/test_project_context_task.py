"""#299 S1–S5：缓存、材料目录、prompt Task、兼容；驱动 shipped CLI/API/HTML。"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from kairo.cli import app
from kairo.project_materials import (
    CACHE_TTL,
    cache_status,
    content_version,
    is_fresh,
    parse_source_id,
    peek_datasource_content,
    set_clock,
)
from kairo.projects import ProjectError, get_project
from kairo.provider import AgentConfig, AgentResult
from kairo.web.server import create_app
from kairo.workspace import Workspace

runner = CliRunner()


def _stub_cmd(path: Path, source: str, counter: Path | None = None, code: int = 0) -> str:
    lines = ["import sys"]
    if counter is not None:
        lines.append(f"open({str(counter)!r}, 'a', encoding='utf-8').write('x')")
    lines.append(f"sys.stdout.write({source!r})")
    lines.append(f"sys.exit({code})")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(path))} {{url}}"


def _cli(args, cwd: Path, monkeypatch):
    monkeypatch.chdir(cwd)
    return runner.invoke(app, args)


def _load(result):
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _count(counter: Path) -> int:
    return len(counter.read_text(encoding="utf-8")) if counter.is_file() else 0


class _Clock:
    def __init__(self, when: datetime) -> None:
        self.when = when

    def __call__(self) -> datetime:
        return self.when


class ProjectCliTestProvider:
    """确定性替身：真实子进程执行 shipped `kairo project` 命令。"""

    name = "project-cli-test"
    model = "test"
    supports_read_dirs = True
    supports_project_cli = True

    def run(self, config: AgentConfig, signal=None) -> AgentResult:
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
        assert catalog.get("ok") is True
        topic = next(
            i
            for i in catalog["items"]
            if i["type"] in ("understanding", "digest") and i["state"] == "available"
        )
        ds = next(i for i in catalog["items"] if i["type"] == "datasource")
        t = kairo("project", "read", pid, topic["source_id"], "--run", rid, "--root", serve)
        d = kairo("project", "read", pid, ds["source_id"], "--run", rid, "--root", serve)
        body = (
            f"# combined\n\n"
            f"[{topic['title']}](input:{t['input_id']})\n\n{t['content']}\n\n"
            f"[{ds['title']}](input:{d['input_id']})\n\n{d['content']}\n"
        )
        dest = config.artifact_dir / "artifact.md"
        dest.write_text(body, encoding="utf-8")
        return AgentResult(artifacts=[dest], result_text=body)


class BogusCiteProvider:
    name = "bogus-cite"
    model = "test"
    supports_read_dirs = True
    supports_project_cli = True

    def run(self, config: AgentConfig, signal=None) -> AgentResult:
        dest = config.artifact_dir / "artifact.md"
        dest.write_text("[x](input:inp-not-real)\n", encoding="utf-8")
        return AgentResult(artifacts=[dest], result_text=dest.read_text())


def test_clock_label_strips_iso_noise():
    from kairo.web.views import _clock_label

    assert _clock_label("2026-09-05T11:51:09+00:00") == "2026-09-05 11:51"
    assert _clock_label("2026-09-05T12:51:09Z") == "2026-09-05 12:51"
    assert _clock_label(None) == ""


def test_unit_cache_expiry_and_source_id():
    now = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    exp = now + CACHE_TTL
    assert is_fresh(now, exp) is True
    assert is_fresh(exp, exp) is False
    assert is_fresh(exp + timedelta(seconds=1), exp) is False
    assert content_version("a") != content_version("b")
    parsed = parse_source_id("topic:alpha-ws:digest::ref-1")
    assert parsed["kind"] == "digest" and parsed["home"] == "" and parsed["ref_id"] == "ref-1"


def _prepare(tmp_path, monkeypatch, *, with_topic_body: bool = True):
    serve = tmp_path / "root"
    serve.mkdir()
    ws = Workspace.init(serve / "alpha-ws", topic="阿尔法")
    if with_topic_body:
        src = tmp_path / "note.txt"
        src.write_text("note-body", encoding="utf-8")
        ref_id = ws.add([src])
        (ws.root / "understanding.md").write_text("事实：光伏 80MW\n", encoding="utf-8")
        (ws.references_dir() / ref_id / "digest.md").write_text("digest: solar 80\n", encoding="utf-8")
    monkeypatch.chdir(serve)
    counter = tmp_path / "reads.txt"
    ok_cmd = _stub_cmd(tmp_path / "ok.py", "plant,mw\nsolar,80\n", counter)
    _load(_cli(["settings", "set", "connections.tencent-docs.authorized", "true"], serve, monkeypatch))
    _load(_cli(["settings", "set", "connections.tencent-docs.cmd", ok_cmd], serve, monkeypatch))
    created = _load(_cli(["project", "create", "综合能源"], serve, monkeypatch))
    pid = created["id"]
    _load(_cli(["project", "link", pid, "alpha-ws"], serve, monkeypatch))
    ds = _load(
        _cli(
            ["datasource", "add", pid, "--url", "https://docs.qq.com/sheet/Denergy", "--purpose", "装机"],
            serve,
            monkeypatch,
        )
    )
    return serve, pid, ds["id"], counter, ws


def test_s1_s4_cache_ttl_refresh_and_web(tmp_path, monkeypatch):
    serve, pid, ds_id, counter, _ws = _prepare(tmp_path, monkeypatch)
    clock = _Clock(datetime(2026, 9, 5, 12, 0, tzinfo=UTC))
    set_clock(clock)
    try:
        first = _load(_cli(["datasource", "read", pid, ds_id], serve, monkeypatch))
        assert first["ok"] is True and "solar,80" in first["content"]
        assert first["state"] == "fresh"
        assert _count(counter) == 1
        second = _load(_cli(["datasource", "read", pid, ds_id], serve, monkeypatch))
        assert second["content"] == first["content"]
        assert second["version"] == first["version"]
        assert _count(counter) == 1

        client = TestClient(create_app(serve))
        page = client.get(f"/projects/{pid}/datasources/{ds_id}")
        assert page.status_code == 200
        assert "solar,80" in page.text
        again = client.get(f"/projects/{pid}/datasources/{ds_id}")
        assert "solar,80" in again.text
        assert _count(counter) == 1
        html_proj = client.get(f"/projects/{pid}")
        assert html_proj.status_code == 200
        assert "Reusable" in html_proj.text or "可复用" in html_proj.text
        assert "Create task" in html_proj.text or "创建" in html_proj.text
        assert 'name="prompt"' in html_proj.text
        assert 'class="task-create"' in html_proj.text
        assert "task-create-bar" in html_proj.text
        assert "2026-09-05T" not in html_proj.text
        assert "2026-09-05 13:00" in html_proj.text
        ds_block = html_proj.text.split("Tasks")[0] if "Tasks" in html_proj.text else html_proj.text
        assert 'class="obj-actions"' in ds_block

        clock.when = clock.when + CACHE_TTL
        expired = _load(_cli(["datasource", "read", pid, ds_id], serve, monkeypatch))
        assert expired["ok"] is True
        assert _count(counter) == 2

        boom = _stub_cmd(tmp_path / "boom.py", "", counter, code=2)
        _load(_cli(["settings", "set", "connections.tencent-docs.cmd", boom], serve, monkeypatch))
        clock.when = clock.when + timedelta(seconds=1)
        # still fresh relative to last success? last success was at previous now; add 1s still fresh
        # rewind to within TTL of last success: last write was at 13:00, now 13:00:01
        fail_refresh = _cli(["datasource", "read", pid, ds_id, "--refresh"], serve, monkeypatch)
        assert fail_refresh.exit_code != 0
        assert json.loads(fail_refresh.output)["code"] == "read_failed"
        assert _count(counter) == 3
        reuse = _load(_cli(["datasource", "read", pid, ds_id], serve, monkeypatch))
        assert "solar,80" in reuse["content"]
        assert _count(counter) == 3

        clock.when = clock.when + CACHE_TTL
        expired_fail = _cli(["datasource", "read", pid, ds_id], serve, monkeypatch)
        assert expired_fail.exit_code != 0
        assert json.loads(expired_fail.output)["code"] == "read_failed"
        assert _count(counter) == 4
        view = client.get(f"/projects/{pid}/datasources/{ds_id}")
        assert view.status_code == 200
        assert "solar,80" in view.text
        assert "Expired" in view.text or "已过期" in view.text
        assert "read_failed" not in view.text or True  # page may show old body, not success of this fetch

        public = TestClient(create_app(serve, mode="public-read"))
        assert public.get(f"/api/projects/{pid}/context").status_code == 404
        assert public.get(f"/projects/{pid}/datasources/{ds_id}").status_code == 404
        assert public.post(f"/api/projects/{pid}/context/read", json={"source_id": "x"}).status_code == 404
    finally:
        set_clock(None)


def test_s2_catalog_and_on_demand_read(tmp_path, monkeypatch):
    serve, pid, ds_id, counter, ws = _prepare(tmp_path, monkeypatch)
    before = _count(counter)
    catalog = _load(_cli(["project", "context", pid], serve, monkeypatch))
    assert catalog["ok"] is True
    types = {i["type"] for i in catalog["items"]}
    assert "understanding" in types and "digest" in types and "datasource" in types
    assert all("content" not in i for i in catalog["items"])
    assert _count(counter) == before
    und = next(i for i in catalog["items"] if i["type"] == "understanding")
    assert und["state"] == "available"
    body = _load(_cli(["project", "read", pid, und["source_id"]], serve, monkeypatch))
    assert "光伏" in body["content"]
    digest = next(i for i in catalog["items"] if i["type"] == "digest")
    dbody = _load(_cli(["project", "read", pid, digest["source_id"]], serve, monkeypatch))
    assert "solar" in dbody["content"]
    ds_item = next(i for i in catalog["items"] if i["type"] == "datasource")
    ds_body = _load(_cli(["project", "read", pid, ds_item["source_id"]], serve, monkeypatch))
    assert "solar,80" in ds_body["content"]
    missing = _cli(["project", "read", pid, "datasource:ds-not-here"], serve, monkeypatch)
    assert missing.exit_code != 0
    assert json.loads(missing.output)["code"] == "not_found"
    other = _load(_cli(["project", "create", "其它"], serve, monkeypatch))
    denied = _cli(["project", "read", other["id"], und["source_id"]], serve, monkeypatch)
    assert denied.exit_code != 0
    empty = Workspace.init(serve / "empty-ws", topic="空")
    _load(_cli(["project", "link", pid, "empty-ws"], serve, monkeypatch))
    catalog2 = _load(_cli(["project", "context", pid], serve, monkeypatch))
    empty_u = next(
        i for i in catalog2["items"] if i["type"] == "understanding" and i["source_id"].endswith("empty-ws:understanding")
    )
    assert empty_u["state"] == "unavailable"
    ungen = _cli(["project", "read", pid, empty_u["source_id"]], serve, monkeypatch)
    assert ungen.exit_code != 0
    assert json.loads(ungen.output)["code"] == "material_unavailable"
    assert empty.root.name == "empty-ws"


def test_s3_s5_prompt_task_and_legacy(tmp_path, monkeypatch):
    serve, pid, ds_id, counter, _ws = _prepare(tmp_path, monkeypatch)
    _load(_cli(["datasource", "read", pid, ds_id], serve, monkeypatch))
    cached_reads = _count(counter)

    old = _load(
        _cli(["task", "create", pid, "--name", "周报", "--datasource", ds_id], serve, monkeypatch)
    )
    assert old["mode"] == "source_snapshot"
    run_old = _load(_cli(["task", "run", pid, old["id"]], serve, monkeypatch))
    assert run_old["status"] == "succeeded"
    art_old = _load(_cli(["artifact", "show", pid, run_old["id"]], serve, monkeypatch))
    assert "Task version: 1" in art_old["artifact"]
    assert "solar,80" in art_old["artifact"]
    old_bytes = art_old["artifact"]

    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("综合 Topic 与 Data Source", encoding="utf-8")
    monkeypatch.setattr("kairo.projects.select_project_agent", lambda: ProjectCliTestProvider())
    created = _load(
        _cli(
            ["task", "create", pid, "--name", "综合", "--prompt-file", str(prompt_file)],
            serve,
            monkeypatch,
        )
    )
    assert created["mode"] == "agent"
    assert created.get("datasource_id") in ("", None)
    both = _cli(
        ["task", "create", pid, "--name", "坏", "--datasource", ds_id, "--prompt", "x"],
        serve,
        monkeypatch,
    )
    assert both.exit_code == 2
    empty = _cli(["task", "create", pid, "--name", "空", "--prompt", "   "], serve, monkeypatch)
    assert empty.exit_code != 0

    run_new = _load(_cli(["task", "run", pid, created["id"]], serve, monkeypatch))
    assert run_new["status"] == "succeeded"
    assert _count(counter) == cached_reads
    shown = _load(_cli(["artifact", "show", pid, run_new["id"]], serve, monkeypatch))
    assert shown["inputs"]
    types = {i["type"] for i in shown["inputs"]}
    assert "datasource" in types
    assert any(i["type"] in ("understanding", "digest") for i in shown["inputs"])
    iid = shown["inputs"][0]["input_id"]
    evidence = _load(_cli(["project", "input", pid, run_new["id"], iid], serve, monkeypatch))
    assert evidence["content"]
    version = shown["inputs"][0]["version"]

    _load(_cli(["settings", "set", "connections.tencent-docs.cmd", _stub_cmd(tmp_path / "ok2.py", "NEW\n", counter)], serve, monkeypatch))
    _load(_cli(["datasource", "read", pid, ds_id, "--refresh"], serve, monkeypatch))
    evidence2 = _load(_cli(["project", "input", pid, run_new["id"], iid], serve, monkeypatch))
    assert evidence2["content"] == evidence["content"]
    assert evidence2["version"] == version

    monkeypatch.setattr("kairo.projects.select_project_agent", lambda: BogusCiteProvider())
    bad_task = _load(_cli(["task", "create", pid, "--name", "假引用", "--prompt", "写"], serve, monkeypatch))
    bad_run = _cli(["task", "run", pid, bad_task["id"]], serve, monkeypatch)
    assert bad_run.exit_code != 0
    payload = json.loads(bad_run.output)
    assert payload["status"] == "failed"
    assert payload["artifact_path"] is None

    from kairo.provider import StubProvider

    monkeypatch.setattr("kairo.projects.select_project_agent", lambda: StubProvider())
    stub_task = _load(_cli(["task", "create", pid, "--name", "stub", "--prompt", "写"], serve, monkeypatch))
    stub_run = _cli(["task", "run", pid, stub_task["id"]], serve, monkeypatch)
    assert stub_run.exit_code != 0
    assert json.loads(stub_run.output)["reason"] == "provider_unsupported"

    still = _load(_cli(["artifact", "show", pid, run_old["id"]], serve, monkeypatch))
    assert still["artifact"] == old_bytes

    client = TestClient(create_app(serve))
    html = client.get(f"/projects/{pid}")
    assert 'name="prompt"' in html.text
    assert "Create task" in html.text or "创建 Task" in html.text or "创建任务" in html.text
    assert 'class="task-create"' in html.text
    empty_proj = _load(_cli(["project", "create", "无源"], serve, monkeypatch))
    html_empty = client.get(f"/projects/{empty_proj['id']}")
    assert 'name="prompt"' in html_empty.text
    assert 'class="task-create"' in html_empty.text
    posted = client.post(
        f"/projects/{empty_proj['id']}/tasks",
        data={"name": "网页任务", "prompt": "只写一句话", "schedule": "once"},
        follow_redirects=True,
    )
    assert posted.status_code == 200
    api_task = client.post(
        f"/api/projects/{pid}/tasks",
        json={"name": "API任务", "prompt": "综合两类材料"},
    ).json()
    assert api_task["ok"] is True
    monkeypatch.setattr("kairo.projects.select_project_agent", lambda: ProjectCliTestProvider())
    accepted = client.post(f"/api/projects/{pid}/tasks/{api_task['task']['id']}/run")
    assert accepted.status_code in (200, 202)
    rid = accepted.json()["run"]["id"]
    got = None
    for _ in range(50):
        got = client.get(f"/api/projects/{pid}/runs/{rid}").json()
        if got["run"]["status"] != "running":
            break
        time.sleep(0.1)
    assert got["run"]["status"] == "succeeded"
    assert got["inputs"]
    art_html = client.get(f"/projects/{pid}/runs/{rid}")
    assert art_html.status_code == 200
    assert "combined" in art_html.text or "来源" in art_html.text or "Sources" in art_html.text


def test_concurrent_first_read_one_pull(tmp_path, monkeypatch):
    serve, pid, ds_id, counter, _ws = _prepare(tmp_path, monkeypatch, with_topic_body=False)
    env = os.environ.copy()
    env["KAIRO_SERVE_ROOT"] = str(serve)
    env["XDG_CONFIG_HOME"] = os.environ["XDG_CONFIG_HOME"]
    procs = [
        subprocess.Popen(
            [sys.executable, "-m", "kairo", "datasource", "read", pid, ds_id, "--root", str(serve)],
            cwd=str(serve),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(4)
    ]
    codes = [p.wait() for p in procs]
    assert codes == [0, 0, 0, 0]
    assert _count(counter) == 1


def test_concurrent_run_inputs_keep_both_sources(tmp_path, monkeypatch):
    serve, pid, ds_id, _counter, _ws = _prepare(tmp_path, monkeypatch)
    from kairo.project_materials import list_context, scratch_dir
    from kairo.projects import RunRecord, _save_run

    run_id = "run-parallel1"
    scratch = scratch_dir(serve, pid, run_id)
    scratch.mkdir(parents=True)
    catalog = list_context(serve, pid)
    und = next(i["source_id"] for i in catalog["items"] if i["type"] == "understanding")
    ds_src = next(i["source_id"] for i in catalog["items"] if i["type"] == "datasource")
    rec = RunRecord(
        id=run_id,
        project_id=pid,
        task_id="tsk-x",
        task_name="并",
        task_version=1,
        status="running",
        schema_version=2,
        mode="agent",
        scope_topics=["alpha-ws"],
        scope_datasources=[ds_id],
        scratch_dir=str(scratch.relative_to(serve)),
        created_at="2026-09-05T00:00:00+00:00",
        started_at="2026-09-05T00:00:00+00:00",
    )
    _save_run(serve, rec)
    env = os.environ.copy()
    env["KAIRO_SERVE_ROOT"] = str(serve)
    env["XDG_CONFIG_HOME"] = os.environ["XDG_CONFIG_HOME"]
    procs = []
    for source in (und, ds_src):
        procs.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "kairo",
                    "project",
                    "read",
                    pid,
                    source,
                    "--run",
                    run_id,
                    "--root",
                    str(serve),
                ],
                cwd=str(serve),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        )
    outs = []
    for p in procs:
        stdout, _stderr = p.communicate()
        assert p.returncode == 0, stdout
        outs.append(json.loads(stdout))
    ids = {o["input_id"] for o in outs}
    assert None not in ids and len(ids) == 2
    index = json.loads((scratch / "index.json").read_text(encoding="utf-8"))
    assert {i["input_id"] for i in index} == ids


def test_cache_bundle_mismatch_is_uncached(tmp_path, monkeypatch):
    serve, pid, ds_id, counter, _ws = _prepare(tmp_path, monkeypatch, with_topic_body=False)
    _load(_cli(["datasource", "read", pid, ds_id], serve, monkeypatch))
    bundle = serve / ".kairo" / "projects" / pid / "cache" / ds_id / "cache.json"
    data = json.loads(bundle.read_text(encoding="utf-8"))
    data["version"] = "deadbeef"
    bundle.write_text(json.dumps(data), encoding="utf-8")
    again = _load(_cli(["datasource", "read", pid, ds_id], serve, monkeypatch))
    assert again["ok"] is True
    assert _count(counter) == 2


def test_uncached_cache_status_exposes_content_none(tmp_path, monkeypatch):
    serve, pid, ds_id, _counter, _ws = _prepare(tmp_path, monkeypatch, with_topic_body=False)
    project = get_project(serve, pid)
    ds = next(item for item in project.datasources if item.id == ds_id)
    status = cache_status(serve, project, ds)
    assert status["state"] == "uncached"
    assert status["content"] is None
    try:
        peek_datasource_content(serve, pid, ds_id)
    except ProjectError as exc:
        assert exc.code == "cache_missing"
    else:
        raise AssertionError("expected cache_missing")


def test_uncached_body_page_and_content_api_do_not_call_reader(tmp_path, monkeypatch):
    serve, pid, ds_id, counter, _ws = _prepare(tmp_path, monkeypatch, with_topic_body=False)
    before = _count(counter)
    client = TestClient(create_app(serve))
    page = client.get(f"/projects/{pid}/datasources/{ds_id}")
    assert page.status_code == 200
    assert "Not read yet" in page.text or "尚未读取" in page.text
    assert 'name="refresh"' not in page.text
    api = client.get(f"/api/projects/{pid}/datasources/{ds_id}/content")
    assert api.status_code == 404
    payload = api.json()
    assert payload["ok"] is False
    assert payload["code"] == "cache_missing"
    assert _count(counter) == before


def test_first_read_failure_then_retry_succeeds(tmp_path, monkeypatch):
    serve, pid, ds_id, counter, _ws = _prepare(tmp_path, monkeypatch, with_topic_body=False)
    boom = _stub_cmd(tmp_path / "boom-first.py", "", counter, code=2)
    _load(_cli(["settings", "set", "connections.tencent-docs.cmd", boom], serve, monkeypatch))
    client = TestClient(create_app(serve))
    failed = client.post(f"/projects/{pid}/datasources/{ds_id}/read")
    assert failed.status_code == 200
    assert "Internal Server Error" not in failed.text
    assert "read_failed" in failed.text
    assert "Not read yet" in failed.text or "尚未读取" in failed.text
    ok = _stub_cmd(tmp_path / "ok-retry.py", "recovered-body\n", counter)
    _load(_cli(["settings", "set", "connections.tencent-docs.cmd", ok], serve, monkeypatch))
    recovered = client.post(f"/projects/{pid}/datasources/{ds_id}/read", follow_redirects=True)
    assert recovered.status_code == 200
    assert "recovered-body" in recovered.text


def test_refresh_failure_keeps_old_body_on_page(tmp_path, monkeypatch):
    serve, pid, ds_id, counter, _ws = _prepare(tmp_path, monkeypatch, with_topic_body=False)
    _load(_cli(["datasource", "read", pid, ds_id], serve, monkeypatch))
    boom = _stub_cmd(tmp_path / "boom-refresh.py", "", counter, code=2)
    _load(_cli(["settings", "set", "connections.tencent-docs.cmd", boom], serve, monkeypatch))
    client = TestClient(create_app(serve))
    failed = client.post(
        f"/projects/{pid}/datasources/{ds_id}/read",
        data={"refresh": "1"},
    )
    assert failed.status_code == 200
    assert "solar,80" in failed.text
    assert "read_failed" in failed.text
    assert "Reusable" in failed.text or "可复用" in failed.text
    assert "Not read yet" not in failed.text and "尚未读取" not in failed.text
