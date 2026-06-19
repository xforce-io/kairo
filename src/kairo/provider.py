"""ModelProvider —— 唯一的模型缝。M0 只有确定性 StubProvider。"""

from __future__ import annotations

import hashlib
from typing import Protocol


class ModelProvider(Protocol):
    name: str
    model: str

    def complete(self, prompt: str) -> str: ...


class StubProvider:
    """确定性占位:离线 + 测试。输出含标记与输入摘要,只验骨牌链、不被当真。"""

    name = "stub"
    model = "stub"

    def complete(self, prompt: str) -> str:
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:8]
        return f"⚠️ STUB OUTPUT [{digest}]\n\n{prompt.strip()}"
