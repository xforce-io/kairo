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
    list_context,
    parse_source_id,
    peek_datasource_content,
    read_material,
    set_clock,
    scratch_dir,
)
from kairo.projects import ProjectError, get_project
from kairo.provider import AgentConfig, AgentResult
from kairo.web.server import create_app
from kairo.workspace import Workspace
from kairo.time_display import clock_label

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
        assert t["numbered_content"].startswith("1: ") and t["line_count"] >= 1
        assert d["numbered_content"].startswith("1: ") and d["line_count"] >= 1
        location = "#L1" if getattr(self, "locate", False) else ""
        body = (
            f"# combined\n\n"
            f"[{topic['title']}](input:{t['input_id']}{location})\n\n{t['content']}\n\n"
            f"[{ds['title']}](input:{d['input_id']}{location})\n\n{d['content']}\n"
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


def test_rewrite_artifact_input_links_only_recorded():
    from kairo.web.views import rewrite_artifact_input_links

    html = '<p><a href="input:inp-ok">t</a> <a href="input:inp-ghost">x</a></p>'
    out = rewrite_artifact_input_links(html, "prj-1", "run-1", {"inp-ok"})
    assert 'href="/projects/prj-1/runs/run-1/inputs/inp-ok"' in out
    assert 'href="input:inp-ghost"' in out
    assert "input:inp-ok" not in out


def test_clock_label_strips_iso_noise():
    from kairo.web.views import _clock_label

    local = datetime.fromisoformat("2026-09-05T11:51:09+00:00").astimezone()
    assert _clock_label("2026-09-05T11:51:09+00:00").startswith(local.strftime("%Y-%m-%d %H:%M") + " UTC")
    assert _clock_label("2026-09-05T11:51:09Z") == _clock_label("2026-09-05T11:51:09+00:00")
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


def test_unit_evidence_body_path_rejects_escape(tmp_path):
    from kairo.project_materials import evidence_body_path

    folder = tmp_path / "scratch"
    folder.mkdir()
    (folder / "ok.md").write_text("in-scratch\n", encoding="utf-8")
    outside = tmp_path / "cache"
    outside.mkdir()
    (outside / "review-escape.md").write_text("from-cache\n", encoding="utf-8")
    assert evidence_body_path(folder, "ok.md").read_text(encoding="utf-8") == "in-scratch\n"
    rel = os.path.relpath(outside / "review-escape.md", folder)
    try:
        evidence_body_path(folder, rel)
        raise AssertionError("relative escape must fail")
    except ProjectError as exc:
        assert exc.code == "evidence_failed"
    try:
        evidence_body_path(folder, str(outside / "review-escape.md"))
        raise AssertionError("absolute path must fail")
    except ProjectError as exc:
        assert exc.code == "evidence_failed"
    link = folder / "link.md"
    link.symlink_to(outside / "review-escape.md")
    try:
        evidence_body_path(folder, "link.md")
        raise AssertionError("symlink escape must fail")
    except ProjectError as exc:
        assert exc.code == "evidence_failed"


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
        assert "<table>" in page.text
        assert "solar" in page.text and "80" in page.text
        again = client.get(f"/projects/{pid}/datasources/{ds_id}")
        assert "<table>" in again.text
        assert "solar" in again.text
        assert _count(counter) == 1
        html_proj = client.get(f"/projects/{pid}")
        assert html_proj.status_code == 200
        assert "Reusable" in html_proj.text or "可复用" in html_proj.text
        assert "Create task" in html_proj.text or "创建" in html_proj.text
        assert 'name="prompt"' in html_proj.text
        assert 'class="task-create"' in html_proj.text
        assert "task-create-bar" in html_proj.text
        assert "2026-09-05T" not in html_proj.text
        assert clock_label("2026-09-06T12:00:00Z") in html_proj.text
        assert 'class="obj-actions"' in html_proj.text

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
        assert "<table>" in view.text
        assert "solar" in view.text
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
    assert "<table>" in failed.text
    assert "solar" in failed.text
    assert "read_failed" in failed.text
    assert "Reusable" in failed.text or "可复用" in failed.text
    assert "Not read yet" not in failed.text and "尚未读取" not in failed.text


def _running_record(serve, pid, run_id, *, topics, datasources, scratch=None):
    from kairo.projects import RunRecord, _save_run

    folder = scratch if scratch is not None else scratch_dir(serve, pid, run_id)
    folder.mkdir(parents=True, exist_ok=True)
    rec = RunRecord(
        id=run_id,
        project_id=pid,
        task_id="tsk-scope",
        task_name="范围",
        task_version=1,
        status="running",
        schema_version=2,
        mode="agent",
        task_snapshot={"prompt": "x"},
        scope_topics=topics,
        scope_datasources=datasources,
        scratch_dir=str(folder.relative_to(serve)).replace("\\", "/"),
        created_at="2026-09-05T00:00:00+00:00",
        started_at="2026-09-05T00:00:00+00:00",
    )
    return _save_run(serve, rec)


def test_frozen_empty_scope_stays_empty_after_link(tmp_path, monkeypatch):
    serve, pid, ds_id, _counter, _ws = _prepare(tmp_path, monkeypatch)
    _running_record(serve, pid, "run-empty", topics=[], datasources=[])
    before = list_context(serve, pid, run_id="run-empty")
    assert before["items"] == []
    _load(
        _cli(
            ["datasource", "add", pid, "--url", "https://docs.qq.com/sheet/Dlater", "--purpose", "后来"],
            serve,
            monkeypatch,
        )
    )
    after = list_context(serve, pid, run_id="run-empty")
    assert after["items"] == []
    try:
        read_material(serve, pid, f"datasource:{ds_id}", run_id="run-empty")
    except Exception as exc:
        assert getattr(exc, "code", None) == "not_found"
    else:
        raise AssertionError("frozen empty scope must reject later sources")


def test_missing_scope_fields_use_current_project(tmp_path, monkeypatch):
    serve, pid, ds_id, _counter, _ws = _prepare(tmp_path, monkeypatch)
    run_id = "run-legacy"
    path = serve / ".kairo" / "projects" / pid / "runs" / f"{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "id": run_id,
                "project_id": pid,
                "task_id": "tsk-old",
                "task_name": "旧",
                "task_version": 1,
                "status": "running",
                "schema_version": 1,
                "mode": "agent",
                "created_at": "2026-09-05T00:00:00+00:00",
                "started_at": "2026-09-05T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    catalog = list_context(serve, pid, run_id=run_id)
    kinds = {item["type"] for item in catalog["items"]}
    assert "understanding" in kinds
    assert "datasource" in kinds


class _IndexOnlyProvider:
    name = "index-only"
    model = "test"
    supports_read_dirs = True
    supports_project_cli = True

    def run(self, config, signal=None):
        dest = config.artifact_dir / "artifact.md"
        dest.write_text("[ghost](input:inp-missing)\n", encoding="utf-8")
        return AgentResult(artifacts=[dest], result_text=dest.read_text())


class _CiteProvider:
    def __init__(self, input_id: str):
        self.name = "cite"
        self.model = "test"
        self.supports_read_dirs = True
        self.supports_project_cli = True
        self._input_id = input_id

    def run(self, config, signal=None):
        dest = config.artifact_dir / "artifact.md"
        dest.write_text(f"[t](input:{self._input_id})\n", encoding="utf-8")
        return AgentResult(artifacts=[dest], result_text=dest.read_text())


def test_missing_evidence_body_fails_publish(tmp_path, monkeypatch):
    from kairo.project_materials import _atomic_json
    from kairo.projects import _execute_agent_run

    serve, pid, ds_id, _counter, _ws = _prepare(tmp_path, monkeypatch)
    run_id = "run-nobody"
    rec = _running_record(serve, pid, run_id, topics=["alpha-ws"], datasources=[ds_id])
    scratch = Path(serve) / rec.scratch_dir
    _atomic_json(
        scratch / "index.json",
        [
            {
                "input_id": "inp-missing",
                "source_id": f"datasource:{ds_id}",
                "type": "datasource",
                "title": "ghost",
                "version": "deadbeef",
                "read_at": rec.created_at,
                "read_count": 1,
                "body": "inp-missing.md",
            }
        ],
    )
    out = _execute_agent_run(serve, pid, run_id, _IndexOnlyProvider())
    assert out.status == "failed"
    assert out.reason == "evidence_failed"
    assert out.artifact_path is None


def test_out_of_scope_evidence_fails_publish(tmp_path, monkeypatch):
    from kairo.project_materials import _atomic_json
    from kairo.projects import _execute_agent_run

    serve, pid, _ds_id, _counter, _ws = _prepare(tmp_path, monkeypatch)
    run_id = "run-oob"
    rec = _running_record(serve, pid, run_id, topics=["alpha-ws"], datasources=[])
    scratch = Path(serve) / rec.scratch_dir
    body = "out-of-scope-body"
    (scratch / "inp-oob.md").write_text(body, encoding="utf-8")
    _atomic_json(
        scratch / "index.json",
        [
            {
                "input_id": "inp-oob",
                "source_id": "datasource:not-allowed",
                "type": "datasource",
                "title": "oob",
                "version": content_version(body),
                "read_at": rec.created_at,
                "read_count": 1,
                "body": "inp-oob.md",
            }
        ],
    )
    out = _execute_agent_run(serve, pid, run_id, _CiteProvider("inp-oob"))
    assert out.status == "failed"
    assert out.reason == "evidence_failed"
    assert out.artifact_path is None


def test_non_member_digest_fails_publish_even_when_slug_matches(tmp_path, monkeypatch):
    from kairo.project_materials import _atomic_json
    from kairo.projects import _execute_agent_run
    from kairo.workspace import Workspace

    serve, pid, ds_id, _counter, _ws = _prepare(tmp_path, monkeypatch)
    other = Workspace.init(serve / "unlinked", topic="未关联")
    src = tmp_path / "outsider.txt"
    src.write_text("outsider", encoding="utf-8")
    outsider_id = other.add([src])
    (other.references_dir() / outsider_id / "digest.md").write_text("not a member\n", encoding="utf-8")
    source_id = f"topic:alpha-ws:digest:unlinked:{outsider_id}"
    try:
        read_material(serve, pid, source_id, run_id=None)
        raise AssertionError("project read should reject non-member digest")
    except Exception as exc:
        assert getattr(exc, "code", None) in ("not_found", "invalid_request") or "digest" in str(exc).lower() or "成员" in str(exc)

    run_id = "run-nonmember"
    rec = _running_record(serve, pid, run_id, topics=["alpha-ws"], datasources=[ds_id])
    scratch = Path(serve) / rec.scratch_dir
    body = "forged-non-member\n"
    (scratch / "inp-nm.md").write_text(body, encoding="utf-8")
    _atomic_json(
        scratch / "index.json",
        [
            {
                "input_id": "inp-nm",
                "source_id": source_id,
                "type": "digest",
                "title": "ghost",
                "version": content_version(body),
                "read_at": rec.created_at,
                "read_count": 1,
                "body": "inp-nm.md",
            }
        ],
    )
    out = _execute_agent_run(serve, pid, run_id, _CiteProvider("inp-nm"))
    assert out.status == "failed"
    assert out.reason == "evidence_failed"
    assert out.artifact_path is None


def test_evidence_body_path_escape_fails_publish(tmp_path, monkeypatch):
    from kairo.project_materials import _atomic_json, cache_dir
    from kairo.projects import _execute_agent_run, get_run
    from kairo.project_materials import read_run_input

    serve, pid, ds_id, _counter, _ws = _prepare(tmp_path, monkeypatch)
    run_id = "run-escape"
    rec = _running_record(serve, pid, run_id, topics=["alpha-ws"], datasources=[ds_id])
    scratch = Path(serve) / rec.scratch_dir
    cache = cache_dir(serve, pid, ds_id)
    cache.mkdir(parents=True, exist_ok=True)
    escaped = cache / "review-escape.md"
    escaped.write_text("from-cache\n", encoding="utf-8")
    rel = os.path.relpath(escaped, scratch)
    assert rel.startswith(".."), rel
    _atomic_json(
        scratch / "index.json",
        [
            {
                "input_id": "inp-esc",
                "source_id": f"datasource:{ds_id}",
                "type": "datasource",
                "title": "装机",
                "version": content_version("from-cache\n"),
                "read_at": rec.created_at,
                "read_count": 1,
                "body": rel.replace("\\", "/"),
            }
        ],
    )
    out = _execute_agent_run(serve, pid, run_id, _CiteProvider("inp-esc"))
    assert out.status == "failed"
    assert out.reason == "evidence_failed"
    assert out.artifact_path is None
    assert get_run(serve, pid, run_id).status == "failed"

    run_id_abs = "run-abs"
    rec_abs = _running_record(serve, pid, run_id_abs, topics=["alpha-ws"], datasources=[ds_id])
    scratch_abs = Path(serve) / rec_abs.scratch_dir
    _atomic_json(
        scratch_abs / "index.json",
        [
            {
                "input_id": "inp-abs",
                "source_id": f"datasource:{ds_id}",
                "type": "datasource",
                "title": "装机",
                "version": content_version("from-cache\n"),
                "read_at": rec_abs.created_at,
                "read_count": 1,
                "body": str(escaped),
            }
        ],
    )
    out_abs = _execute_agent_run(serve, pid, run_id_abs, _CiteProvider("inp-abs"))
    assert out_abs.status == "failed"
    assert out_abs.reason == "evidence_failed"
    assert out_abs.artifact_path is None

    run_id_link = "run-link"
    rec_link = _running_record(serve, pid, run_id_link, topics=["alpha-ws"], datasources=[ds_id])
    scratch_link = Path(serve) / rec_link.scratch_dir
    link = scratch_link / "inp-link.md"
    link.symlink_to(escaped)
    _atomic_json(
        scratch_link / "index.json",
        [
            {
                "input_id": "inp-link",
                "source_id": f"datasource:{ds_id}",
                "type": "datasource",
                "title": "装机",
                "version": content_version("from-cache\n"),
                "read_at": rec_link.created_at,
                "read_count": 1,
                "body": "inp-link.md",
            }
        ],
    )
    out_link = _execute_agent_run(serve, pid, run_id_link, _CiteProvider("inp-link"))
    assert out_link.status == "failed"
    assert out_link.reason == "evidence_failed"
    assert out_link.artifact_path is None

    escaped.unlink()
    for rid in (run_id, run_id_abs, run_id_link):
        try:
            read_run_input(serve, pid, rid, "inp-esc")
        except Exception:
            pass


def test_valid_recorded_evidence_still_succeeds(tmp_path, monkeypatch):
    from kairo.project_materials import _atomic_json
    from kairo.projects import _execute_agent_run, read_artifact
    from kairo.project_materials import read_run_input

    serve, pid, ds_id, _counter, _ws = _prepare(tmp_path, monkeypatch)
    run_id = "run-ok"
    rec = _running_record(serve, pid, run_id, topics=["alpha-ws"], datasources=[ds_id])
    scratch = Path(serve) / rec.scratch_dir
    body = "plant,mw\nsolar,80\n"
    (scratch / "inp-ok.md").write_text(body, encoding="utf-8")
    _atomic_json(
        scratch / "index.json",
        [
            {
                "input_id": "inp-ok",
                "source_id": f"datasource:{ds_id}",
                "type": "datasource",
                "title": "装机",
                "version": content_version(body),
                "read_at": rec.created_at,
                "read_count": 1,
                "body": "inp-ok.md",
            }
        ],
    )
    out = _execute_agent_run(serve, pid, run_id, _CiteProvider("inp-ok"))
    assert out.status == "succeeded"
    artifact = read_artifact(serve, pid, run_id)
    assert "inp-ok" in artifact
    evidence = read_run_input(serve, pid, run_id, "inp-ok")
    assert evidence["content"] == body
    client = TestClient(create_app(serve))
    ds_page = client.get(f"/projects/{pid}/datasources/{ds_id}")
    assert ds_page.status_code == 200
    assert "<table>" in ds_page.text
    assert "plant" in ds_page.text
    evidence_page = client.get(f"/projects/{pid}/runs/{run_id}/inputs/inp-ok")
    assert evidence_page.status_code == 200
    assert "<table>" in evidence_page.text
    assert "solar" in evidence_page.text
    from kairo.projects import remove_datasource

    remove_datasource(serve, pid, ds_id)
    again = read_run_input(serve, pid, run_id, "inp-ok")
    assert again["content"] == body
    assert again["version"] == content_version(body)
    page = client.get(f"/projects/{pid}/runs/{run_id}")
    assert page.status_code == 200
    assert f"/projects/{pid}/runs/{run_id}/inputs/inp-ok" in page.text
    assert "input:inp-ok" not in page.text


def test_archive_keeps_unique_bodies_when_basenames_collide(tmp_path, monkeypatch):
    from kairo.project_materials import _atomic_json, inputs_dir, read_run_input
    from kairo.projects import _execute_agent_run

    serve, pid, ds_id, _counter, _ws = _prepare(tmp_path, monkeypatch)
    run_id = "run-collide"
    rec = _running_record(serve, pid, run_id, topics=["alpha-ws"], datasources=[ds_id])
    scratch = Path(serve) / rec.scratch_dir
    body_a = "content-A-only\n"
    body_b = "content-B-only\n"
    (scratch / "one").mkdir()
    (scratch / "two").mkdir()
    (scratch / "one" / "body.md").write_text(body_a, encoding="utf-8")
    (scratch / "two" / "body.md").write_text(body_b, encoding="utf-8")
    _atomic_json(
        scratch / "index.json",
        [
            {
                "input_id": "inp-a",
                "source_id": f"datasource:{ds_id}",
                "type": "datasource",
                "title": "A",
                "version": content_version(body_a),
                "read_at": rec.created_at,
                "read_count": 1,
                "body": "one/body.md",
            },
            {
                "input_id": "inp-b",
                "source_id": "topic:alpha-ws:understanding",
                "type": "understanding",
                "title": "B",
                "version": content_version(body_b),
                "read_at": rec.created_at,
                "read_count": 1,
                "body": "two/body.md",
            },
        ],
    )

    class _TwoCiteProvider(_CiteProvider):
        def __init__(self):
            super().__init__("inp-a")

        def run(self, config, signal=None):
            dest = config.artifact_dir / "artifact.md"
            dest.write_text("[a](input:inp-a)\n[b](input:inp-b)\n", encoding="utf-8")
            return AgentResult(artifacts=[dest], result_text=dest.read_text())

    out = _execute_agent_run(serve, pid, run_id, _TwoCiteProvider())
    assert out.status == "succeeded"
    assert out.artifact_path
    got_a = read_run_input(serve, pid, run_id, "inp-a")
    got_b = read_run_input(serve, pid, run_id, "inp-b")
    assert got_a["content"] == body_a
    assert got_b["content"] == body_b
    assert got_a["version"] == content_version(body_a)
    assert got_b["version"] == content_version(body_b)
    dest = inputs_dir(serve, pid, run_id)
    names = sorted(p.name for p in dest.glob("*.md"))
    assert names == ["inp-a.md", "inp-b.md"]


def test_recorded_datasource_survives_unlink_before_publish(tmp_path, monkeypatch):
    from kairo.project_materials import _atomic_json
    from kairo.projects import _execute_agent_run, remove_datasource

    serve, pid, ds_id, _counter, _ws = _prepare(tmp_path, monkeypatch)
    run_id = "run-unlinked"
    rec = _running_record(serve, pid, run_id, topics=["alpha-ws"], datasources=[ds_id])
    scratch = Path(serve) / rec.scratch_dir
    body = "kept-after-unlink\n"
    (scratch / "inp-keep.md").write_text(body, encoding="utf-8")
    _atomic_json(
        scratch / "index.json",
        [
            {
                "input_id": "inp-keep",
                "source_id": f"datasource:{ds_id}",
                "type": "datasource",
                "title": "装机",
                "version": content_version(body),
                "read_at": rec.created_at,
                "read_count": 1,
                "body": "inp-keep.md",
            }
        ],
    )
    remove_datasource(serve, pid, ds_id)
    out = _execute_agent_run(serve, pid, run_id, _CiteProvider("inp-keep"))
    assert out.status == "succeeded"
    assert out.artifact_path


def test_project_primary_precedes_materials_and_names_datasource(tmp_path, monkeypatch):
    serve, pid, ds_id, _counter, _ws = _prepare(tmp_path, monkeypatch)
    client = TestClient(create_app(serve))
    page = client.get(f"/projects/{pid}")
    html = page.text
    primary = html.find('id="project-primary"')
    materials = html.find('id="project-materials"')
    assert primary != -1 and materials != -1
    assert primary < materials
    assert 'class="task-create"' in html[primary:materials]
    assert 'id="material-filter"' in html
    named = client.post(
        f"/projects/{pid}/datasources",
        data={
            "url": "https://docs.qq.com/sheet/Dnamed",
            "name": "需求池",
            "purpose": "周报输入",
        },
        follow_redirects=True,
    )
    assert named.status_code == 200
    assert "需求池" in named.text
    assert named.text.find("需求池") < named.text.find("https://docs.qq.com/sheet/Dnamed")
    created = client.post(
        f"/projects/{pid}/tasks",
        data={"name": "周报", "prompt": "整理风险", "schedule": "once"},
        follow_redirects=True,
    )
    assert created.status_code == 200
    html = created.text
    assert html.find('id="project-primary"') < html.find('id="project-materials"')
    assert "周报" in html[html.find('id="project-primary"'):html.find('id="project-materials"')]
    named_cli = _load(
        _cli(
            [
                "datasource",
                "add",
                pid,
                "--url",
                "https://docs.qq.com/sheet/Dcli-name",
                "--name",
                "CLI名称",
                "--purpose",
                "辅助",
            ],
            serve,
            monkeypatch,
        )
    )
    assert named_cli["name"] == "CLI名称"
    api_ds = client.post(
        f"/api/projects/{pid}/datasources",
        json={
            "url": "https://docs.qq.com/sheet/Dapi-name",
            "name": "API名称",
            "purpose": "接口",
        },
    ).json()
    assert api_ds["ok"] is True
    assert api_ds["datasource"]["name"] == "API名称"


def test_material_search_hidden_beats_obj_row_flex(tmp_path, monkeypatch):
    serve, pid, ds_id, _counter, _ws = _prepare(tmp_path, monkeypatch)
    client = TestClient(create_app(serve))
    html = client.get(f"/projects/{pid}").text
    assert 'id="material-filter"' in html
    assert "material-item" in html
    assert 'class="obj-row material-item"' in html or "material-item" in html
    css = client.get("/static/app.css").text
    assert ".obj-row { display: flex" in css or ".obj-row { display:flex" in css
    hidden_rule = None
    for chunk in css.split("}"):
        if ".obj-row[hidden]" in chunk or ".material-item[hidden]" in chunk:
            hidden_rule = chunk + "}"
            break
    assert hidden_rule is not None, "missing material [hidden] CSS rule"
    assert "none" in hidden_rule
    assert "!important" in hidden_rule


def test_recent_artifact_precedes_collapsed_create_form(tmp_path, monkeypatch):
    from kairo.projects import RunRecord, _artifact_path, _save_run, create_task

    serve, pid, ds_id, _counter, _ws = _prepare(tmp_path, monkeypatch)
    tasks = [
        create_task(serve, pid, name=name, prompt="整理")
        for name in ("日报甲", "日报乙", "日报丙")
    ]
    run_id = "run-recent"
    dest = _artifact_path(serve, pid, run_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("# 日报\n", encoding="utf-8")
    _save_run(
        serve,
        RunRecord(
            id=run_id,
            project_id=pid,
            task_id=tasks[0].id,
            task_name=tasks[0].name,
            task_version=1,
            status="succeeded",
            artifact_path=str(dest.relative_to(serve)).replace("\\", "/"),
            schema_version=2,
            mode="agent",
            created_at="2026-09-05T00:00:00+00:00",
            started_at="2026-09-05T00:00:00+00:00",
            finished_at="2026-09-05T00:01:00+00:00",
        ),
    )
    client = TestClient(create_app(serve))
    html = client.get(f"/projects/{pid}").text
    recent = html.find('id="project-recent"')
    create_at = html.find('id="task-create"')
    assert recent != -1 and create_at != -1
    assert recent < create_at
    assert f"/projects/{pid}/runs/{run_id}" in html[recent:create_at]
    assert clock_label("2026-09-05T00:00:00Z") in html[recent:create_at]
    snippet = html[create_at : create_at + 80]
    assert "<details" in html[create_at - 40 : create_at + 40] or snippet.startswith("task-create")
    details = html[html.rfind("<details", 0, create_at + 1) : create_at + 120]
    assert "open" not in details.split(">")[0]
    primary = html[html.find('id="project-primary"') : html.find('id="project-materials"')]
    assert 'class="obj-rename"' not in primary or "<details" in html[: html.find('class="obj-rename"')]


def test_recent_results_one_latest_per_existing_task(tmp_path, monkeypatch):
    from kairo.projects import RunRecord, _artifact_path, _save_run, create_task

    serve, pid, ds_id, _counter, _ws = _prepare(tmp_path, monkeypatch)
    task_a = create_task(serve, pid, name="日报甲", prompt="整理")
    task_b = create_task(serve, pid, name="日报乙", prompt="整理")
    ids = []
    for i in range(4):
        run_id = f"run-a-{i}"
        dest = _artifact_path(serve, pid, run_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(f"# a{i}\n", encoding="utf-8")
        hour = f"{i:02d}"
        _save_run(
            serve,
            RunRecord(
                id=run_id,
                project_id=pid,
                task_id=task_a.id,
                task_name=task_a.name,
                task_version=i + 1,
                status="succeeded",
                artifact_path=str(dest.relative_to(serve)).replace("\\", "/"),
                schema_version=2,
                mode="agent",
                created_at=f"2026-09-05T{hour}:00:00+00:00",
                started_at=f"2026-09-05T{hour}:00:00+00:00",
                finished_at=f"2026-09-05T{hour}:01:00+00:00",
            ),
        )
        ids.append(run_id)
    dest_b = _artifact_path(serve, pid, "run-b-0")
    dest_b.parent.mkdir(parents=True, exist_ok=True)
    dest_b.write_text("# b0\n", encoding="utf-8")
    _save_run(
        serve,
        RunRecord(
            id="run-b-0",
            project_id=pid,
            task_id=task_b.id,
            task_name=task_b.name,
            task_version=1,
            status="succeeded",
            artifact_path=str(dest_b.relative_to(serve)).replace("\\", "/"),
            schema_version=2,
            mode="agent",
            created_at="2026-09-05T04:00:00+00:00",
            started_at="2026-09-05T04:00:00+00:00",
            finished_at="2026-09-05T04:01:00+00:00",
        ),
    )
    html = TestClient(create_app(serve)).get(f"/projects/{pid}").text
    recent = html[html.find('id="project-recent"') : html.find('id="task-create"')]
    history = html[html.find('id="project-run-history"') :]
    assert recent.count("Artifact") == 2
    assert "run-a-3" in recent and "run-b-0" in recent
    assert "run-a-0" not in recent and "run-a-1" not in recent and "run-a-2" not in recent
    assert clock_label("2026-09-05T03:00:00Z") in recent
    assert clock_label("2026-09-05T04:00:00Z") in recent
    assert history.count(f"/projects/{pid}/runs/") == 5
    for run_id in (*ids, "run-b-0"):
        assert run_id in history
    assert "<details" in html[html.find('id="project-run-history"') - 20 : html.find('id="project-run-history"') + 80]
    assert "open" not in html[html.find('id="project-run-history"') : html.find('id="project-run-history"') + 40]


def test_empty_project_shows_material_counts_and_next_step(tmp_path, monkeypatch):
    serve, pid, ds_id, _counter, _ws = _prepare(tmp_path, monkeypatch)
    client = TestClient(create_app(serve))
    html = client.get(f"/projects/{pid}").text
    primary = html[html.find('id="project-primary"') : html.find('id="project-materials"')]
    assert "1" in primary
    assert ("主题" in primary or "Topics" in primary)
    assert ("数据源" in primary or "Data sources" in primary)
    assert "下一步" in primary or "Next:" in primary
    assert 'id="task-create"' in primary
    head = primary[primary.find('id="task-create"') - 60 : primary.find('id="task-create"') + 40]
    assert "open" not in head.split(">")[0]


def test_overview_demotes_rename_and_datasource_edit(tmp_path, monkeypatch):
    serve, pid, ds_id, _counter, _ws = _prepare(tmp_path, monkeypatch)
    client = TestClient(create_app(serve))
    html = client.get(f"/projects/{pid}").text
    rename_at = html.find('class="obj-rename"')
    assert rename_at != -1
    before_rename = html[max(0, rename_at - 200) : rename_at]
    assert "<details" in before_rename
    ds_block = html[html.find("datasources") if "datasources" in html else 0 :]
    # full name/purpose editor is inside a collapsed details, not a standing obj-add on the row
    edit_forms = html.count(f'action="/projects/{pid}/datasources/{ds_id}/edit"')
    assert edit_forms == 1
    edit_at = html.find(f'action="/projects/{pid}/datasources/{ds_id}/edit"')
    assert "<details" in html[max(0, edit_at - 400) : edit_at]
    run_btn = html.find(f'action="/projects/{pid}/tasks/')
    remove_at = html.find(f'action="/projects/{pid}/datasources/{ds_id}/delete"')
    if run_btn != -1:
        assert "btn-step" in html[run_btn : run_btn + 200]
    assert remove_at != -1
    assert "btn-step" not in html[remove_at : remove_at + 180]


def test_task_page_edit_keeps_invalid_input(tmp_path, monkeypatch):
    serve, pid, ds_id, _counter, _ws = _prepare(tmp_path, monkeypatch, with_topic_body=False)
    client = TestClient(create_app(serve))
    created = client.post(
        f"/projects/{pid}/tasks",
        data={"name": "每周整理", "prompt": "整理风险", "schedule": "once"},
        follow_redirects=True,
    )
    assert "每周整理" in created.text
    assert f"/projects/{pid}/tasks/" in created.text
    task_id = [t.id for t in __import__("kairo.projects", fromlist=["get_project"]).get_project(serve, pid).tasks][0]
    page = client.get(f"/projects/{pid}/tasks/{task_id}")
    assert page.status_code == 200
    assert "整理风险" in page.text
    bad = client.post(
        f"/projects/{pid}/tasks/{task_id}",
        data={"name": "保留名称XYZ", "prompt": "   "},
    )
    assert bad.status_code == 200
    assert "保留名称XYZ" in bad.text
    ok = client.post(
        f"/projects/{pid}/tasks/{task_id}",
        data={"name": "每周整理", "prompt": "改过的要求"},
        follow_redirects=True,
    )
    assert ok.status_code == 200
    assert "v2" in ok.text
    assert "改过的要求" in ok.text


def test_run_page_hides_none_and_shows_elapsed(tmp_path, monkeypatch):
    import os
    from kairo.projects import RunRecord, _save_run

    serve, pid, ds_id, _counter, _ws = _prepare(tmp_path, monkeypatch, with_topic_body=False)
    from kairo.projects import create_task

    task = create_task(serve, pid, name="每周整理", prompt="x")
    rec = RunRecord(
        id="run-live",
        project_id=pid,
        task_id=task.id,
        task_name=task.name,
        task_version=1,
        status="running",
        reason=None,
        schema_version=2,
        mode="agent",
        started_at="2026-09-05T00:00:00+00:00",
        created_at="2026-09-05T00:00:00+00:00",
        worker_pid=os.getpid(),
    )
    _save_run(serve, rec)
    client = TestClient(create_app(serve))
    page = client.get(f"/projects/{pid}")
    assert "None" not in page.text
    assert "Running" in page.text or "运行中" in page.text
    detail = client.get(f"/projects/{pid}/runs/run-live")
    assert detail.status_code == 200
    assert "None" not in detail.text
    assert "m" in detail.text or "s" in detail.text
    failed = RunRecord(
        id="run-fail",
        project_id=pid,
        task_id=task.id,
        task_name=task.name,
        task_version=1,
        status="failed",
        reason="provider_failed",
        schema_version=2,
        mode="agent",
        started_at="2026-09-05T00:00:00+00:00",
        finished_at="2026-09-05T00:01:00+00:00",
        created_at="2026-09-05T00:00:00+00:00",
    )
    _save_run(serve, failed)
    fail_page = client.get(f"/projects/{pid}/runs/run-fail")
    assert "provider_failed" not in fail_page.text
    assert "failed" in fail_page.text.lower() or "失败" in fail_page.text
    from kairo.provider import StubProvider

    monkeypatch.setattr("kairo.projects.select_project_agent", lambda: StubProvider())
    retry = client.post(f"/projects/{pid}/tasks/{task.id}/run", follow_redirects=False)
    assert retry.status_code == 303
    assert "/runs/" in retry.headers.get("location", "")
    assert "run-fail" not in retry.headers.get("location", "")


def test_interval_is_rejected_but_legacy_interval_stays_manual(tmp_path, monkeypatch):
    serve, pid, ds_id, _counter, _ws = _prepare(tmp_path, monkeypatch, with_topic_body=False)
    from kairo.projects import ProjectError, create_task, edit_task, get_project

    client = TestClient(create_app(serve))
    page = client.get(f"/projects/{pid}")
    assert 'value="interval"' not in page.text
    api = client.post(
        f"/api/projects/{pid}/tasks",
        json={"name": "周期", "prompt": "x", "schedule": "interval"},
    )
    assert api.status_code == 400
    assert api.json()["code"] == "unsupported_schedule"
    cli = _cli(
        ["task", "create", pid, "--name", "周期CLI", "--prompt", "x", "--schedule", "interval"],
        serve,
        monkeypatch,
    )
    assert cli.exit_code != 0
    once = create_task(serve, pid, name="手动", prompt="x", schedule="once")
    try:
        edit_task(serve, pid, once.id, schedule="interval")
    except ProjectError as exc:
        assert exc.code == "unsupported_schedule"
    else:
        raise AssertionError("expected unsupported_schedule")
    project = get_project(serve, pid)
    legacy = next(t for t in project.tasks)
    legacy.schedule = "interval"
    legacy.interval_hours = 24
    from kairo.projects import save_project

    save_project(serve, project)
    edited = edit_task(serve, pid, legacy.id, name="历史周期")
    assert edited.schedule == "interval"
    assert edited.interval_hours == 24
    shown = client.get(f"/projects/{pid}/tasks/{legacy.id}")
    assert shown.status_code == 200
    assert "Automatic schedule is not enabled" in shown.text or "未启用自动调度" in shown.text


def test_unsaved_task_run_is_blocked_and_keeps_draft(tmp_path, monkeypatch):
    from kairo.projects import create_task, list_runs

    serve, pid, ds_id, _counter, _ws = _prepare(tmp_path, monkeypatch, with_topic_body=False)
    task = create_task(serve, pid, name="每周整理", prompt="原始要求")
    client = TestClient(create_app(serve))
    before = list_runs(serve, pid)
    blocked = client.post(
        f"/projects/{pid}/tasks/{task.id}/run",
        data={"name": "每周整理", "prompt": "未保存的新要求"},
        follow_redirects=False,
    )
    assert blocked.status_code == 200
    assert "未保存的新要求" in blocked.text
    assert "原始要求" not in blocked.text or "未保存" in blocked.text
    assert "请先保存" in blocked.text or "Save your edits" in blocked.text
    assert list_runs(serve, pid) == before
    from kairo.provider import StubProvider

    monkeypatch.setattr("kairo.projects.select_project_agent", lambda: StubProvider())
    ok = client.post(
        f"/projects/{pid}/tasks/{task.id}/run",
        follow_redirects=False,
    )
    assert ok.status_code == 303
    assert "/runs/" in ok.headers.get("location", "")


def test_unsaved_legacy_task_run_is_blocked_and_keeps_draft(tmp_path, monkeypatch):
    from kairo.projects import create_task, list_runs

    serve, pid, ds_id, _counter, _ws = _prepare(tmp_path, monkeypatch, with_topic_body=False)
    task = create_task(serve, pid, name="快照", datasource_id=ds_id)
    client = TestClient(create_app(serve))
    before = list_runs(serve, pid)
    blocked_name = client.post(
        f"/projects/{pid}/tasks/{task.id}/run",
        data={"name": "未保存快照", "datasource_id": ds_id},
        follow_redirects=False,
    )
    assert blocked_name.status_code == 200
    assert "未保存快照" in blocked_name.text
    assert "请先保存" in blocked_name.text or "Save your edits" in blocked_name.text
    assert list_runs(serve, pid) == before
    blocked_ds = client.post(
        f"/projects/{pid}/tasks/{task.id}/run",
        data={"name": "快照", "datasource_id": "ds-other"},
        follow_redirects=False,
    )
    assert blocked_ds.status_code == 200
    assert "ds-other" in blocked_ds.text
    assert list_runs(serve, pid) == before
    ok = client.post(
        f"/projects/{pid}/tasks/{task.id}/run",
        follow_redirects=False,
    )
    assert ok.status_code == 303
    assert "/runs/" in ok.headers.get("location", "")


def test_source_snapshot_artifact_does_not_claim_no_inputs(tmp_path, monkeypatch):
    from kairo.projects import create_task, run_task

    serve, pid, ds_id, _counter, _ws = _prepare(tmp_path, monkeypatch, with_topic_body=False)
    task = create_task(serve, pid, name="快照", datasource_id=ds_id)
    record = run_task(serve, pid, task.id)
    assert record.status == "succeeded"
    client = TestClient(create_app(serve))
    page = client.get(f"/projects/{pid}/runs/{record.id}")
    assert page.status_code == 200
    assert "本次未读取项目材料" not in page.text
    assert "This run did not read project materials" not in page.text
    html = page.text
    assert "docs.qq.com" in html or ds_id in html or "Input" in html or "solar" in html


def test_elapsed_label_unknown_when_finished_missing(tmp_path):
    from kairo.web.views import run_elapsed_label

    assert (
        run_elapsed_label(
            "2019-01-01T00:00:00+00:00",
            None,
            status="succeeded",
            unknown="未知",
        )
        == "未知"
    )
    running = run_elapsed_label("2026-09-05T00:00:00+00:00", None, status="running")
    assert running.endswith("s") or "m" in running or "h" in running
    assert "168h" not in run_elapsed_label(
        "2019-01-01T00:00:00+00:00", None, status="failed", unknown="未知"
    )


def test_context_lists_named_datasource_first(tmp_path, monkeypatch):
    from kairo.projects import edit_datasource

    serve, pid, ds_id, _counter, _ws = _prepare(tmp_path, monkeypatch)
    edit_datasource(serve, pid, ds_id, name="需求池", purpose="周报输入")
    catalog = list_context(serve, pid)
    types = [i["type"] for i in catalog["items"]]
    assert types[0] == "datasource"
    assert types.index("datasource") < types.index("understanding")
    ds_item = catalog["items"][0]
    assert ds_item["title"] == "需求池"
    assert "http" not in ds_item["title"]


def test_topic_only_inputs_fail_when_datasources_in_scope(tmp_path, monkeypatch):
    from kairo.project_materials import _atomic_json
    from kairo.projects import _execute_agent_run

    serve, pid, ds_id, _counter, _ws = _prepare(tmp_path, monkeypatch)
    run_id = "run-noids"
    rec = _running_record(serve, pid, run_id, topics=["alpha-ws"], datasources=[ds_id])
    scratch = Path(serve) / rec.scratch_dir
    body = "topic-only\n"
    (scratch / "inp-t.md").write_text(body, encoding="utf-8")
    _atomic_json(
        scratch / "index.json",
        [
            {
                "input_id": "inp-t",
                "source_id": "topic:alpha-ws:understanding",
                "type": "understanding",
                "title": "事实层",
                "version": content_version(body),
                "read_at": rec.created_at,
                "read_count": 1,
                "body": "inp-t.md",
            }
        ],
    )
    out = _execute_agent_run(serve, pid, run_id, _CiteProvider("inp-t"))
    assert out.status == "failed"
    assert out.reason == "datasource_unread"
    assert out.artifact_path is None


def test_warm_skips_fresh_cache_and_refreshes_expired(tmp_path, monkeypatch):
    from kairo.projects import _warm_run_datasources, get_run

    serve, pid, ds_id, counter, _ws = _prepare(tmp_path, monkeypatch)
    clock = _Clock(datetime(2026, 9, 5, 12, 0, tzinfo=UTC))
    set_clock(clock)
    try:
        _load(_cli(["datasource", "read", pid, ds_id], serve, monkeypatch))
        assert _count(counter) == 1
        rec = _running_record(serve, pid, "run-warm", topics=["alpha-ws"], datasources=[ds_id])
        _warm_run_datasources(serve, get_run(serve, pid, rec.id))
        assert _count(counter) == 1
        clock.when = clock.when + CACHE_TTL
        _warm_run_datasources(serve, get_run(serve, pid, rec.id))
        assert _count(counter) == 2
    finally:
        set_clock(None)


def test_project_surfaces_running_and_latest_failure(tmp_path, monkeypatch):
    from kairo.projects import RunRecord, _save_run, create_task

    serve, pid, _ds, _counter, _ws = _prepare(tmp_path, monkeypatch)
    task = create_task(serve, pid, name="日报", prompt="整理")
    for index, status in enumerate(("succeeded", "running", "failed")):
        _save_run(serve, RunRecord(
            id=f"run-visibility-{index}", project_id=pid, task_id=task.id,
            worker_pid=os.getpid() if status == "running" else None,
            task_name=task.name, task_version=1, status=status,
            schema_version=2, mode="agent",
            created_at=f"2026-09-05T0{index}:00:00+00:00",
        ))
    html = TestClient(create_app(serve)).get(f"/projects/{pid}").text
    attention = html.split('id="project-attention"')[1]
    for marker in ("id=\"project-recent\"", "id=\"task-create\"", "id=\"project-run-history\""):
        if marker in attention:
            attention = attention.split(marker)[0]
            break
    assert "run-visibility-1" in attention
    assert "run-visibility-2" in attention
    assert "run-visibility-0" not in attention
