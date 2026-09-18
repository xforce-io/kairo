"""#396 view-run 内存会话：按 slug 串行 TaskRegistry.start，不写 state.json。"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from kairo.view_run import ViewRunSession
from kairo.web.tasks import classify_task
from kairo.workspace import Workspace, WorkspaceNotFound


def start_view_run_pump(app, session: ViewRunSession) -> None:
    threading.Thread(
        target=_pump_view_run,
        args=(app, session),
        daemon=True,
        name="kairo-view-run",
    ).start()


def _start_topic(app, slug: str):
    from kairo.web.views import _apply_knowledge_run_boundary, _knowledge_run_boundary

    root = Path(app.state.root)
    ws = Workspace.open(root / slug)
    reg = app.state.registry
    existing = reg.current(slug)
    if existing is not None:
        return existing
    argv = [sys.executable, "-m", "kairo", "run"]
    boundary = _knowledge_run_boundary(ws)
    try:
        task = reg.start(slug, ws.root, argv, job_kind="reconcile")
        _apply_knowledge_run_boundary(task, boundary)
        return task
    except RuntimeError:
        task = reg.current(slug)
        if task is None:
            raise
        return task


def _wait_task(app, session: ViewRunSession, task) -> None:
    reg = app.state.registry
    while not task.done:
        if session.should_stop() and not task.cancel_requested:
            reg.cancel(task.task_id)
        time.sleep(0.05)


def _pump_view_run(app, session: ViewRunSession) -> None:
    slugs = [topic.slug for topic in session.topics]
    try:
        for i, slug in enumerate(slugs):
            if session.should_stop():
                session.abort_remaining(slugs[i:])
                return
            try:
                task = _start_topic(app, slug)
            except (WorkspaceNotFound, RuntimeError, OSError):
                session.record(slug, "failed")
                session.ready.set()
                continue
            session.set_current(i, slug, task.task_id)
            _wait_task(app, session, task)
            result = classify_task(task)
            if result.kind == "cancelled":
                session.record(slug, "cancelled")
                session.abort_remaining(slugs[i + 1 :])
                return
            if result.kind == "failed":
                session.record(slug, "failed")
            else:
                session.record(slug, "ran")
    finally:
        session.mark_finished()
