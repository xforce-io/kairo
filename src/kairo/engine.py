"""reconcile 引擎 —— step 是最外围的薄驱动壳,跑到收敛。

step 不懂规则干啥:扫规则 → 跑 stale 的 → 收敛即停。一次 step 把骨牌倒到底。
收敛是结构性保证(progress 锚离散项),迭代上限只是失控 backstop。
"""

from __future__ import annotations

from kairo.history import snapshot
from kairo.rules import ComposeRule, DigestRule, NormalizeRule, TransformRule, _hash
from kairo.stream_index import write_stream_index

MAX_ITER = 100


class ProseError(Exception):
    """单 ref 生成 prose 的前置失败(unknown-ref / not-stream / …)。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _machine_transcript_form(man):
    return next(
        (f for f in man.forms if f.role == "transcript" and f.origin != "added"),
        None,
    )


def prose_precheck(ws, ref_id: str) -> str:
    """校验可生成 prose;返回 key。失败抛 ProseError(无副作用)。"""
    if ref_id not in ws.list_reference_ids():
        raise ProseError("unknown-ref", f"reference 不存在:{ref_id}")
    man = ws.read_manifest(ref_id)
    sc = ws.constitution.source_classes.get(man.source_class)
    if sc is not None and not sc.fold:
        raise ProseError("not-stream", f"基线参考不生成文稿:{ref_id}")
    key = f"references/{ref_id}/prose.md"
    if any(f.role == "prose" for f in man.forms) or (ws.root / key).exists():
        raise ProseError("prose-exists", f"已有可读文稿:{ref_id}")
    if _machine_transcript_form(man) is None:
        raise ProseError("no-machine-transcript", f"需要机器 ASR 誊录才能生成文稿:{ref_id}")
    return key


def can_generate_prose(ws, ref_id: str) -> bool:
    """Web/CLI 显示条件:stream + 机器 transcript + 尚无 prose。"""
    try:
        prose_precheck(ws, ref_id)
    except ProseError:
        return False
    return True


def generate_prose(ws, provider, ref_id: str) -> str:
    """为单条 ref 生成 prose.md(旁路 normalize 开关,不改 constitution,不跑 digest/compose)。

    返回 prose 相对路径。前置失败抛 ProseError(code=…)。
    """
    key = prose_precheck(ws, ref_id)
    rule = NormalizeRule(ws, provider, force_enabled=True)
    items = [it for it in rule.discover() if it.key == key]
    if not items:
        raise ProseError("no-machine-transcript", f"需要机器 ASR 誊录才能生成文稿:{ref_id}")
    state = ws.read_state()
    items[0].run(state)
    ws.write_state(state)
    if not (ws.root / key).is_file():
        raise ProseError("failed", f"生成文稿失败:{ref_id}")
    return key


def _build_rules(ws, provider) -> list:
    """构造调和规则列表(transform 声明驱动 + Normalize/Digest/Compose)。
    discover/is_stale 不碰 provider,故 pending() 可传 provider=None 只读枚举。"""
    transform_rules = [
        TransformRule(ws, t.consumes, t.produces, t.backend)
        for t in ws.constitution.transforms
    ]
    return [
        *transform_rules,
        NormalizeRule(ws, provider),  # ASR 誊录 → 规范化全文 prose(#30),插在 Digest 前
        DigestRule(ws, provider),
        ComposeRule(ws, provider),
    ]


def pending(ws) -> list:
    """当前 stale 的 WorkItem(只读:不跑 provider、不写 state)。dashboard 算待办数用。"""
    state = ws.read_state()
    items = []
    for rule in _build_rules(ws, None):
        items.extend(item for item in rule.discover(state) if item.is_stale(state))
    return items


def step(ws, provider) -> bool:
    """跑调和循环到收敛。返回是否有推进。"""
    state = ws.read_state()
    rules = _build_rules(ws, provider)
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
    write_stream_index(ws)  # 派生导航索引(#16);不进调和循环,每次 step 后刷新
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
