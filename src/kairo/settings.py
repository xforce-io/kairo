"""本机 Settings：四分区 + 连接健康。凭据只引用环境变量名，不写 token 值。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


PARTITIONS = ("general", "projects", "workspaces", "timeline")
CONNECTION_TENCENT = "tencent-docs"


class SettingsError(ValueError):
    """Settings 读写非法。"""


class Connection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authorized: bool = False
    token_env: str = "TENCENT_DOCS_TOKEN"
    cmd: str | None = None


class SettingsDoc(BaseModel):
    model_config = ConfigDict(extra="forbid")

    general: dict[str, Any] = Field(default_factory=lambda: {"locale": "zh"})
    projects: dict[str, Any] = Field(default_factory=dict)
    workspaces: dict[str, Any] = Field(default_factory=dict)
    timeline: dict[str, Any] = Field(default_factory=dict)
    connections: dict[str, Connection] = Field(
        default_factory=lambda: {CONNECTION_TENCENT: Connection()}
    )


def settings_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "kairo" / "settings.json"


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)
    except Exception as exc:
        temp.unlink(missing_ok=True)
        raise SettingsError(f"保存失败:{exc}") from exc


def load_settings() -> SettingsDoc:
    path = settings_path()
    if not path.is_file():
        return SettingsDoc()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SettingsError(f"无法解析 {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise SettingsError(f"{path} 必须是对象")
    connections = raw.get("connections") or {}
    parsed = {}
    for key, val in connections.items():
        parsed[key] = Connection.model_validate(val if isinstance(val, dict) else {})
    if CONNECTION_TENCENT not in parsed:
        parsed[CONNECTION_TENCENT] = Connection()
    return SettingsDoc(
        general=dict(raw.get("general") or {"locale": "zh"}),
        projects=dict(raw.get("projects") or {}),
        workspaces=dict(raw.get("workspaces") or {}),
        timeline=dict(raw.get("timeline") or {}),
        connections=parsed,
    )


def save_settings(doc: SettingsDoc) -> None:
    payload = {
        "general": doc.general,
        "projects": doc.projects,
        "workspaces": doc.workspaces,
        "timeline": doc.timeline,
        "connections": {k: v.model_dump() for k, v in doc.connections.items()},
    }
    _atomic_write(settings_path(), payload)


def as_public_dict(doc: SettingsDoc | None = None) -> dict[str, Any]:
    """对外展示：连接健康不含 token 值。"""
    doc = doc or load_settings()
    connections = {}
    for name, conn in doc.connections.items():
        token_present = bool(os.environ.get(conn.token_env or ""))
        if not conn.authorized:
            health = "unauthorized"
        elif token_present:
            health = "authorized"
        else:
            health = "missing_token"
        connections[name] = {
            "authorized": conn.authorized,
            "token_env": conn.token_env,
            "cmd_set": bool(conn.cmd),
            "health": health,
        }
    return {
        "general": doc.general,
        "projects": doc.projects,
        "workspaces": doc.workspaces,
        "timeline": doc.timeline,
        "connections": connections,
    }


def get_connection(name: str = CONNECTION_TENCENT) -> Connection:
    doc = load_settings()
    conn = doc.connections.get(name)
    if conn is None:
        raise SettingsError(f"未知连接:{name}")
    return conn


def set_dotted(path: str, value: Any) -> SettingsDoc:
    """写 general.locale / connections.tencent-docs.authorized 等。"""
    parts = [p for p in (path or "").split(".") if p]
    if len(parts) < 2:
        raise SettingsError("设置路径至少两段，如 general.locale")
    doc = load_settings()
    head = parts[0]
    if head in PARTITIONS:
        cursor = getattr(doc, head)
        for key in parts[1:-1]:
            nxt = cursor.get(key)
            if not isinstance(nxt, dict):
                cursor[key] = {}
                nxt = cursor[key]
            cursor = nxt
        parsed = _coerce(value)
        cursor[parts[-1]] = parsed
    elif head == "connections":
        if len(parts) < 3:
            raise SettingsError("连接路径形如 connections.tencent-docs.authorized")
        name, field = parts[1], parts[2]
        conn = doc.connections.get(name) or Connection()
        data = conn.model_dump()
        if field not in data:
            raise SettingsError(f"连接无字段:{field}")
        if field == "authorized":
            data[field] = _as_bool(value)
        elif field == "cmd":
            data[field] = None if value in ("", "null", None) else str(value)
        elif field == "token_env":
            data[field] = str(value)
        else:
            raise SettingsError(f"不可写字段:{field}")
        doc.connections[name] = Connection.model_validate(data)
    else:
        raise SettingsError(f"未知分区:{head}")
    save_settings(doc)
    return doc


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    raise SettingsError(f"不是布尔值:{value!r}")


def _coerce(value: Any) -> Any:
    if isinstance(value, (dict, list, int, float, bool)) or value is None:
        return value
    text = str(value)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text
