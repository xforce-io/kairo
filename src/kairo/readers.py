"""Data Source Reader。本期仅腾讯文档表格 / 智能表格；分类逻辑不可分叉。"""

from __future__ import annotations

import shlex
import subprocess
from urllib.parse import urlparse

from kairo.settings import CONNECTION_TENCENT, Connection

PERMISSION = "permission"
INVALID_LINK = "invalid_link"
READ_FAILED = "read_failed"

_PERMISSION_MARKERS = ("401", "403", "unauthorized", "forbidden", "permission denied", "权限失效", "未授权")
_INVALID_MARKERS = ("404", "invalid_link", "无效链接", "unknown url", "不是腾讯文档")


class ReadError(Exception):
    """读取失败，code 为 permission / invalid_link / read_failed。"""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def validate_tencent_url(url: str, kind: str) -> None:
    text = (url or "").strip()
    if not text:
        raise ReadError(INVALID_LINK, "链接为空")
    parsed = urlparse(text)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not (
        host == "docs.qq.com" or host.endswith(".docs.qq.com")
    ):
        raise ReadError(INVALID_LINK, "不是腾讯文档链接")
    path = parsed.path.lower()
    if kind == "spreadsheet" and "/sheet" not in path:
        raise ReadError(INVALID_LINK, "不是腾讯文档表格链接")
    if kind == "smartsheet" and "/smartsheet" not in path:
        raise ReadError(INVALID_LINK, "不是腾讯文档智能表格链接")
    if kind not in ("spreadsheet", "smartsheet"):
        raise ReadError(INVALID_LINK, f"不支持的类型:{kind}")


def _classify_failure(blob: str) -> str:
    low = blob.lower()
    if any(m in low for m in _PERMISSION_MARKERS):
        return PERMISSION
    if any(m in low for m in _INVALID_MARKERS):
        return INVALID_LINK
    return READ_FAILED


def read_tencent_docs(
    url: str,
    kind: str,
    connection: Connection,
    *,
    runner=subprocess.run,
) -> str:
    """真实 Reader 入口：先查连接与链接，再跑外部 cmd，映射三类失败。"""
    if connection.authorized is False:
        raise ReadError(PERMISSION, "连接未授权")
    validate_tencent_url(url, kind)
    cmd = (connection.cmd or "").strip()
    if not cmd:
        raise ReadError(READ_FAILED, "未配置腾讯文档读取命令")
    try:
        argv = [part.format(url=url) for part in shlex.split(cmd)]
    except (ValueError, KeyError, IndexError) as exc:
        raise ReadError(READ_FAILED, f"命令无法解析:{exc}") from exc
    try:
        proc = runner(argv, capture_output=True, text=True, check=False)
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


def read_datasource(url: str, kind: str, reader: str, connection: Connection, **kwargs) -> str:
    if reader != CONNECTION_TENCENT and reader != "tencent-docs":
        raise ReadError(READ_FAILED, f"未知 Reader:{reader}")
    return read_tencent_docs(url, kind, connection, **kwargs)
