"""reconcile 引擎 —— step 是最外围的薄驱动壳,跑到收敛。

step 不懂规则干啥:扫规则 → 跑 stale 的 → 收敛即停。一次 step 把骨牌倒到底。
收敛是结构性保证(progress 锚离散项),迭代上限只是失控 backstop。
"""

from __future__ import annotations

import shutil

from kairo.history import snapshot
from kairo.models import REASON_PROVIDER_FAILED, TargetState
from kairo.rules import (
    REASON_COMPOSE_MIGRATION_REQUIRED,
    REASON_EXPLICIT_RECOMPOSE,
    ComposeRule,
    DigestRule,
    NormalizeRule,
    ReviewFoldRule,
    TransformRule,
    _hash,
    effective_compose_block_reason,
    leftover_degraded_requires_migration,
)
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
        ReviewFoldRule(ws, provider),  # #193 journal 后附纪要折入该条回顾
        ComposeRule(ws, provider),
    ]


def pending(ws) -> list:
    """当前 stale 的 WorkItem(只读:不跑 provider、不写 state)。dashboard 算待办数用。"""
    state = ws.read_state()
    items = []
    for rule in _build_rules(ws, None):
        items.extend(item for item in rule.discover(state) if item.is_stale(state))
    return items


def has_provider_failed(ws) -> bool:
    """#105:当前 state 是否存在 provider-failed 终态(product 或 target)。

    CLI step/run 据此非零退出,使 Web TaskRegistry 判 failed 而非假成功。
    """
    state = ws.read_state()
    for ps in state.products.values():
        if ps.status == "blocked" and ps.reason == REASON_PROVIDER_FAILED:
            return True
    live_paths = {t.path for t in ws.constitution.live_targets()}
    for path, ts in state.targets.items():
        if (
            path in live_paths
            and ts.status == "blocked"
            and ts.reason == REASON_PROVIDER_FAILED
        ):
            return True
    return False


def promote_oversized_degraded(ws, state=None):
    """#176:写路径把超长 leftover compose-degraded 落到既有迁移 reason。"""
    state = state or ws.read_state()
    changed = False
    live = {t.path for t in ws.constitution.live_targets()}
    for path, ts in state.targets.items():
        if path not in live:
            continue
        if leftover_degraded_requires_migration(ws, path, ts):
            ts.reason = REASON_COMPOSE_MIGRATION_REQUIRED
            ts.status = "blocked"
            changed = True
    if changed:
        ws.write_state(state)
    return state


def step(ws, provider) -> bool:
    """跑调和循环到收敛。返回是否有推进。

    #105:每个 WorkItem 执行后立刻 write_state,使 provider-failed 等 blocked
    诊断在后续 item 挂起/进程被杀时仍已落盘。
    """
    state = promote_oversized_degraded(ws)
    rules = _build_rules(ws, provider)
    any_progress = False
    for _ in range(MAX_ITER):
        progressed = False
        for rule in rules:
            for item in rule.discover(state):
                if item.is_stale(state):
                    item.run(state)
                    progressed = True
                    ws.write_state(state)  # #105 中途落盘
        if not progressed:
            break
        any_progress = True
    ws.write_state(state)
    write_stream_index(ws)  # 派生导航索引(#16);不进调和循环,每次 step 后刷新
    if any_progress:
        snapshot(ws, state)
    return any_progress


def ref_product_blocks(ws, ref_id: str) -> list[dict]:
    """该 reference 下 status=blocked 的 products(供 Web 展示)。"""
    prefix = f"references/{ref_id}/"
    out = []
    for key, ps in ws.read_state().products.items():
        if key.startswith(prefix) and ps.status == "blocked":
            diag = ps.diagnostic
            out.append(
                {
                    "key": key,
                    "name": key[len(prefix) :],
                    "reason": ps.reason or "blocked",
                    "summary": diag.summary if diag else None,
                    "stage": diag.stage if diag else None,
                    "provider": diag.provider if diag else None,
                }
            )
    return sorted(out, key=lambda x: x["name"])


