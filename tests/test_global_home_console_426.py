"""#426 Console default entry opens global-home digest/notes; CLI uses the same path."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from kairo.cli import app as cli_app
from kairo.notes import add_note, show_notes
from kairo.ref_find import read_ref
from kairo.refs import (
    RefRecord,
    add_global_ref,
    add_tag,
    create_tag,
    global_home,
    global_home_path,
    resolve_open,
    resolve_topic_ref_home,
    set_include_tags,
    topic_members,
)
from kairo.status_view import resolve_status_ref
from kairo.web.server import create_app
from kairo.workspace import Workspace

runner = CliRunner()
ZH = {"Accept-Language": "zh-CN"}
SAMPLE_IDS = ("2026-09-23-alpha", "2026-09-23-beta", "2026-09-23-gamma")
UNAVAILABLE = "指定材料不可用"


def _setup_global_samples(tmp_path):
    serve = tmp_path / "root"
    serve.mkdir()
    create_tag(serve, "energy")
    Workspace.init(serve / "energy", topic="energy")
    set_include_tags(serve, "energy", ["energy"])
    gws = None
    for rid in SAMPLE_IDS:
        src = tmp_path / f"{rid}.txt"
        src.write_text(f"{rid} SOURCE\n")
        add_global_ref(serve, [src], ref_id=rid, copy=True)
        add_tag(serve, home="", ref_id=rid, tag="energy")
        if gws is None:
            gws = global_home(serve)
        (gws.references_dir() / rid / "digest.md").write_text(
            f"# {rid} DIGEST\n\n{rid} body\n"
        )
        add_note(serve, ref_id=rid, content=f"{rid} note", home="global")
    return serve


def test_resolve_topic_ref_home_omitted_unique_global():
    members = [
        RefRecord(home="", id="2026-09-23-alpha", title="a", source_class="stream"),
    ]
    assert resolve_topic_ref_home("energy", None, "2026-09-23-alpha", members) == ""
    assert resolve_topic_ref_home("energy", "global", "2026-09-23-alpha", members) == ""
    assert resolve_topic_ref_home("energy", "", "2026-09-23-alpha", members) is None


def test_resolve_topic_ref_home_prefers_topic_local():
    members = [
        RefRecord(home="energy", id="shared", title="local", source_class="stream"),
        RefRecord(home="", id="shared", title="global", source_class="stream"),
        RefRecord(home="other", id="shared", title="other", source_class="stream"),
    ]
    assert resolve_topic_ref_home("energy", None, "shared", members) == "energy"
    assert resolve_topic_ref_home("energy", "global", "shared", members) == ""
    assert resolve_topic_ref_home("energy", None, "missing", members) is None


def test_s1_console_default_entry_opens_global_home_refs(tmp_path):
    serve = _setup_global_samples(tmp_path)
    client = TestClient(create_app(serve))
    dash = client.get("/")
    assert dash.status_code == 200
    assert 'href="/topics/energy"' in dash.text
    alias = client.get("/topics/energy", follow_redirects=False)
    assert alias.status_code == 303
    assert "/w/energy" in alias.headers["location"]

    for rid in SAMPLE_IDS:
        page = client.get(f"/w/energy?ref={rid}", headers=ZH)
        assert page.status_code == 200, rid
        assert UNAVAILABLE not in page.text
        assert f'hx-get="/w/energy/ref/{rid}?home=global&amp;panel=notes"' in page.text
        assert f'href="/w/energy?ref={rid}&amp;home=global"' in page.text
        assert 'nav-doc is-ref is-active' in page.text

        detail = client.get(f"/w/energy/ref/{rid}", headers={**ZH, "HX-Request": "true"})
        assert detail.status_code == 200, rid
        assert UNAVAILABLE not in detail.text
        assert f"{rid} DIGEST" in detail.text
        assert f"{rid} note" in detail.text or f"{rid} note" in page.text

        notes = client.get(f"/w/energy/ref/{rid}/notes", headers=ZH)
        assert notes.status_code == 200, rid
        assert UNAVAILABLE not in notes.text
        assert f"{rid} note" in notes.text
        digest_form = client.get(f"/w/energy/ref/{rid}/form/digest", headers=ZH)
        assert digest_form.status_code == 200, rid
        assert f"{rid} DIGEST" in digest_form.text


def test_s1_understanding_digest_link_omits_home(tmp_path):
    """Default Topic conclusion links to /ref/{id}/form/digest with no ?home=."""
    serve = _setup_global_samples(tmp_path)
    rid = SAMPLE_IDS[0]
    (serve / "energy" / "understanding.md").write_text(
        f"# 结论\n\n见 [纪要](references/{rid}/digest.md)\n"
    )
    client = TestClient(create_app(serve))
    doc = client.get("/w/energy/doc", params={"path": "understanding.md"}, headers=ZH)
    assert doc.status_code == 200
    assert UNAVAILABLE not in doc.text
    assert f"/w/energy/ref/{rid}/form/digest" in doc.text
    assert f"/w/energy/ref/{rid}/form/digest?home=" not in doc.text
    opened = client.get(f"/w/energy/ref/{rid}/form/digest", headers=ZH)
    assert opened.status_code == 200
    assert UNAVAILABLE not in opened.text
    assert f"{rid} DIGEST" in opened.text


def test_s2_cli_and_console_resolve_same_path(tmp_path, monkeypatch):
    serve = _setup_global_samples(tmp_path)
    client = TestClient(create_app(serve))
    topic = Workspace.open(serve / "energy")
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(serve))
    monkeypatch.chdir(serve / "energy")
    members = topic_members(serve, "energy")

    for rid in SAMPLE_IDS:
        expected = global_home_path(serve) / "references" / rid
        assert (expected / "digest.md").is_file()
        assert (expected / "notes.jsonl").is_file()

        console_home = resolve_topic_ref_home("energy", None, rid, members)
        assert console_home == ""
        console_ws, console_id = resolve_open(serve, console_home, rid)
        console_dir = console_ws.references_dir() / console_id
        assert console_dir == expected

        page = client.get(f"/w/energy/ref/{rid}", headers=ZH)
        assert page.status_code == 200
        assert f'href="/refs/{rid}?home=global"' in page.text
        assert f"{rid} DIGEST" in page.text

        payload = read_ref(serve, ref_id=rid, home=None, form="digest")
        assert payload["ok"] is True
        assert payload["home"] == ""
        assert payload["id"] == rid
        assert f"{rid} DIGEST" in payload["content"]

        status = resolve_status_ref(topic, rid, None)
        assert status.id == rid
        assert status.home == ""
        assert status.dir == expected

        tower = show_notes(serve, ref_id=rid, home=None)
        assert tower["count"] == 1
        assert tower["items"][0]["content"] == f"{rid} note"
        assert tower["items"][0]["home"] == ""

        listed = runner.invoke(cli_app, ["ref", "find", "--title", rid, "--json"])
        assert listed.exit_code == 0, listed.output
        found = json.loads(listed.stdout)
        assert found["count"] == 1
        assert found["items"][0]["home"] == ""
        assert found["items"][0]["id"] == rid

        status_cli = runner.invoke(cli_app, ["status", "--ref", rid, "--json"])
        assert status_cli.exit_code == 0, status_cli.output
        status_payload = json.loads(status_cli.stdout)
        assert status_payload["home"] == ""
        assert status_payload["id"] == rid
