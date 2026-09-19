"""#392 S1/S2: project read-url CLI, type=url inputs, Skill/prompt. No web/."""

from __future__ import annotations

import inspect
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from kairo.cli import app
from kairo.project_materials import (
    content_version,
    finalize_inputs,
    load_run_inputs,
    parse_source_id,
    scratch_dir,
)
from kairo.projects import DataSource, get_project, save_project
from kairo.provider import AgentResult

runner = CliRunner()

WECOM_KINDS = (
    ("https://doc.weixin.qq.com/doc/e3_DocExample", "document"),
    ("https://doc.weixin.qq.com/sheet/e3_SheetExample", "spreadsheet"),
    ("https://doc.weixin.qq.com/smartsheet/s3_SmartExample", "smartsheet"),
    ("https://doc.weixin.qq.com/smartpage/a1_PageExample", "smartpage"),
)
SHEET_URL = WECOM_KINDS[1][0]
MODAO_URL = "https://modao.cc/app/energy-prototype"
MAIL_URL = "mail://wecom/inbox?keywords=energy"
IMAP_URL = "imap://alice@imap.example.com/INBOX?keywords=energy"
SUCCESS_KEYS = {
    "ok",
    "input_id",
    "title",
    "source_id",
    "type",
    "url",
    "reader",
    "kind",
    "content",
    "version",
    "fetched_at",
    "numbered_content",
    "line_count",
}


def seed_existing_datasource(
    serve,
    project_id: str,
    *,
    url: str,
    reader: str,
    kind: str,
    connection_id: str | None = None,
    purpose: str = "",
    name: str = "",
    ds_id: str | None = None,
):
    project = get_project(serve, project_id)
    ds = DataSource(
        id=ds_id or f"ds-seed-{len(project.datasources):04d}",
        connection_id=connection_id or reader,
        url=url,
        kind=kind,
        purpose=purpose,
        name=name,
        reader=reader,
    )
    project.datasources.append(ds)
    save_project(serve, project)
    return ds


def _stub_cmd(path: Path, body: str) -> str:
    path.write_text(f"import sys\nsys.stdout.write({body!r})\n", encoding="utf-8")
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(path))} {{url}}"


def _cli(args, cwd: Path, monkeypatch):
    monkeypatch.chdir(cwd)
    return runner.invoke(app, args)


def _load(result):
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _fail(result):
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload.get("ok") is False
    assert "input_id" not in payload
    return payload


def _running_record(serve, pid, run_id, *, topics=None, datasources=None):
    from kairo.projects import RunRecord, _save_run

    folder = scratch_dir(serve, pid, run_id)
    folder.mkdir(parents=True, exist_ok=True)
    rec = RunRecord(
        id=run_id,
        project_id=pid,
        task_id="tsk-read-url",
        task_name="跟读",
        task_version=1,
        status="running",
        schema_version=2,
        mode="agent",
        task_snapshot={"prompt": "x"},
        scope_topics=list(topics or []),
        scope_datasources=list(datasources) if datasources is not None else [],
        scratch_dir=str(folder.relative_to(serve)).replace("\\", "/"),
        created_at="2026-09-16T00:00:00+00:00",
        started_at="2026-09-16T00:00:00+00:00",
    )
    return _save_run(serve, rec)


def _prepare(tmp_path, monkeypatch, *, body="# 能源周报\n\nsheet-body\n"):
    serve = tmp_path / "root"
    serve.mkdir()
    monkeypatch.chdir(serve)
    ok_cmd = _stub_cmd(tmp_path / "wecom-ok.py", body)
    _load(_cli(["settings", "set", "connections.wecom.authorized", "true"], serve, monkeypatch))
    _load(_cli(["settings", "set", "connections.wecom.cmd", ok_cmd], serve, monkeypatch))
    created = _load(_cli(["project", "create", "能源跟读"], serve, monkeypatch))
    pid = created["id"]
    ds = seed_existing_datasource(
        serve,
        pid,
        url="https://www.notion.so/" + "a" * 32,
        reader="notion",
        kind="page",
        name="能源",
    )
    return serve, pid, ds


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


class FollowUrlProvider:
    """Deterministic S2 fixture: read registered DS, then follow a WeCom URL."""

    name = "follow-url"
    model = "test"
    supports_read_dirs = True
    supports_project_cli = True

    def run(self, config, signal=None):
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
        registered = kairo("project", "read", pid, ds["source_id"], "--run", rid, "--root", serve)
        followed = kairo(
            "project", "read-url", pid, "--run", rid, "--root", serve, SHEET_URL
        )
        assert followed["type"] == "url"
        body = (
            f"# 跟读\n\n"
            f"[{ds['title']}](input:{registered['input_id']})\n\n"
            f"[{followed['title']}](input:{followed['input_id']})\n"
        )
        dest = config.artifact_dir / "artifact.md"
        dest.write_text(body, encoding="utf-8")
        return AgentResult(artifacts=[dest], result_text=body)


