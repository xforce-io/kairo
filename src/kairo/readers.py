"""Data Source Reader。用户可见分类是平台/Reader，由 URL 推断。"""

from __future__ import annotations

import imaplib
import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from kairo.settings import CONNECTION_IMAP, CONNECTION_TENCENT, Connection

PERMISSION = "permission"
INVALID_LINK = "invalid_link"
READ_FAILED = "read_failed"
UNSUPPORTED = "unsupported_reader"

READER_TENCENT = CONNECTION_TENCENT
READER_WECOM = "wecom"
READER_NOTION = "notion"
READER_IMAP = CONNECTION_IMAP
KIND_MAIL = "mail-search"

_WECOM_HOSTS = ("work.weixin.qq.com", "doc.weixin.qq.com", "page.weixin.qq.com")
_TENCENT_DOCS_HOST = "docs.qq.com"
_WECOM_KINDS = ("document", "spreadsheet", "smartsheet", "smartpage")
_MAIL_LIMIT_DEFAULT = 20
_MAIL_LIMIT_MAX = 50
_IMAP_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


@dataclass(frozen=True)
class InferredSource:
    reader: str
    connection_id: str
    kind: str
    label: str
    live: bool

_PERMISSION_MARKERS = (
    "401",
    "403",
    "unauthorized",
    "forbidden",
    "permission denied",
    "no authorization",
    "权限失效",
    "未授权",
    "未被授权",
)
_INVALID_MARKERS = ("404", "invalid_link", "无效链接", "unknown url", "不是腾讯文档", "不是企微文档")


class ReadError(Exception):
    """读取失败，code 为 permission / invalid_link / read_failed。"""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def infer_source(url: str) -> InferredSource:
    """纯函数：URL → Reader。不读 HTML、不访问网络。"""
    text = (url or "").strip()
    if not text:
        raise ReadError(INVALID_LINK, "链接为空")
    parsed = urlparse(text)
    if parsed.scheme in ("mail", "imap"):
        return _infer_mail(text)
    _reject_url_credentials(parsed)
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    if parsed.scheme not in ("http", "https") or not host:
        raise ReadError(INVALID_LINK, "不是有效链接")
    if host == _TENCENT_DOCS_HOST:
        if path.startswith("/smartsheet/"):
            return InferredSource(READER_TENCENT, READER_TENCENT, "smartsheet", "腾讯文档", True)
        if path.startswith("/sheet/"):
            return InferredSource(READER_TENCENT, READER_TENCENT, "spreadsheet", "腾讯文档", True)
        raise ReadError(INVALID_LINK, "不是腾讯文档表格或智能表格链接")
    if host == "notion.so" or host.endswith(".notion.so") or host == "notion.site" or host.endswith(".notion.site"):
        raise ReadError(UNSUPPORTED, "Notion Reader 尚未接入")
    if any(host == h or host.endswith("." + h) for h in _WECOM_HOSTS):
        return _infer_wecom(path)
    raise ReadError(INVALID_LINK, "无法识别的资料平台")


def _infer_wecom(path: str) -> InferredSource:
    if path.startswith("/smartsheet/"):
        kind = "smartsheet"
    elif path.startswith("/sheet/"):
        kind = "spreadsheet"
    elif path.startswith("/doc/"):
        kind = "document"
    elif "/smartpage/" in path:
        kind = "smartpage"
    else:
        raise ReadError(INVALID_LINK, "不是企微文档链接")
    return InferredSource(READER_WECOM, READER_WECOM, kind, "企微文档", True)


def _reject_url_credentials(parsed, *, allow_username: bool = False) -> None:
    if parsed.password:
        raise ReadError(INVALID_LINK, "链接不得包含凭据")
    if parsed.username and not allow_username:
        raise ReadError(INVALID_LINK, "链接不得包含凭据")


@dataclass(frozen=True)
class MailQuery:
    reader: str
    folder: str
    keywords: tuple[str, ...]
    begin: str | None
    end: str | None
    only_subject: bool
    limit: int
    host: str | None = None
    username: str | None = None
    port: int | None = None
    raw: str = ""