def clear_provider_failed_targets(ws) -> int:
    """清除 target 上的 provider-failed 终态(保留正文),返回清除条数。

    #98:run 等显式恢复入口用;不删 understanding/assessment 已有内容。
    """
    state = ws.read_state()
    n = 0
    live_paths = {t.path for t in ws.constitution.live_targets()}
    for path, ts in list(state.targets.items()):
        if (
            path in live_paths
            and ts.status == "blocked"
            and ts.reason == REASON_PROVIDER_FAILED
        ):
            ts.status = "ok"
            ts.reason = ts.retry_reason
            ts.diagnostic = None
            ts.retry_reason = None
            state.targets[path] = ts
            n += 1
    if n:
        ws.write_state(state)
    return n


def clear_reference_products(ws, ref_id: str) -> None:
    """清除 ref 的派生产物记账与文件,并去掉非 origin=added 的 forms(#73 重试 asr-failed)。

    保留用户添加的源 form(音频/附件/原文)。
    """
    if ref_id not in ws.list_reference_ids():
        raise ValueError(f"reference 不存在:{ref_id}")
    state = ws.read_state()
    prefix = f"references/{ref_id}/"
    for key in list(state.products):
        if key.startswith(prefix):
            (ws.root / key).unlink(missing_ok=True)
            del state.products[key]
    ref_dir = ws.references_dir() / ref_id
    for name in ("transcript.md", "prose.md", "digest.md"):
        (ref_dir / name).unlink(missing_ok=True)
    for p in ref_dir.glob("transcript.*.md"):
        p.unlink(missing_ok=True)
    for p in ref_dir.glob("source_text*.md"):
        p.unlink(missing_ok=True)
    man = ws.read_manifest(ref_id)
    man.forms = [f for f in man.forms if f.origin == "added"]
    ws.write_manifest(ref_id, man)
    ws.write_state(state)


def retry_reference(ws, provider, ref_id: str) -> bool:
    """清除 ref 派生产物(含终态 blocked)后 step 到收敛。

    按需 prose 不受默认 normalize 开关控制；重试前若已存在，收敛后须恢复。
    """
    if ref_id not in ws.list_reference_ids():
        raise ValueError(f"reference 不存在:{ref_id}")
    state = ws.read_state()
    prose_key = f"references/{ref_id}/prose.md"
    manifest = ws.read_manifest(ref_id)
    had_generated_prose = prose_key in state.products or any(
        form.role == "prose" and form.origin != "added" for form in manifest.forms
    )
    clear_reference_products(ws, ref_id)
    progressed = step(ws, provider)
    if had_generated_prose and not (ws.root / prose_key).is_file():
        generate_prose(ws, provider, ref_id)
        progressed = True
    return progressed


def delete_reference(ws, ref_id: str, *, recompose: bool = False, provider=None) -> None:
    """#77:永久删除一条参考。

    - 删 `references/<id>/` 整树与 `state.products` 前缀项
    - 各 target 的 folded / last_major_folded 摘掉该 digest 键
    - 若该 digest 曾 fold 进产物:标 `reason=materials-changed`(正文默认保留,主按钮变运行)
    - recompose=True:整篇 re-step 全量重综合(需 provider;手改可能丢失)
    - 不删用户 workspace 外的原始路径(仅 workspace 内登记/copy)
    """
    if ref_id not in ws.list_reference_ids():
        raise ValueError(f"reference 不存在:{ref_id}")
    if recompose and provider is None:
        raise ValueError("recompose 需要 provider")

    digest_key = f"references/{ref_id}/digest.md"
    prefix = f"references/{ref_id}/"
    state = ws.read_state()

    for key in list(state.products):
        if key.startswith(prefix):
            del state.products[key]

    for path, ts in list(state.targets.items()):
        had = digest_key in ts.folded
        ts.folded = {k: v for k, v in ts.folded.items() if k != digest_key}
        ts.last_major_folded = {
            k: v for k, v in ts.last_major_folded.items() if k != digest_key
        }
        if had and ts.status != "blocked":
            # 材料集变了:账本已诚实,正文待用户运行综合(或本次 recompose)
            ts.reason = "materials-changed"
        state.targets[path] = ts

    ref_dir = ws.references_dir() / ref_id
    if ref_dir.is_dir():
        shutil.rmtree(ref_dir)

    ws.write_state(state)
    write_stream_index(ws)

    if recompose:
        re_step(ws, provider)