def test_parse_source_id_accepts_url_materials():
    parsed = parse_source_id(f"url:{SHEET_URL}")
    assert parsed["kind"] == "url"
    assert parsed["url"] == SHEET_URL
    try:
        parse_source_id("url:mail://wecom/inbox?keywords=x")
        raise AssertionError("mail source_id must not parse as url material")
    except Exception as exc:
        assert getattr(exc, "code", None) == "not_found"


def test_cli_wecom_read_url_success_and_datasources_unchanged(tmp_path, monkeypatch):
    serve, pid, ds = _prepare(tmp_path, monkeypatch)
    run_id = "run-s1"
    _running_record(serve, pid, run_id, datasources=[ds.id])
    before = [item.id for item in get_project(serve, pid).datasources]
    before_cache = {
        p.name
        for p in (serve / ".kairo" / "projects" / pid / "cache").glob("*")
        if p.is_dir()
    }
    result = _load(
        _cli(
            ["project", "read-url", pid, "--run", run_id, "--root", str(serve), SHEET_URL],
            serve,
            monkeypatch,
        )
    )
    assert SUCCESS_KEYS <= set(result)
    assert result["ok"] is True
    assert result["type"] == "url"
    assert result["url"] == SHEET_URL
    assert result["source_id"] == f"url:{SHEET_URL}"
    assert result["reader"] == "wecom"
    assert result["kind"] == "spreadsheet"
    assert result["title"] == "能源周报"
    assert "sheet-body" in result["content"]
    assert result["input_id"].startswith("inp-")
    assert result["version"] == content_version(result["content"])
    assert result["numbered_content"].startswith("1: ")
    assert result["line_count"] >= 1
    assert result["input_id"]
    after = [item.id for item in get_project(serve, pid).datasources]
    assert after == before
    after_cache = {
        p.name
        for p in (serve / ".kairo" / "projects" / pid / "cache").glob("*")
        if p.is_dir()
    }
    assert after_cache == before_cache
    scratch = load_run_inputs(serve, pid, run_id, scratch=True)
    assert len(scratch) == 1
    assert scratch[0]["type"] == "url"
    assert scratch[0]["url"] == SHEET_URL
    assert scratch[0]["input_id"] == result["input_id"]

    again = _load(
        _cli(
            ["project", "read-url", pid, "--run", run_id, "--root", str(serve), f"  {SHEET_URL}  "],
            serve,
            monkeypatch,
        )
    )
    assert again["input_id"] == result["input_id"]
    scratch = load_run_inputs(serve, pid, run_id, scratch=True)
    assert len(scratch) == 1
    assert scratch[0]["read_count"] == 2


def test_cli_wecom_four_kinds_live_false_is_not_unsupported(tmp_path, monkeypatch):
    serve, pid, ds = _prepare(tmp_path, monkeypatch)
    run_id = "run-kinds"
    _running_record(serve, pid, run_id, datasources=[ds.id])
    for url, kind in WECOM_KINDS:
        payload = _load(
            _cli(
                ["project", "read-url", pid, "--run", run_id, "--root", str(serve), url],
                serve,
                monkeypatch,
            )
        )
        assert payload["ok"] is True
        assert payload["kind"] == kind
        assert payload["type"] == "url"
        assert payload["reader"] == "wecom"


def test_finalize_inputs_accepts_type_url(tmp_path, monkeypatch):
    serve, pid, _ds = _prepare(tmp_path, monkeypatch)
    run_id = "run-final"
    _running_record(serve, pid, run_id, datasources=[])
    payload = _load(
        _cli(
            ["project", "read-url", pid, "--run", run_id, "--root", str(serve), SHEET_URL],
            serve,
            monkeypatch,
        )
    )
    items = finalize_inputs(serve, pid, run_id)
    assert len(items) == 1
    assert items[0]["type"] == "url"
    assert items[0]["source_id"] == f"url:{SHEET_URL}"
    assert items[0]["input_id"] == payload["input_id"]
    archived = load_run_inputs(serve, pid, run_id)
    assert archived[0]["type"] == "url"
    assert archived[0]["url"] == SHEET_URL