def _truthy(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes")


def _parse_limit(raw: str | None) -> int:
    if not raw:
        return _MAIL_LIMIT_DEFAULT
    try:
        n = int(raw)
    except ValueError as exc:
        raise ReadError(INVALID_LINK, "limit 必须是整数") from exc
    if n < 1 or n > _MAIL_LIMIT_MAX:
        raise ReadError(INVALID_LINK, f"limit 须在 1–{_MAIL_LIMIT_MAX}")
    return n


def _parse_day(raw: str | None, field: str) -> str | None:
    if not raw:
        return None
    try:
        datetime.strptime(raw, "%Y-%m-%d")
    except ValueError as exc:
        raise ReadError(INVALID_LINK, f"{field} 须为 YYYY-MM-DD") from exc
    return raw


def parse_mail_query(url: str) -> MailQuery:
    text = (url or "").strip()
    parsed = urlparse(text)
    if parsed.scheme not in ("mail", "imap"):
        raise ReadError(INVALID_LINK, "不是邮件检索链接")
    _reject_url_credentials(parsed, allow_username=parsed.scheme == "imap")
    query = parse_qs(parsed.query, keep_blank_values=False)
    keywords: list[str] = []
    for item in query.get("keywords") or []:
        keywords.extend(part.strip() for part in item.split(",") if part.strip())
    folder = unquote((parsed.path or "").lstrip("/")) or "INBOX"
    only_subject = any(_truthy(v) for v in (query.get("only_subject") or []))
    limit = _parse_limit((query.get("limit") or [None])[0])
    begin = _parse_day((query.get("begin") or [None])[0], "begin")
    end = _parse_day((query.get("end") or [None])[0], "end")
    if parsed.scheme == "mail":
        host = (parsed.hostname or "").lower()
        if host != "wecom":
            raise ReadError(INVALID_LINK, "mail:// 仅支持 wecom 主机")
        return MailQuery(
            reader=READER_WECOM,
            folder=folder or "inbox",
            keywords=tuple(keywords),
            begin=begin,
            end=end,
            only_subject=only_subject,
            limit=limit,
            raw=text,
        )
    host = (parsed.hostname or "").lower()
    if not host:
        raise ReadError(INVALID_LINK, "IMAP 链接缺少主机")
    username = unquote(parsed.username) if parsed.username else None
    if not username:
        raise ReadError(INVALID_LINK, "IMAP 链接缺少用户名")
    return MailQuery(
        reader=READER_IMAP,
        folder=folder,
        keywords=tuple(keywords),
        begin=begin,
        end=end,
        only_subject=only_subject,
        limit=limit,
        host=host,
        username=username,
        port=parsed.port,
        raw=text,
    )


def _infer_mail(url: str) -> InferredSource:
    spec = parse_mail_query(url)
    if spec.reader == READER_WECOM:
        return InferredSource(READER_WECOM, READER_WECOM, KIND_MAIL, "企微邮件", True)
    return InferredSource(READER_IMAP, READER_IMAP, KIND_MAIL, "IMAP 邮件", True)


def validate_tencent_url(url: str, kind: str) -> None:
    text = (url or "").strip()
    if not text:
        raise ReadError(INVALID_LINK, "链接为空")
    parsed = urlparse(text)
    _reject_url_credentials(parsed)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or host != _TENCENT_DOCS_HOST:
        raise ReadError(INVALID_LINK, "不是腾讯文档链接")
    path = parsed.path.lower()
    if kind == "spreadsheet" and not path.startswith("/sheet/"):
        raise ReadError(INVALID_LINK, "不是腾讯文档表格链接")
    if kind == "smartsheet" and not path.startswith("/smartsheet/"):
        raise ReadError(INVALID_LINK, "不是腾讯文档智能表格链接")
    if kind not in ("spreadsheet", "smartsheet"):
        raise ReadError(INVALID_LINK, f"不支持的类型:{kind}")


def validate_wecom_url(url: str, kind: str) -> None:
    inferred = infer_source(url)
    if inferred.reader != READER_WECOM:
        raise ReadError(INVALID_LINK, "不是企微文档链接")
    if kind not in _WECOM_KINDS:
        raise ReadError(INVALID_LINK, f"不支持的类型:{kind}")
    if kind != inferred.kind:
        raise ReadError(INVALID_LINK, "不是对应形态的企微文档链接")


def _classify_failure(blob: str) -> str:
    low = blob.lower()
    if any(m in low for m in _PERMISSION_MARKERS):
        return PERMISSION
    if any(m in low for m in _INVALID_MARKERS):
        return INVALID_LINK
    return READ_FAILED


READER_TIMEOUT_SECONDS = 30.0


def _run_url_cmd(cmd: str, url: str, runner, timeout: float) -> str:
    try:
        argv = [part.format(url=url) for part in shlex.split(cmd)]
    except (ValueError, KeyError, IndexError) as exc:
        raise ReadError(READ_FAILED, f"命令无法解析:{exc}") from exc
    try:
        proc = runner(argv, capture_output=True, text=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ReadError(READ_FAILED, "读取超时") from exc
    except OSError as exc:
        raise ReadError(READ_FAILED, f"无法启动读取命令:{exc}") from exc
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    if proc.returncode != 0:
        raise ReadError(_classify_failure(stdout + "\n" + stderr), (stderr or stdout).strip() or "读取失败")
    body = stdout.strip()
    if not body:
        raise ReadError(READ_FAILED, "读取结果为空")
    return body


def read_tencent_docs(
    url: str,
    kind: str,
    connection: Connection,
    *,
    runner=subprocess.run,
    timeout: float = READER_TIMEOUT_SECONDS,
) -> str:
    """真实 Reader 入口：先查连接与链接，再跑外部 cmd，映射三类失败。"""
    if connection.authorized is False:
        raise ReadError(PERMISSION, "连接未授权")
    validate_tencent_url(url, kind)
    cmd = (connection.cmd or "").strip()
    if not cmd:
        raise ReadError(READ_FAILED, "未配置腾讯文档读取命令")
    return _run_url_cmd(cmd, url, runner, timeout)


def read_wecom_docs(
    url: str,
    kind: str,
    connection: Connection,
    *,
    runner=subprocess.run,
    timeout: float = READER_TIMEOUT_SECONDS,
) -> str:
    """企微 Reader：已配 cmd 则与腾讯文档相同；否则走本机 wecom-cli 适配器。"""
    if connection.authorized is False:
        raise ReadError(PERMISSION, "连接未授权")
    if kind == KIND_MAIL:
        return read_wecom_mail(url, connection, runner=runner, timeout=timeout)
    validate_wecom_url(url, kind)
    cmd = (connection.cmd or "").strip()
    if cmd:
        return _run_url_cmd(cmd, url, runner, timeout)
    return _read_wecom_cli(url, kind, runner=runner, timeout=timeout)


def _wecom_invoke(runner, args: list[str], timeout: float) -> str:
    try:
        proc = runner(
            ["wecom-cli", *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReadError(READ_FAILED, "读取超时") from exc
    except OSError as exc:
        raise ReadError(READ_FAILED, f"无法启动读取命令:{exc}") from exc
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    if proc.returncode != 0:
        raise ReadError(_classify_failure(stdout + "\n" + stderr), (stderr or stdout).strip() or "读取失败")
    return stdout


def _wecom_json(runner, args: list[str], timeout: float):
    raw = _wecom_invoke(runner, args, timeout).strip()
    if not raw:
        raise ReadError(READ_FAILED, "读取结果为空")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _wecom_payload(runner, parts: list[str], payload: dict, timeout: float):
    return _wecom_json(runner, [*parts, "--json", json.dumps(payload, ensure_ascii=False)], timeout)


def _read_saved_file(path: str) -> str:
    try:
        return Path(str(path)).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ReadError(READ_FAILED, f"无法读取落盘内容:{exc}") from exc


_BODY_KEYS = ("content", "content_file_inner", "file_path", "records", "grid_data")


def _text_from_payload(data) -> str:
    if isinstance(data, str):
        text = data.strip()
        if text:
            return text
        raise ReadError(READ_FAILED, "读取结果为空")
    if not isinstance(data, dict):
        dumped = json.dumps(data, ensure_ascii=False)
        if dumped and dumped not in ("{}", "[]", "null"):
            return dumped
        raise ReadError(READ_FAILED, "读取结果为空")
    content = data.get("content")
    if isinstance(content, dict):
        nested = content.get("markdown_content") or content.get("text")
        if nested and str(nested).strip():
            return str(nested).strip()
    elif isinstance(content, str) and content.strip():
        return content.strip()
    inner = data.get("content_file_inner")
    if inner and str(inner).strip():
        return str(inner).strip()
    path = data.get("file_path")
    if path:
        body = _read_saved_file(path)
        if body:
            return body
    records = data.get("records")
    if records:
        return json.dumps(records, ensure_ascii=False)
    if any(key in data for key in _BODY_KEYS):
        raise ReadError(READ_FAILED, "读取结果为空")
    dumped = json.dumps(data, ensure_ascii=False)
    if dumped and dumped != "{}":
        return dumped
    raise ReadError(READ_FAILED, "读取结果为空")


def _wecom_tab(url: str) -> str | None:
    values = parse_qs(urlparse(url).query).get("tab") or []
    tab = (values[0] if values else "").strip()
    return tab or None


def _read_wecom_cli(url: str, kind: str, *, runner, timeout: float) -> str:
    if kind == "document":
        data = _wecom_payload(runner, ["doc", "contents", "get"], {"docid": url, "content_type": "markdown"}, timeout)
        return _text_from_payload(data)
    if kind == "spreadsheet":
        return _read_wecom_sheet(url, runner, timeout)
    if kind == "smartsheet":
        return _read_wecom_smartsheet(url, runner, timeout)
    if kind == "smartpage":
        return _read_wecom_smartpage(url, runner, timeout)
    raise ReadError(INVALID_LINK, f"不支持的类型:{kind}")


def _read_wecom_sheet(url: str, runner, timeout: float) -> str:
    meta = _wecom_payload(runner, ["sheet", "get"], {"docid": url}, timeout)
    sheets = meta.get("sheets") if isinstance(meta, dict) else None
    if not sheets:
        raise ReadError(READ_FAILED, "读取结果为空")
    tab = _wecom_tab(url)
    if tab:
        sheets = [s for s in sheets if s.get("sheet_id") == tab or s.get("title") == tab]
        if not sheets:
            raise ReadError(INVALID_LINK, "指定的子表不存在")
    parts: list[str] = []
    name = meta.get("name") if isinstance(meta, dict) else None
    if name:
        parts.append(f"# {name}")
    for sheet in sheets:
        sid = sheet.get("sheet_id")
        title = sheet.get("title") or sid or "sheet"
        payload = {"docid": url, "mode": "csv"}
        if sid:
            payload["sheet_id"] = sid
        raw = _wecom_payload(runner, ["sheet", "ranges", "get"], payload, timeout)
        parts.append(f"## {title}\n{_text_from_payload(raw)}")
    body = "\n\n".join(parts).strip()
    if not body:
        raise ReadError(READ_FAILED, "读取结果为空")
    return body


def _sheet_matches_tab(sheet: dict, tab: str) -> bool:
    return tab in (str(sheet.get("sheet_id") or ""), str(sheet.get("title") or ""))


def _smartsheet_usable(sheets: list, url: str) -> list:
    tables = [
        s
        for s in sheets
        if str(s.get("type") or "smartsheet") == "smartsheet"
    ]
    tab = _wecom_tab(url)
    if not tab:
        return tables
    matched = [s for s in tables if _sheet_matches_tab(s, tab)]
    if matched:
        return matched
    if any(_sheet_matches_tab(s, tab) for s in sheets):
        raise ReadError(INVALID_LINK, "指定的是看板不是表格")
    raise ReadError(INVALID_LINK, "指定的子表不存在")


def _read_wecom_smartsheet(url: str, runner, timeout: float) -> str:
    meta = _wecom_payload(runner, ["smartsheet", "sheets", "list"], {"docid": url}, timeout)
    sheets = meta.get("sheets") if isinstance(meta, dict) else None
    if not sheets:
        raise ReadError(READ_FAILED, "读取结果为空")
    usable = _smartsheet_usable(sheets, url)
    if not usable:
        raise ReadError(READ_FAILED, "没有可读取的表格")
    parts: list[str] = []
    name = meta.get("name") if isinstance(meta, dict) else None
    if name:
        parts.append(f"# {name}")
    skipped: list[str] = []
    ok = 0
    for sheet in usable:
        title = sheet.get("title") or sheet.get("sheet_title") or sheet.get("sheet_id") or "sheet"
        try:
            raw = _wecom_payload(
                runner,
                ["smartsheet", "records", "list"],
                {"docid": url, "sheet_title": title, "limit": 100},
                timeout,
            )
            parts.append(f"## {title}\n{_text_from_payload(raw)}")
            ok += 1
        except ReadError as exc:
            skipped.append(f"- {title}: {exc}")
    if skipped:
        parts.append("## 未读取\n" + "\n".join(skipped))
    if ok == 0:
        raise ReadError(READ_FAILED, "读取结果为空")
    return "\n\n".join(parts).strip()


def _page_title(page: dict) -> str:
    return str(page.get("page_title") or page.get("title") or page.get("page_id") or "page")


def _page_markdown(page: dict) -> str | None:
    content = page.get("content")
    if isinstance(content, dict):
        nested = content.get("markdown_content") or content.get("text")
        if nested and str(nested).strip():
            return str(nested).strip()
    inner = page.get("content_file_inner")
    if inner and str(inner).strip():
        return str(inner).strip()
    path = page.get("file_path")
    if path:
        body = _read_saved_file(path)
        if body:
            return body
    return None


def _read_wecom_smartpage(url: str, runner, timeout: float) -> str:
    meta = _wecom_payload(runner, ["smartpage", "pages", "get"], {"docid": url}, timeout)
    pages = meta.get("pages") if isinstance(meta, dict) else None
    if not pages:
        raise ReadError(READ_FAILED, "读取结果为空")
    page_parts: list[str] = []
    for page in pages:
        page_id = page.get("page_id")
        title = _page_title(page)
        body = _page_markdown(page)
        if not body and page_id:
            raw = _wecom_payload(
                runner,
                ["smartpage", "pages", "get"],
                {"docid": url, "page_id": page_id, "content_type": "markdown"},
                timeout,
            )
            if isinstance(raw, dict) and raw.get("pages"):
                raw = raw["pages"][0]
            body = _page_markdown(raw) if isinstance(raw, dict) else _text_from_payload(raw)
        if not body:
            continue
        page_parts.append(f"## {title}\n{body}")
    if not page_parts:
        raise ReadError(READ_FAILED, "读取结果为空")
    doc_title = meta.get("doc_title") if isinstance(meta, dict) else None
    if doc_title:
        return f"# {doc_title}\n\n" + "\n\n".join(page_parts)
    return "\n\n".join(page_parts)


def _mail_heading(spec: MailQuery) -> str:
    bits = []
    if spec.keywords:
        bits.append("keywords=" + ",".join(spec.keywords))
    if spec.begin:
        bits.append(f"begin={spec.begin}")
    if spec.end:
        bits.append(f"end={spec.end}")
    if spec.only_subject:
        bits.append("only_subject=1")
    bits.append(f"limit={spec.limit}")
    return "# 邮件检索：" + " ".join(bits)


def format_mail_markdown(spec: MailQuery, messages: list[dict]) -> str:
    heading = _mail_heading(spec)
    if not messages:
        return heading + "\n\n（无命中邮件）\n"
    blocks = [heading]
    for item in messages:
        subject = str(item.get("subject") or "（无主题）").strip() or "（无主题）"
        sent = str(item.get("sent") or "").strip()
        sender = str(item.get("sender") or "").strip()
        body = str(item.get("body") or "").strip()
        attachments = [str(name).strip() for name in (item.get("attachments") or []) if str(name).strip()]
        title = f"## {sent + ' ' if sent else ''}{subject}".rstrip()
        meta = []
        if sender:
            meta.append(f"- 发件人: {sender}")
        if attachments:
            meta.append("- 附件: " + ", ".join(attachments))
        chunk = title
        if meta:
            chunk += "\n" + "\n".join(meta)
        if body:
            chunk += "\n\n" + body
        blocks.append(chunk)
    return "\n\n".join(blocks) + "\n"


def _addr_text(value) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("email") or value.get("address") or "").strip()
    return str(value or "").strip()


def read_wecom_mail(
    url: str,
    connection: Connection,
    *,
    runner=subprocess.run,
    timeout: float = READER_TIMEOUT_SECONDS,
) -> str:
    if connection.authorized is False:
        raise ReadError(PERMISSION, "连接未授权")
    spec = parse_mail_query(url)
    if spec.reader != READER_WECOM:
        raise ReadError(INVALID_LINK, "不是企微邮件检索链接")
    cmd = (connection.cmd or "").strip()
    if cmd:
        return _run_url_cmd(cmd, url, runner, timeout)
    payload: dict = {"limit": spec.limit, "folder_names": [spec.folder]}
    if spec.keywords:
        payload["keywords"] = list(spec.keywords)
    if spec.begin:
        payload["begin_time"] = f"{spec.begin} 00:00:00"
    if spec.end:
        payload["end_time"] = f"{spec.end} 23:59:59"
    if spec.only_subject:
        payload["only_subject"] = True
    data = _wecom_payload(runner, ["mail", "search"], payload, timeout)
    mails = data.get("mails") if isinstance(data, dict) else None
    if not mails:
        return format_mail_markdown(spec, [])
    messages: list[dict] = []
    ids = [str(item.get("mail_id") or "") for item in mails if item.get("mail_id")]
    details = {}
    if ids:
        raw = _wecom_payload(runner, ["mail", "get"], {"mail_ids": ids[: spec.limit]}, timeout)
        for item in raw.get("mail_list") or [] if isinstance(raw, dict) else []:
            mid = str(item.get("mail_id") or item.get("encode_mail_id") or "")
            if mid:
                details[mid] = item
    for item in mails[: spec.limit]:
        mid = str(item.get("mail_id") or "")
        detail = details.get(mid) or item
        sender = _addr_text((detail.get("sender") or item.get("sender") or {}))
        if not sender and item.get("from"):
            sender = _addr_text(item.get("from"))
        attachments = []
        for att in detail.get("attachments") or []:
            name = att.get("name") if isinstance(att, dict) else str(att)
            if name:
                attachments.append(str(name))
        messages.append(
            {
                "subject": detail.get("subject") or item.get("subject") or "",
                "sent": detail.get("send_time") or item.get("send_time") or "",
                "sender": sender,
                "body": detail.get("content") or detail.get("text") or item.get("content") or "",
                "attachments": attachments,
            }
        )
    return format_mail_markdown(spec, messages)


def _imap_day(value: str) -> str:
    day = datetime.strptime(value, "%Y-%m-%d")
    return f"{day.day:02d}-{_IMAP_MONTHS[day.month - 1]}-{day.year}"


def _imap_before(value: str) -> str:
    """IMAP BEFORE 不含当天；end 按 YYYY-MM-DD 闭区间，推到次日。"""
    day = datetime.strptime(value, "%Y-%m-%d") + timedelta(days=1)
    return f"{day.day:02d}-{_IMAP_MONTHS[day.month - 1]}-{day.year}"


def _imap_criteria(spec: MailQuery) -> str:
    parts: list[str] = []
    if spec.begin:
        parts.append(f"SINCE {_imap_day(spec.begin)}")
    if spec.end:
        parts.append(f"BEFORE {_imap_before(spec.end)}")
    for kw in spec.keywords:
        token = kw.replace('"', "")
        field = "SUBJECT" if spec.only_subject else "TEXT"
        parts.append(f'{field} "{token}"')
    return " ".join(parts) if parts else "ALL"


def _message_text(raw: bytes) -> tuple[str, str, str, str, list[str]]:
    parsed = BytesParser(policy=policy.default).parsebytes(raw)
    subject = str(parsed.get("Subject") or "")
    sender = str(parsed.get("From") or "")
    sent = str(parsed.get("Date") or "")
    body = ""
    if parsed.is_multipart():
        for part in parsed.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                body = part.get_content()
                break
        if not body:
            for part in parsed.walk():
                if part.get_content_type() == "text/html" and not part.get_filename():
                    body = part.get_content()
                    break
    else:
        body = parsed.get_content() if parsed.get_content_type().startswith("text/") else ""
    attachments = []
    for part in parsed.iter_attachments() if hasattr(parsed, "iter_attachments") else []:
        name = part.get_filename()
        if name:
            attachments.append(name)
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    return sent, sender, subject, str(body or "").strip(), attachments


def read_imap_mail(
    url: str,
    connection: Connection,
    *,
    mailbox=None,
    timeout: float = READER_TIMEOUT_SECONDS,
    imap_factory=None,
) -> str:
    if connection.authorized is False:
        raise ReadError(PERMISSION, "连接未授权")
    spec = parse_mail_query(url)
    if spec.reader != READER_IMAP:
        raise ReadError(INVALID_LINK, "不是 IMAP 邮件检索链接")
    password = os.environ.get(connection.token_env or "IMAP_PASSWORD") or ""
    if not password:
        raise ReadError(PERMISSION, "缺少 IMAP 密码环境变量")
    client = mailbox
    owned = False
    try:
        if client is None:
            factory = imap_factory or imaplib.IMAP4_SSL
            if spec.port:
                client = factory(spec.host, spec.port, timeout=timeout)
            else:
                client = factory(spec.host, timeout=timeout)
            owned = True
            login = client.login(spec.username, password)
            if isinstance(login, tuple) and login and login[0] not in ("OK", b"OK"):
                raise ReadError(PERMISSION, "IMAP 登录失败")
        selected = client.select(spec.folder)
        if isinstance(selected, tuple) and selected and selected[0] not in ("OK", b"OK"):
            raise ReadError(READ_FAILED, "无法打开邮件文件夹")
        charset = "UTF-8" if any(ord(ch) > 127 for kw in spec.keywords for ch in kw) else None
        typ, data = client.search(charset, _imap_criteria(spec))
        if typ not in ("OK", b"OK"):
            raise ReadError(READ_FAILED, "IMAP 检索失败")
        ids = (data[0] or b"").split() if data else []
        messages: list[dict] = []
        for msg_id in ids[-spec.limit :]:
            typ, fetched = client.fetch(msg_id, "(RFC822)")
            if typ not in ("OK", b"OK") or not fetched:
                continue
            raw = b""
            for part in fetched:
                if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], (bytes, bytearray)):
                    raw = bytes(part[1])
                    break
            if not raw:
                continue
            sent, sender, subject, body, attachments = _message_text(raw)
            messages.append(
                {
                    "sent": sent,
                    "sender": sender,
                    "subject": subject,
                    "body": body,
                    "attachments": attachments,
                }
            )
        return format_mail_markdown(spec, messages)
    except ReadError:
        raise
    except Exception as exc:
        raise ReadError(_classify_failure(str(exc)), str(exc).strip() or "IMAP 读取失败") from exc
    finally:
        if owned and client is not None:
            try:
                client.logout()
            except Exception:
                pass


def read_datasource(url: str, kind: str, reader: str, connection: Connection, **kwargs) -> str:
    if reader == CONNECTION_TENCENT or reader == "tencent-docs":
        return read_tencent_docs(url, kind, connection, **kwargs)
    if reader == READER_WECOM:
        return read_wecom_docs(url, kind, connection, **kwargs)
    if reader == READER_IMAP:
        return read_imap_mail(url, connection, **kwargs)
    raise ReadError(READ_FAILED, f"未知 Reader:{reader}")
