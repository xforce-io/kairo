"""Data Source Reader。用户可见分类是平台/Reader，由 URL 推断。"""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from kairo.settings import CONNECTION_TENCENT, Connection

PERMISSION = "permission"
INVALID_LINK = "invalid_link"
READ_FAILED = "read_failed"
UNSUPPORTED = "unsupported_reader"

READER_TENCENT = CONNECTION_TENCENT
READER_WECOM = "wecom"
READER_NOTION = "notion"

_WECOM_HOSTS = ("work.weixin.qq.com", "doc.weixin.qq.com", "page.weixin.qq.com")
_TENCENT_DOCS_HOST = "docs.qq.com"
_WECOM_KINDS = ("document", "spreadsheet", "smartsheet", "smartpage")


@dataclass(frozen=True)
class InferredSource:
    reader: str
    connection_id: str
    kind: str
    label: str
    live: bool

_PERMISSION_MARKERS = ("401", "403", "unauthorized", "forbidden", "permission denied", "权限失效", "未授权")
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


def _reject_url_credentials(parsed) -> None:
    if parsed.username or parsed.password:
        raise ReadError(INVALID_LINK, "链接不得包含凭据")


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
    elif content and str(content).strip() and not str(content).startswith("{"):
        return str(content).strip()
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
    dumped = json.dumps(data, ensure_ascii=False)
    if dumped and dumped != "{}":
        return dumped
    raise ReadError(READ_FAILED, "读取结果为空")


def _require_wecom_auth(runner, timeout: float) -> None:
    status = _wecom_invoke(runner, ["auth", "show", "--status"], timeout).strip().lower()
    if "authorized" not in status or "unauthorized" in status:
        raise ReadError(PERMISSION, "连接未授权")


def _read_wecom_cli(url: str, kind: str, *, runner, timeout: float) -> str:
    _require_wecom_auth(runner, timeout)
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


def _read_wecom_smartsheet(url: str, runner, timeout: float) -> str:
    meta = _wecom_payload(runner, ["smartsheet", "sheets", "list"], {"docid": url}, timeout)
    sheets = meta.get("sheets") if isinstance(meta, dict) else None
    if not sheets:
        raise ReadError(READ_FAILED, "读取结果为空")
    parts: list[str] = []
    for sheet in sheets:
        title = sheet.get("title") or sheet.get("sheet_title") or sheet.get("sheet_id") or "sheet"
        raw = _wecom_payload(
            runner,
            ["smartsheet", "records", "list"],
            {"docid": url, "sheet_title": title, "limit": 100},
            timeout,
        )
        parts.append(f"## {title}\n{_text_from_payload(raw)}")
    body = "\n\n".join(parts).strip()
    if not body:
        raise ReadError(READ_FAILED, "读取结果为空")
    return body


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


def read_datasource(url: str, kind: str, reader: str, connection: Connection, **kwargs) -> str:
    if reader == CONNECTION_TENCENT or reader == "tencent-docs":
        return read_tencent_docs(url, kind, connection, **kwargs)
    if reader == READER_WECOM:
        return read_wecom_docs(url, kind, connection, **kwargs)
    raise ReadError(READ_FAILED, f"未知 Reader:{reader}")
