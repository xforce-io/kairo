"""Dated discussion updates kept alongside the Ref they belong to."""

from __future__ import annotations

import datetime as dt
import fcntl
from dataclasses import dataclass
from pathlib import Path

from pydantic import TypeAdapter


@dataclass(frozen=True)
class ProgressEntry:
    day: dt.date
    content: str


_entries = TypeAdapter(list[ProgressEntry])


def read_progress(ref_dir: Path) -> list[ProgressEntry]:
    try:
        raw = (ref_dir / "progress.json").read_bytes()
    except FileNotFoundError:
        return []
    return sorted(_entries.validate_json(raw), key=lambda entry: entry.day)


def append_progress(ref_dir: Path, entry: ProgressEntry) -> None:
    path = ref_dir / "progress.json"
    with (ref_dir / "progress.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        entries = read_progress(ref_dir)
        entries.append(entry)
        temporary = path.with_suffix(".tmp")
        try:
            temporary.write_bytes(_entries.dump_json(entries))
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
