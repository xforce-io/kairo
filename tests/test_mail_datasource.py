"""#337 邮件检索 Data Source：推断、拒绝凭据、企微/IMAP 读取。"""

from __future__ import annotations

import json
from email.message import EmailMessage

from kairo.project_materials import list_context
from kairo.projects import ProjectError, add_datasource, create_project, get_project
from kairo.readers import (
    INVALID_LINK,
    KIND_MAIL,
    PERMISSION,
    READ_FAILED,
    ReadError,
    _imap_criteria,
    format_mail_markdown,
    infer_source,
    parse_mail_query,
    read_datasource,
)
from kairo.settings import Connection


WECOM_MAIL = "mail://wecom/inbox?keywords=评审会,TR1&begin=2026-08-24&limit=10"
IMAP_MAIL = "imap://alice@imap.example.com/INBOX?keywords=评审会&begin=2026-08-24"


class Proc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_infer_mail_and_imap_queries():
    wecom = infer_source(WECOM_MAIL)
    assert wecom.reader == "wecom" and wecom.kind == KIND_MAIL and wecom.live
    assert wecom.connection_id == "wecom"
    imap = infer_source(IMAP_MAIL)
    assert imap.reader == "imap" and imap.kind == KIND_MAIL and imap.connection_id == "imap"
    spec = parse_mail_query(WECOM_MAIL)
    assert spec.keywords == ("评审会", "TR1")
    assert spec.begin == "2026-08-24" and spec.limit == 10
    with_port = parse_mail_query("imap://alice@imap.example.com:993/INBOX?keywords=x")
    assert with_port.port == 993 and with_port.host == "imap.example.com"


def test_mail_url_with_password_is_invalid_and_not_stored(tmp_path):
    secret = "leaked-imap-pass-337"
    project = create_project(tmp_path, "P")
    try:
        add_datasource(
            tmp_path,
            project.id,
            url=f"imap://alice:{secret}@imap.example.com/INBOX?keywords=x",
        )
        raise AssertionError("expected invalid_link")
    except ProjectError as exc:
        assert exc.code == "invalid_link"
    disk = (tmp_path / ".kairo" / "projects" / project.id / "project.json").read_text(
        encoding="utf-8"
    )
    assert secret not in disk
    assert get_project(tmp_path, project.id).datasources == []


def test_add_mail_datasource_records_mail_search_kind(tmp_path):
    project = create_project(tmp_path, "P")
    wecom = add_datasource(tmp_path, project.id, url=WECOM_MAIL, name="评审会邮件")
    assert wecom.kind == KIND_MAIL and wecom.reader == "wecom"
    imap = add_datasource(tmp_path, project.id, url=IMAP_MAIL, purpose="普通邮箱")
    assert imap.kind == KIND_MAIL and imap.reader == "imap"
    saved = get_project(tmp_path, project.id)
    assert [ds.kind for ds in saved.datasources] == [KIND_MAIL, KIND_MAIL]


def test_unknown_mail_host_is_invalid_link():
    try:
        infer_source("mail://gmail/inbox?keywords=x")
        raise AssertionError("expected invalid")
    except ReadError as exc:
        assert exc.code == INVALID_LINK


def test_wecom_mail_read_hit_and_empty():
    conn = Connection(authorized=True, cmd=None, token_env="")

    def runner_hit(argv, **_kwargs):
        if argv[:3] == ["wecom-cli", "mail", "search"]:
            return Proc(json.dumps({"mails": [{"mail_id": "m1", "subject": "TR1 纪要", "send_time": "2026-09-01", "sender": {"email": "a@x"}}]}))
        if argv[:3] == ["wecom-cli", "mail", "get"]:
            return Proc(
                json.dumps(
                    {
                        "mail_list": [
                            {
                                "mail_id": "m1",
                                "subject": "TR1 纪要",
                                "send_time": "2026-09-01",
                                "sender": {"email": "a@x"},
                                "content": "通过评审。",
                                "attachments": [{"name": "纪要.pdf"}],
                            }
                        ]
                    }
                )
            )
        return Proc("unexpected", returncode=1, stderr="no")

    text = read_datasource(WECOM_MAIL, KIND_MAIL, "wecom", conn, runner=runner_hit)
    assert "TR1 纪要" in text and "通过评审。" in text and "纪要.pdf" in text
    assert "发件人: a@x" in text

    def runner_empty(argv, **_kwargs):
        if argv[:3] == ["wecom-cli", "mail", "search"]:
            return Proc(json.dumps({"mails": []}))
        return Proc("unexpected", returncode=1, stderr="no")

    empty = read_datasource(WECOM_MAIL, KIND_MAIL, "wecom", conn, runner=runner_empty)
    assert "无命中邮件" in empty


def test_wecom_mail_unauthorized_is_permission():
    try:
        read_datasource(
            WECOM_MAIL,
            KIND_MAIL,
            "wecom",
            Connection(authorized=False, cmd=None, token_env=""),
        )
        raise AssertionError("expected permission")
    except ReadError as exc:
        assert exc.code == PERMISSION


