"""#400 S1–S3: interactive project read, citation records, S3 identifier failures."""

from __future__ import annotations

import re

from kairo.project_materials import write_cache
from kairo.projects import list_runs
from test_project_read_url import (
    SHEET_URL,
    _cli,
    _fail,
    _load,
    _prepare,
    _running_record,
    seed_existing_datasource,
)

MISSING_RUN = "run-deadbeefdead"
MISSING_REC = "rec-deadbeefdead"
CLOSED_RUN = "run-c1c1c1c1c1c1"


def _fail_guide(result):
    payload = _fail(result)
    assert "retryable" in payload
    assert payload.get("next")
    assert "content" not in payload
    assert "input_id" not in payload
    return payload


def _seed_cached(serve, pid, ds, body: str, monkeypatch):
    _load(_cli(["settings", "set", "connections.notion.authorized", "true"], serve, monkeypatch))
    write_cache(serve, pid, ds, body)
    return f"datasource:{ds.id}"


def test_s1_ephemeral_read_and_read_url(tmp_path, monkeypatch):
    serve, pid, ds = _prepare(tmp_path, monkeypatch)
    source_id = _seed_cached(serve, pid, ds, f"# 能源\n\n{SHEET_URL}\n", monkeypatch)
    before_runs = len(list_runs(serve, pid))
    catalog = _load(
        _cli(["project", "context", pid, "--root", str(serve)], serve, monkeypatch)
    )
    assert catalog["ok"] is True
    assert catalog["project_id"] == pid
    assert any(item.get("source_id") == source_id for item in catalog["items"])
    assert len(list_runs(serve, pid)) == before_runs
    registered = _load(
        _cli(["project", "read", pid, source_id, "--root", str(serve)], serve, monkeypatch)
    )
    assert registered["ok"] is True
    assert registered["content"]
    assert registered["version"]
    assert registered["input_id"] is None
    followed = _load(
        _cli(
            ["project", "read-url", pid, "--root", str(serve), SHEET_URL],
            serve,
            monkeypatch,
        )
    )
    assert followed["ok"] is True
    assert followed["input_id"] is None
    assert followed["source_id"] == f"url:{SHEET_URL}"
    assert followed["version"]
    assert "sheet-body" in followed["content"]
    assert "numbered_content" not in followed
    assert len(list_runs(serve, pid)) == before_runs
    records = serve / ".kairo" / "projects" / pid / "records"
    assert not records.exists() or list(records.iterdir()) == []


def test_s2_record_lifecycle_and_task_run_untouched(tmp_path, monkeypatch):
    serve, pid, ds = _prepare(tmp_path, monkeypatch)
    source_id = _seed_cached(serve, pid, ds, f"# 能源\n\n{SHEET_URL}\n", monkeypatch)
    run_id = "run-a1a1a1a1a1a1"
    _running_record(serve, pid, run_id, datasources=[ds.id])
    created = _load(_cli(["project", "record", "create", pid, "--root", str(serve)], serve, monkeypatch))
    rec_id = created["record_id"]
    assert rec_id.startswith("rec-")
    assert created["status"] == "open"
    assert ds.id in created["scope_datasources"]
    empty_show = _load(
        _cli(["project", "record", "show", pid, rec_id, "--root", str(serve)], serve, monkeypatch)
    )
    assert empty_show["inputs"] == []

    extra = seed_existing_datasource(
        serve,
        pid,
        url="https://www.notion.so/" + "b" * 32,
        reader="notion",
        kind="page",
        name="后加",
        ds_id="ds-after-freeze",
    )
    catalog = _load(
        _cli(
            ["project", "context", pid, "--record", rec_id, "--root", str(serve)],
            serve,
            monkeypatch,
        )
    )
    ds_ids = [item["source_id"] for item in catalog["items"] if item["type"] == "datasource"]
    assert f"datasource:{ds.id}" in ds_ids
    assert f"datasource:{extra.id}" not in ds_ids

    _load(
        _cli(
            ["project", "read", pid, source_id, "--record", rec_id, "--root", str(serve)],
            serve,
            monkeypatch,
        )
    )
    followed = _load(
        _cli(
            [
                "project",
                "read-url",
                pid,
                "--record",
                rec_id,
                "--root",
                str(serve),
                SHEET_URL,
            ],
            serve,
            monkeypatch,
        )
    )
    assert followed["input_id"].startswith("inp-")
    assert followed["numbered_content"].startswith("1: ")

    resumed = _load(
        _cli(
            ["project", "record", "resume", pid, rec_id, "--root", str(serve)],
            serve,
            monkeypatch,
        )
    )
    assert resumed["record_id"] == rec_id
    shown = _load(
        _cli(["project", "record", "show", pid, rec_id, "--root", str(serve)], serve, monkeypatch)
    )
    assert any(item["input_id"] == followed["input_id"] for item in shown["inputs"])
    recorded = _load(
        _cli(
            [
                "project",
                "record",
                "input",
                pid,
                rec_id,
                followed["input_id"],
                "--root",
                str(serve),
            ],
            serve,
            monkeypatch,
        )
    )
    assert recorded["ok"] is True
    assert recorded["input_id"] == followed["input_id"]
    assert recorded["content"] == followed["content"]

    ended = _load(
        _cli(["project", "record", "end", pid, rec_id, "--root", str(serve)], serve, monkeypatch)
    )
    assert ended["status"] == "closed"
    again = _load(
        _cli(["project", "record", "end", pid, rec_id, "--root", str(serve)], serve, monkeypatch)
    )
    assert again["status"] == "closed"
    closed_show = _load(
        _cli(["project", "record", "show", pid, rec_id, "--root", str(serve)], serve, monkeypatch)
    )
    assert closed_show["inputs"]
    closed_input = _load(
        _cli(
            [
                "project",
                "record",
                "input",
                pid,
                rec_id,
                followed["input_id"],
                "--root",
                str(serve),
            ],
            serve,
            monkeypatch,
        )
    )
    assert closed_input["content"] == followed["content"]

    run_follow = _load(
        _cli(
            ["project", "read-url", pid, "--run", run_id, "--root", str(serve), SHEET_URL],
            serve,
            monkeypatch,
        )
    )
    assert run_follow["input_id"].startswith("inp-")
    assert run_follow["input_id"] != followed["input_id"]
    assert all(item.id != rec_id for item in list_runs(serve, pid))


