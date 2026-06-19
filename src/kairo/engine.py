"""reconcile 引擎 —— step 是最外围的薄驱动壳,跑到收敛。

step 不懂规则干啥:扫规则 → 跑 stale 的 → 收敛即停。一次 step 把骨牌倒到底。
收敛是结构性保证(progress 锚离散项),迭代上限只是失控 backstop。
"""

from __future__ import annotations

from kairo.history import snapshot
from kairo.rules import AsrRule, ComposeRule, DigestRule, _hash

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


def re_step(ws, provider, target: str | None = None) -> bool:
    """强制重算。全量 / 指定文档(整篇重综合,丢手改)/ 指定 reference(重产 digest)。"""
    state = ws.read_state()
    target_paths = [t.path for t in ws.constitution.targets]
    if target is None:
        for tp in target_paths:
            (ws.root / tp).unlink(missing_ok=True)
        state.targets = {}
    elif target in target_paths:
        (ws.root / target).unlink(missing_ok=True)
        state.targets.pop(target, None)
    else:
        (ws.root / f"references/{target}/digest.md").unlink(missing_ok=True)
        state.products.pop(f"references/{target}/digest.md", None)
    ws.write_state(state)
    return step(ws, provider)


def accept(ws, doc: str) -> None:
    """接受手改:把当前文档内容钉为新 output_hash 基线,解除 blocked。"""
    state = ws.read_state()
    ts = state.targets.get(doc)
    if ts is None:
        return
    ts.output_hash = _hash((ws.root / doc).read_text())
    ts.status = "ok"
    ts.reason = None
    state.targets[doc] = ts
    ws.write_state(state)
