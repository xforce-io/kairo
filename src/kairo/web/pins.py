"""serve root 置顶名单:<root>/pinned.yaml,有序 slug 列表。"""

from __future__ import annotations

from pathlib import Path

import yaml

PIN_FILE = "pinned.yaml"


def pin_path(root: Path | str) -> Path:
    return Path(root) / PIN_FILE


def read_pins(root: Path | str) -> list[str]:
    path = pin_path(root)
    if not path.is_file():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in data:
        if isinstance(item, str) and item and item not in seen:
            out.append(item)
            seen.add(item)
    return out


def write_pins(root: Path | str, slugs: list[str]) -> None:
    path = pin_path(root)
    payload = yaml.safe_dump(slugs, allow_unicode=True, default_flow_style=False)
    path.write_text(payload, encoding="utf-8")


def toggle_pin(root: Path | str, slug: str, known: set[str]) -> list[str]:
    """未置顶则插入头部;已置顶则移除。写入时丢掉不在 known 里的幽灵 slug。"""
    pins = [s for s in read_pins(root) if s in known]
    if slug in pins:
        pins = [s for s in pins if s != slug]
    else:
        pins = [slug, *[s for s in pins if s != slug]]
    write_pins(root, pins)
    return pins