def test_s3_identifier_failures_do_not_call_reader(tmp_path, monkeypatch):
    serve, pid, ds = _prepare(tmp_path, monkeypatch)
    other = _load(_cli(["project", "create", "其它"], serve, monkeypatch))
    other_id = other["id"]
    rec = _load(_cli(["project", "record", "create", pid, "--root", str(serve)], serve, monkeypatch))
    rec_id = rec["record_id"]
    _load(_cli(["project", "record", "end", pid, rec_id, "--root", str(serve)], serve, monkeypatch))
    _running_record(serve, pid, CLOSED_RUN, datasources=[ds.id])
    from kairo.projects import _save_run, get_run

    closed = get_run(serve, pid, CLOSED_RUN)
    closed.status = "succeeded"
    _save_run(serve, closed)

    calls = {"n": 0}
    import kairo.project_materials as materials

    original = materials.read_datasource

    def counted(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(materials, "read_datasource", counted)

    cases = [
        (["project", "read-url", pid, "--run", MISSING_RUN, "--root", str(serve), SHEET_URL], "not_found", True),
        (["project", "read-url", pid, "--record", MISSING_REC, "--root", str(serve), SHEET_URL], "not_found", True),
        (["project", "read-url", pid, "--run", CLOSED_RUN, "--root", str(serve), SHEET_URL], "run_closed", False),
        (["project", "read-url", pid, "--record", rec_id, "--root", str(serve), SHEET_URL], "record_closed", False),
        (["project", "read-url", other_id, "--record", rec_id, "--root", str(serve), SHEET_URL], "not_found", True),
        (
            ["project", "read-url", pid, "--run", rec_id, "--root", str(serve), SHEET_URL],
            "invalid_request",
            True,
        ),
        (
            [
                "project",
                "read-url",
                pid,
                "--run",
                CLOSED_RUN,
                "--record",
                rec_id,
                "--root",
                str(serve),
                SHEET_URL,
            ],
            "invalid_request",
            True,
        ),
    ]
    for args, code, retryable in cases:
        payload = _fail_guide(_cli(args, serve, monkeypatch))
        assert payload["code"] == code, (args, payload)
        assert payload["retryable"] is retryable
        assert (
            "record create" in payload["next"]
            or "只保留" in payload["next"]
            or "请使用" in payload["next"]
        )
    assert calls["n"] == 0
    recovered = _load(
        _cli(
            ["project", "read-url", pid, "--root", str(serve), SHEET_URL],
            serve,
            monkeypatch,
        )
    )
    assert recovered["ok"] is True
    assert recovered["input_id"] is None


def test_read_url_help_covers_ephemeral_and_mutex(tmp_path, monkeypatch):
    serve, _pid, _ds = _prepare(tmp_path, monkeypatch)
    result = _cli(["project", "read-url", "--help"], serve, monkeypatch)
    assert result.exit_code == 0, result.output
    text = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert "临时读" in text
    assert "--run" in text
    assert "--record" in text
    assert "互斥" in text


def test_s3_next_can_create_record(tmp_path, monkeypatch):
    serve, pid, ds = _prepare(tmp_path, monkeypatch)
    payload = _fail_guide(
        _cli(
            ["project", "read-url", pid, "--run", MISSING_RUN, "--root", str(serve), SHEET_URL],
            serve,
            monkeypatch,
        )
    )
    assert payload["retryable"] is True
    created = _load(_cli(["project", "record", "create", pid, "--root", str(serve)], serve, monkeypatch))
    followed = _load(
        _cli(
            [
                "project",
                "read-url",
                pid,
                "--record",
                created["record_id"],
                "--root",
                str(serve),
                SHEET_URL,
            ],
            serve,
            monkeypatch,
        )
    )
    assert followed["input_id"].startswith("inp-")
    empty = _load(
        _cli(
            ["project", "record", "show", pid, created["record_id"], "--root", str(serve)],
            serve,
            monkeypatch,
        )
    )
    assert empty["inputs"]
