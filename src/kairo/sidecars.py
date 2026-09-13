"""#378: digest sidecars (brief / extract) run without blocking compose."""

from __future__ import annotations

import contextvars
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

Job = Callable[[], None]


@dataclass
class _Scope:
    pending: list[Job] = field(default_factory=list)
    running: list[threading.Thread] = field(default_factory=list)


_scope: contextvars.ContextVar[_Scope | None] = contextvars.ContextVar("pipeline_sidecars", default=None)


def begin() -> None:
    _scope.set(_Scope())


def defer(job: Job) -> None:
    """Register a sidecar, or run it now when no step() scope is open."""
    scope = _scope.get()
    if scope is None:
        job()
        return
    scope.pending.append(job)


def kick() -> None:
    scope = _scope.get()
    if scope is None or not scope.pending:
        return
    jobs = list(scope.pending)
    scope.pending.clear()
    for job in jobs:
        thread = threading.Thread(target=_run, args=(job,), name="kairo-sidecar", daemon=True)
        thread.start()
        scope.running.append(thread)


def join() -> None:
    scope = _scope.get()
    if scope is None:
        return
    kick()
    for thread in scope.running:
        thread.join()
    scope.running.clear()


def end() -> None:
    try:
        join()
    finally:
        _scope.set(None)


def _run(job: Job) -> None:
    try:
        job()
    except Exception:
        pass
