"""reconcile 引擎 —— step 是最外围的薄驱动壳,跑到收敛。

step 不懂规则干啥:扫规则 → 跑 stale 的 → 收敛即停。一次 step 把骨牌倒到底。
收敛是结构性保证(progress 锚离散项),迭代上限只是失控 backstop。
"""

from __future__ import annotations

from kairo.history import snapshot
from kairo.rules import AsrRule, ComposeRule, DigestRule

MAX_ITER = 100


def step(ws, provider) -> bool:
    """跑调和循环到收敛。返回是否有推进。"""
    state = ws.read_state()
    rules = [AsrRule(ws), DigestRule(ws, provider), ComposeRule(ws, provider)]
    any_progress = False
    for _ in range(MAX_ITER):
        progressed = False
        for rule in rules:
            for item in rule.discover(state):
                if item.is_stale(state):
                    item.run(state)
                    progressed = True
        if not progressed:
            break
        any_progress = True
    ws.write_state(state)
    if any_progress:
        snapshot(ws, state)
    return any_progress
