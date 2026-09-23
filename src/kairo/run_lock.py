"""Same-serve-root single-flight lock for mutating `kairo run`."""

from __future__ import annotations

import fcntl
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOCK_NAME = "run.lock"
OCCUPANCY_NAME = "run.occupancy.json"


class RunOccupiedError(RuntimeError):
    """A mutating kairo run already holds the serve-root lock."""

    def __init__(self, message: str, occupancy: dict[str, Any]):
        super().__init__(message)
        self.occupancy = occupancy


def run_lock_dir(serve_root: Path) -> Path:
    return Path(serve_root).expanduser().resolve() / ".kairo"


def occupancy_path(serve_root: Path) -> Path:
    return run_lock_dir(serve_root) / OCCUPANCY_NAME


def lock_path(serve_root: Path) -> Path:
    return run_lock_dir(serve_root) / LOCK_NAME


def read_occupancy(serve_root: Path) -> dict[str, Any]:
    path = occupancy_path(serve_root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def format_occupancy_error(occupancy: dict[str, Any], serve_root: Path) -> str:
    pid = occupancy.get("pid", "unknown")
    started = occupancy.get("started_at", "unknown")
    target = occupancy.get("target", "unknown")
    root = occupancy.get("serve_root") or str(Path(serve_root).expanduser().resolve())
    return (
        "Error: kairo run is already occupying this serve root\n"
        f"  pid: {pid}\n"
        f"  started: {started}\n"
        f"  target: {target}\n"
        f"  serve_root: {root}\n"
        "A second mutating run was refused; wait for the occupant."
    )


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_occupancy(serve_root: Path, occupancy: dict[str, Any]) -> None:
    path = occupancy_path(serve_root)
    tmp = path.with_name(path.name + ".tmp")
    payload = json.dumps(occupancy, ensure_ascii=False, indent=2) + "\n"
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


@dataclass
class RunLock:
    serve_root: Path
    occupancy: dict[str, Any]
    _fd: int
    _released: bool = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            occupancy_path(self.serve_root).unlink(missing_ok=True)
        except OSError:
            pass
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)

    def __enter__(self) -> RunLock:
        return self

    def __exit__(self, *_exc) -> None:
        self.release()


def acquire_run_lock(serve_root: Path, *, target: str) -> RunLock:
    """Take the serve-root exclusive lock or raise RunOccupiedError.

    Granularity is one serve root. The lock is an fcntl flock so a dead
    process releases it; occupancy metadata is for the error message only.
    """
    root = Path(serve_root).expanduser().resolve()
    lock_dir = run_lock_dir(root)
    lock_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path(root), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        occupancy = read_occupancy(root)
        os.close(fd)
        raise RunOccupiedError(format_occupancy_error(occupancy, root), occupancy) from exc
    occupancy = {
        "pid": os.getpid(),
        "started_at": _now_utc(),
        "target": target,
        "serve_root": str(root),
    }
    try:
        _write_occupancy(root, occupancy)
    except Exception:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        raise
    return RunLock(serve_root=root, occupancy=occupancy, _fd=fd)
