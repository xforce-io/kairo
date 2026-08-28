"""Digest/Compose 共用的材料目录输入契约。"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CatalogItem:
    """一条可被 agent 读取的材料。"""

    rel_path: str
    abs_path: Path
    role: str
    origin: str
    required: bool
    size: int = 0


def item_size(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def _staged_path(item: CatalogItem, index: int) -> str:
    """生成不受源路径影响、不会碰撞或越界的工作集路径。"""
    bucket = "required" if item.required else "optional"
    key = hashlib.sha256(str(item.abs_path).encode()).hexdigest()[:12]
    suffix = item.abs_path.suffix.lower()
    if not (suffix.isascii() and suffix.startswith(".") and suffix[1:].isalnum()):
        suffix = ""
    return f"{bucket}/{index:04d}-{key}{suffix}"


def _cell(value: object) -> str:
    return str(value).replace("|", "/").replace("\r", " ").replace("\n", " ")


def format_catalog(items: list[CatalogItem]) -> str:
    """写入 prompt 的材料目录，不含正文。"""
    if not items:
        return "[材料目录](空)\n"
    lines = [
        "[材料目录]",
        "标记「必读」必须读完再写产物;「按需」仅在需要时 Read。",
        "表格与清单只抽取关键数字、口径、范围与异常,禁止整表抄入产物。",
        "文件已复制到工作目录的读取路径;目录按读取路径 Read。",
        "",
        "| 标记 | 角色 | 来源 | 原路径 | 读取路径 | 体量 |",
        "|---|---|---|---|---|---|",
    ]
    for index, item in enumerate(items):
        read_path = (
            str(item.abs_path)
            if item.abs_path.is_dir()
            else _staged_path(item, index)
        )
        lines.append(
            "| {} | {} | {} | {} | {} | {}B |".format(
                "必读" if item.required else "按需",
                _cell(item.role),
                _cell(item.origin or "—"),
                _cell(item.rel_path),
                _cell(read_path),
                item.size,
            )
        )
    return "\n".join(lines) + "\n"


def read_dirs_for(items: list[CatalogItem]) -> list[Path]:
    """只授权被明确选择的目录；文件使用工作集副本。"""
    out: list[Path] = []
    for item in items:
        if item.abs_path.is_dir() and item.abs_path not in out:
            out.append(item.abs_path)
    return out


def stage_files(items: list[CatalogItem], artifact_dir: Path) -> None:
    """把材料文件复制到受控且唯一的临时工作集路径。"""
    for index, item in enumerate(items):
        if not item.abs_path.is_file():
            continue
        dest = artifact_dir / _staged_path(item, index)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item.abs_path, dest)
