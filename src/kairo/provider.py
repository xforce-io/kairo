"""ModelProvider —— 唯一的模型缝。M0 只有确定性 StubProvider。"""

from __future__ import annotations

import hashlib
import os
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


class ClaudeProvider:
    """真 Claude(claude-opus-4-8,adaptive thinking)。client 可注入便于测试。"""

    name = "claude"

    def __init__(self, model: str = "claude-opus-4-8", client=None) -> None:
        self.model = model
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def complete(self, prompt: str) -> str:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in resp.content if b.type == "text")


def select_provider():
    """有 ANTHROPIC_API_KEY 且非 KAIRO_STUB → Claude;否则 stub。"""
    if os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("KAIRO_STUB"):
        return ClaudeProvider()
    return StubProvider()