def test_imap_read_with_injected_mailbox(monkeypatch):
    raw = EmailMessage()
    raw["Subject"] = "评审会纪要"
    raw["From"] = "ops@example.com"
    raw["Date"] = "Mon, 1 Sep 2026 10:00:00 +0800"
    raw.set_content("本周 TR1 已过。")

    class Box:
        def select(self, folder):
            assert folder == "INBOX"
            return ("OK", [b"1"])

        def search(self, charset, criteria):
            assert charset == "UTF-8"
            assert "SINCE 24-Aug-2026" in criteria
            return ("OK", [b"1"])

        def fetch(self, msg_id, spec):
            return ("OK", [(b"1 (RFC822)", raw.as_bytes())])

        def logout(self):
            return ("BYE", [])

    conn = Connection(authorized=True, token_env="IMAP_PASSWORD")
    monkeypatch.setenv("IMAP_PASSWORD", "not-a-real-secret")
    text = read_datasource(IMAP_MAIL, KIND_MAIL, "imap", conn, mailbox=Box())
    assert "评审会纪要" in text and "本周 TR1 已过。" in text
    assert "ops@example.com" in text


def test_imap_missing_password_is_permission(monkeypatch):
    monkeypatch.delenv("IMAP_PASSWORD", raising=False)
    try:
        read_datasource(
            IMAP_MAIL,
            KIND_MAIL,
            "imap",
            Connection(authorized=True, token_env="IMAP_PASSWORD"),
        )
        raise AssertionError("expected permission")
    except ReadError as exc:
        assert exc.code == PERMISSION


def test_format_mail_markdown_empty_is_success_text():
    spec = parse_mail_query("mail://wecom/inbox?keywords=无")
    text = format_mail_markdown(spec, [])
    assert text.startswith("# 邮件检索：")
    assert "无命中邮件" in text


def test_api_and_html_accept_mail_query_string(tmp_path):
    from fastapi.testclient import TestClient

    from kairo.web.server import create_app

    project = create_project(tmp_path, "P")
    client = TestClient(create_app(tmp_path))
    added = client.post(
        f"/api/projects/{project.id}/datasources",
        json={"url": WECOM_MAIL, "name": "评审会邮件"},
    )
    assert added.status_code == 200, added.text
    body = added.json()
    assert body["ok"] is True
    ds = body["datasource"]
    assert ds["kind"] == KIND_MAIL
    assert ds["reader"] == "wecom"
    page = client.get(f"/projects/{project.id}")
    assert page.status_code == 200
    assert 'type="text"' in page.text
    assert "mail://" in page.text or "imap://" in page.text
    assert "WeCom mail" in page.text or "企微邮件" in page.text
    html = page.text
    assert 'class="obj-actions"' in html
    settings = client.get("/settings")
    assert settings.status_code == 200
    assert "IMAP" in settings.text


def test_imap_connection_is_live_in_settings_catalog():
    from kairo.settings import SettingsDoc, as_public_dict

    public = as_public_dict(SettingsDoc())
    assert public["connections"]["imap"]["live"] is True
    assert public["connections"]["imap"]["token_env"] == "IMAP_PASSWORD"


def test_imap_criteria_english_month_and_inclusive_end():
    spec = parse_mail_query(
        "imap://alice@imap.example.com/INBOX?keywords=评审会&begin=2026-08-24&end=2026-08-24"
    )
    criteria = _imap_criteria(spec)
    assert "SINCE 24-Aug-2026" in criteria
    assert "BEFORE 25-Aug-2026" in criteria
    assert 'TEXT "评审会"' in criteria


def test_mail_datasource_is_agent_material(tmp_path):
    project = create_project(tmp_path, "P")
    ds = add_datasource(tmp_path, project.id, url=WECOM_MAIL, name="评审会邮件")
    catalog = list_context(tmp_path, project.id)
    item = next(row for row in catalog["items"] if row["source_id"] == f"datasource:{ds.id}")
    assert item["type"] == "datasource"
    assert item["title"] == "评审会邮件"


def test_wecom_mail_capability_denied_is_permission():
    def runner(argv, **_kwargs):
        return Proc(
            '{"errcode":851008,"errmsg":"partial no authorization"}',
            returncode=1,
        )

    try:
        read_datasource(
            WECOM_MAIL,
            KIND_MAIL,
            "wecom",
            Connection(authorized=True, cmd=None, token_env=""),
            runner=runner,
        )
        raise AssertionError("expected permission")
    except ReadError as exc:
        assert exc.code == PERMISSION


def test_wecom_mail_search_failure_maps_read_failed():
    def runner(argv, **_kwargs):
        return Proc("", returncode=1, stderr="backend boom")

    try:
        read_datasource(
            WECOM_MAIL,
            KIND_MAIL,
            "wecom",
            Connection(authorized=True, cmd=None, token_env=""),
            runner=runner,
        )
        raise AssertionError("expected read_failed")
    except ReadError as exc:
        assert exc.code == READ_FAILED