def test_mail_and_modao_rejected_without_scratch_body(tmp_path, monkeypatch):
    serve, pid, ds = _prepare(tmp_path, monkeypatch)
    run_id = "run-reject"
    rec = _running_record(serve, pid, run_id, datasources=[ds.id])
    scratch = Path(serve) / rec.scratch_dir
    for url, code in (
        (MAIL_URL, "unsupported_reader"),
        (IMAP_URL, "unsupported_reader"),
        (MODAO_URL, "invalid_link"),
        ("https://www.showdoc.com.cn/energy", "invalid_link"),
        ("file:///tmp/note.md", "invalid_link"),
    ):
        payload = _fail(
            _cli(
                ["project", "read-url", pid, "--run", run_id, "--root", str(serve), url],
                serve,
                monkeypatch,
            )
        )
        assert payload["code"] == code
        assert "error" in payload
    assert not (scratch / "index.json").is_file()
    assert list(scratch.glob("inp-*.md")) == []


def test_project_read_rejects_url_source_id(tmp_path, monkeypatch):
    serve, pid, ds = _prepare(tmp_path, monkeypatch)
    run_id = "run-read"
    _running_record(serve, pid, run_id, datasources=[ds.id])
    payload = _fail(
        _cli(
            [
                "project",
                "read",
                pid,
                f"url:{SHEET_URL}",
                "--run",
                run_id,
                "--root",
                str(serve),
            ],
            serve,
            monkeypatch,
        )
    )
    assert payload["code"] == "invalid_request"


def test_read_url_requires_running_run(tmp_path, monkeypatch):
    serve, pid, ds = _prepare(tmp_path, monkeypatch)
    ephemeral = _load(
        _cli(
            ["project", "read-url", pid, "--root", str(serve), SHEET_URL],
            serve,
            monkeypatch,
        )
    )
    assert ephemeral["ok"] is True
    assert ephemeral["input_id"] is None
    assert "sheet-body" in ephemeral["content"]
    run_id = "run-closed"
    rec = _running_record(serve, pid, run_id, datasources=[ds.id])
    from kairo.projects import _save_run

    rec.status = "succeeded"
    _save_run(serve, rec)
    payload = _fail(
        _cli(
            ["project", "read-url", pid, "--run", run_id, "--root", str(serve), SHEET_URL],
            serve,
            monkeypatch,
        )
    )
    assert payload["code"] == "run_closed"


def test_skill_and_prompt_contain_read_url():
    from kairo.install import skill_source_file
    from kairo.projects import _execute_agent_run

    skill = skill_source_file()
    assert skill is not None
    text = skill.read_text(encoding="utf-8")
    assert "kairo project read-url" in text
    assert "一跳" in text
    assert "墨刀" in text
    assert "datasource add" in text
    src = inspect.getsource(_execute_agent_run)
    assert "read-url" in src
    assert "type=url" in src
    from kairo.project_materials import read_url_material

    impl = inspect.getsource(read_url_material)
    assert "add_datasource" not in impl
    assert "write_cache" not in impl
    assert "save_project" not in impl


def test_s2_fixture_artifact_cites_url_input(tmp_path, monkeypatch):
    from kairo.project_materials import load_run_inputs
    from kairo.projects import _execute_agent_run, read_artifact

    serve, pid, _notion = _prepare(tmp_path, monkeypatch)
    ds = seed_existing_datasource(
        serve,
        pid,
        url=SHEET_URL,
        reader="wecom",
        kind="spreadsheet",
        connection_id="wecom",
        name="企微表",
    )
    run_id = "run-s2"
    _running_record(serve, pid, run_id, datasources=[ds.id])
    before = [item.id for item in get_project(serve, pid).datasources]
    out = _execute_agent_run(serve, pid, run_id, FollowUrlProvider())
    assert out.status == "succeeded", out.reason
    after = [item.id for item in get_project(serve, pid).datasources]
    assert after == before
    body = read_artifact(serve, pid, run_id)
    inputs = load_run_inputs(serve, pid, run_id)
    url_item = next(item for item in inputs if item.get("type") == "url")
    assert url_item["url"] == SHEET_URL
    assert f"(input:{url_item['input_id']})" in body
    assert any(item.get("type") == "datasource" for item in inputs)


def test_datasource_unread_still_fails_if_only_url_inputs(tmp_path, monkeypatch):
    from kairo.projects import _execute_agent_run

    serve, pid, ds = _prepare(tmp_path, monkeypatch)
    run_id = "run-unread"
    _running_record(serve, pid, run_id, datasources=[ds.id])
    payload = _load(
        _cli(
            ["project", "read-url", pid, "--run", run_id, "--root", str(serve), SHEET_URL],
            serve,
            monkeypatch,
        )
    )
    out = _execute_agent_run(serve, pid, run_id, _CiteProvider(payload["input_id"]))
    assert out.status == "failed"
    assert out.reason == "datasource_unread"
    assert out.artifact_path is None