def _run_plan_mode(pending: int, retryable: int, non_retryable: int) -> str:
    if pending > 0 and retryable > 0:
        return "run_and_retry"
    if pending > 0:
        return "run"
    if retryable > 0:
        return "retry"
    if non_retryable > 0:
        return "attention"
    return "clean"


def workspace_run_plan(ws) -> dict:
    """#75/#161:主按钮状态机输入,区分总 blocked 与 Run 可重试 blocked。"""
    pending_n = len(pending(ws))
    blocked_refs: list[dict] = []
    for ref_id in ws.list_reference_ids():
        blocks = ref_product_blocks(ws, ref_id)
        if blocks:
            blocked_refs.append({"ref_id": ref_id, "blocks": blocks})
    blocked_targets: list[dict] = []
    live_paths = {t.path for t in ws.constitution.live_targets()}
    for path, ts in ws.read_state().targets.items():
        if path in live_paths and ts.status == "blocked":
            diag = ts.diagnostic
            reason = effective_compose_block_reason(ws, path, ts)
            blocked_targets.append(
                {
                    "path": path,
                    "reason": reason,
                    "summary": diag.summary if diag else None,
                    "stage": diag.stage if diag else None,
                    "provider": diag.provider if diag else None,
                    "retryable": reason == REASON_PROVIDER_FAILED,
                }
            )
    blocked_ref_n = sum(len(b["blocks"]) for b in blocked_refs)
    blocked_n = blocked_ref_n + len(blocked_targets)
    retryable_n = blocked_ref_n + sum(b["retryable"] for b in blocked_targets)
    non_retryable_n = blocked_n - retryable_n
    mode = _run_plan_mode(pending_n, retryable_n, non_retryable_n)
    return {
        "mode": mode,
        "pending_count": pending_n,
        "blocked_count": blocked_n,
        "retryable_blocked_count": retryable_n,
        "blocked_refs": blocked_refs,
        "blocked_targets": blocked_targets,
    }


def run_workspace(ws, provider, *, retry_blocked: bool | None = None) -> bool:
    """推进工作区:#75 主按钮语义。

    retry_blocked:
      None — 自动:有 blocked 则先清再 step
      True/False — 强制

    #98:显式恢复时清除 reference 派生产物 blocked 与 target provider-failed(保留正文)。
    """
    plan = workspace_run_plan(ws)
    if retry_blocked is None:
        retry_blocked = plan["retryable_blocked_count"] > 0
    if retry_blocked:
        for item in plan["blocked_refs"]:
            clear_reference_products(ws, item["ref_id"])
        clear_provider_failed_targets(ws)
    return step(ws, provider)


def re_step(ws, provider, target: str | None = None) -> bool:
    """强制重算。文档保留旧文件/账本到候选校验成功;reference 仍清派生产物。"""
    state = ws.read_state()
    targets = {t.path: t for t in ws.constitution.live_targets()}

    def mark(path: str) -> None:
        target_config = targets[path]
        doc = ws.root / path
        ts = state.targets.get(path) or TargetState(
            depends_on=list(target_config.depends_on),
            output_hash=_hash(doc.read_text()) if doc.is_file() else "",
        )
        ts.status = "ok"
        ts.reason = REASON_EXPLICIT_RECOMPOSE
        ts.diagnostic = None
        ts.retry_reason = None
        state.targets[path] = ts

    if target is None:
        for path in targets:
            mark(path)
        ws.write_state(state)
        return step(ws, provider)
    if target in targets:
        mark(target)
        ws.write_state(state)
        return step(ws, provider)
    # reference id:完整重试(含 asr-failed),不再只删 digest
    return retry_reference(ws, provider, target)


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
