"""step 后台任务与 SSE(占位,Task 7 完善)。"""

from __future__ import annotations


class TaskRegistry:
    def __init__(self) -> None:
        self._tasks: dict = {}
