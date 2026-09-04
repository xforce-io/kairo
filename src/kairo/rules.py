"""流水线规则:ASR / Digest / Compose。

每条规则 `discover()` 扫出待办 WorkItem;engine 用 `is_stale` 判定是否要跑、
`run` 执行副作用(写产物 + 记账)。step 不懂规则干啥,只跑收敛循环。
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from kairo import corpus
from kairo.backends import run_backend
from kairo.machine import resolve_asr
from kairo.models import (
    REASON_PROVIDER_FAILED,
    FailureDiagnostic,
    Form,
    KnowledgeDiagnostic,
    Manifest,
    ProductState,
    State,
    TargetState,
)
from kairo.provenance import (
    REASON_PROVENANCE_INVALID,
    build_source_catalog,
    fact_anchor_ids,
    format_source_catalog_block,
    provenance_protocol_for,
    validate_provenance,
)
from kairo.catalog import (
    CatalogItem,
    format_catalog,
    item_size,
    read_dirs_for,
    stage_files,
)
from kairo.provider import AgentConfig
from kairo.workspace import _keyed_transform_filename

# #98 安全摘要:单行长度上限
_PROVIDER_SUMMARY_MAX = 200
_REDACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Authorization: Bearer <token> 或 Authorization: <token>
    re.compile(r"(?i)(authorization\s*:\s*)(?:bearer\s+)?\S+"),
    re.compile(r"(?i)(bearer\s+)\S+"),
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)\S+"),
    re.compile(r"(?i)(x-api-key\s*[=:]\s*)\S+"),
    re.compile(r"(?i)(token\s*[=:]\s*)\S+"),
    re.compile(r"(?i)(password\s*[=:]\s*)\S+"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b"),
)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _knowledge_context(ws, text: str) -> tuple[str, str | None, KnowledgeDiagnostic]:
    """#182：调用方决定扫描范围；局部歧义不会关闭整个知识上下文。"""
    try:
        from kairo.knowledge import current_hash, effective_entries, load_global, load_workspace
        from kairo.knowledge_matcher import format_knowledge_context, matcher_for

        from kairo.refs import serve_root_of

        serve = serve_root_of(ws)
        entries = effective_entries(serve, ws.root)
        result = matcher_for(entries).match(text)
        # legacy 读取也会转为 KnowledgeEntry；绝不再把全量 glossary 注入 Prompt。
        _ = load_global(serve), load_workspace(ws.root)
        return (
            format_knowledge_context(result),
            current_hash(ws.root.parent, ws.root),
            KnowledgeDiagnostic(
                matched_entry_ids=[hit.entry.id for hit in result.matches],
                ambiguities=len(result.ambiguities),
                truncated=result.truncated_count,
                skipped=len(result.skipped_terms),
            ),
        )
    except Exception as exc:
        # 权威仓储/索引错误不是“零命中”；仅保存已脱敏短摘要，主规则仍可完成。
        return "", None, KnowledgeDiagnostic(
            available=False,
            error_code="knowledge-unavailable",
            safe_summary=safe_provider_summary(exc),
        )


def _legacy_glossary_hash(ws) -> str | None:
    """迁移后 v2 文件不再可由旧真名册读取；保留旧 advisory 的兼容空值。"""
    try:
        from kairo.glossary import current_effective_hash

        return current_effective_hash(ws.root)
    except Exception:
        return None


def safe_provider_summary(
    exc: BaseException | str, *, max_len: int = _PROVIDER_SUMMARY_MAX
) -> str:
    """异常 → 脱敏、单行、截断的安全摘要(可进 state;不保真原始报文)。"""
    raw = str(exc) if not isinstance(exc, str) else exc
    one = re.sub(r"\s+", " ", raw).strip() or "provider call failed"
    for pat in _REDACT_PATTERNS:
        if pat.groups:
            one = pat.sub(r"\1[redacted]", one)
        else:
            one = pat.sub("[redacted]", one)
    if len(one) > max_len:
        one = one[: max_len - 1].rstrip() + "…"
    return one


def make_provider_diagnostic(
    stage: str, provider, exc: BaseException
) -> FailureDiagnostic:
    """归一化为 FailureDiagnostic;provider 标识来自 provider.name。"""
    name = getattr(provider, "name", None) or "unknown"
    return FailureDiagnostic(
        stage=stage,
        provider=str(name),
        summary=safe_provider_summary(exc),
    )


def _form_abs(ws, form) -> Path:
    loc = Path(form.location)
    return loc if loc.is_absolute() else ws.root / loc


def _form_rel(ws, form, abs_path: Path) -> str:
    loc = form.location
    if not Path(loc).is_absolute():
        return loc
    try:
        return str(abs_path.relative_to(ws.root))
    except ValueError:
        return abs_path.name


def _run_agent(
    provider,
    persona: str,
    context: str,
    artifact: str,
    read_dirs=None,
    catalog_items=None,
    *,
    timeout_s: int | None = None,
) -> str:
    """跑 agent,从隔离 artifact_dir 取回产物内容。写沙箱:artifact-only;
    材料目录的必读项复制进工作集;read_dirs 授按需 Read。

    #105:timeout_s 默认 DEFAULT_CLI_TIMEOUT_S;传入显式值可覆盖(测试/长任务)。
    #153:需要授读但 provider 不支持时失败,不回退倾倒全文。
    """
    from kairo.provider import resolve_cli_timeout

    items = list(catalog_items or [])
    dirs: list[Path] = []
    for p in list(read_dirs or []) + read_dirs_for(items):
        rp = Path(p)
        if rp not in dirs:
            dirs.append(rp)
    if (items or dirs) and not getattr(provider, "supports_read_dirs", False):
        name = getattr(provider, "name", "provider")
        raise RuntimeError(f"{name} 不支持授读(read_dirs),无法按目录引用运行")
    # None → 默认 600s；显式值用于测试或长任务。
    effective = resolve_cli_timeout(timeout_s)
    with tempfile.TemporaryDirectory() as d:
        dpath = Path(d)
        stage_files(items, dpath)
        if items:
            dirs.insert(0, dpath)
        provider.run(
            AgentConfig(
                persona=persona,
                context=context,
                artifact_dir=dpath,
                model=provider.model,
                artifact=artifact,
                timeout_s=effective,
                read_dirs=dirs,
            )
        )
        path = dpath / artifact
        if not path.is_file():
            raise RuntimeError(f"provider produced no artifact: {artifact}")
        return path.read_text()


@dataclass
class WorkItem:
    key: str
    input_hash: str
    run: Callable[[State], None]
    is_stale: Callable[[State], bool]


def _bind_home_state(home_ws, topic_ws, item: WorkItem) -> WorkItem:
    """跨 home 的 transform/digest 读写该 Ref 的 home state，不写进当前 Topic。"""
    if home_ws.root.resolve() == topic_ws.root.resolve():
        return item
    orig_run, orig_stale = item.run, item.is_stale

    def run(_state: State) -> None:
        st = home_ws.read_state()
        orig_run(st)
        home_ws.write_state(st)

    def is_stale(_state: State) -> bool:
        return orig_stale(home_ws.read_state())

    return WorkItem(f"{home_ws.root.name}:{item.key}", item.input_hash, run, is_stale)


class TransformRule:
    """声明驱动的资源转换:有 consumes role、无 produces role → 用 backend 产 produces。

    与 ASR 同构,格式无关:audio→transcript(whisper) / 二进制→source_text(markitdown)。
    后端执行委托 backends.run_backend;KAIRO_STUB 下产占位 produces。
    blocked:源丢失 missing-source、未配 asr 后端 no-asr、asr 命令失败 asr-failed、
    markitdown 转换失败 convert-failed。
    corpus(fold=False)是路径引用层(#88 引用模型):不跑 Transform(含 markitdown);
    由 corpus.collect 挂路径 + agent 按需 Read;Web 可预览则预览,否则系统打开。
    consumes/produces/backend 参数化 → 加新转换只声明 Transform。
    """

    def __init__(
        self, ws, consumes=("audio",), produces="transcript", backend="asr-stub"
    ) -> None:
        self.ws = ws
        self.consumes = list(consumes)
        self.produces = produces
        self.backend = backend

    def _emit(self, ref_id: str, key: str, content: str, origin: str) -> None:
        """写 produces 产物 + 给 manifest 追加 form。"""
        (self.ws.root / key).write_text(content)
        m = self.ws.read_manifest(ref_id)
        m.forms.append(
            Form(role=self.produces, location=key, hash=_hash(content), origin=origin)
        )
        self.ws.write_manifest(ref_id, m)

    def discover(self, state: State | None = None) -> list[WorkItem]:
        items: list[WorkItem] = []
        from kairo.refs import member_sources

        for source_ws, ref_id, _rec in member_sources(self.ws):
            rule = (
                self
                if source_ws.root.resolve() == self.ws.root.resolve()
                else TransformRule(
                    source_ws, self.consumes, self.produces, self.backend
                )
            )
            for item in rule._items_for_ref(ref_id):
                items.append(_bind_home_state(source_ws, self.ws, item))
        return items

    def _items_for_ref(self, ref_id: str) -> list[WorkItem]:
        man = self.ws.read_manifest(ref_id)
        sc = self.ws.constitution.source_classes.get(man.source_class)
        if sc is not None and not sc.fold:
            return []  # 基线=路径引用,不派生
        srcs = [f for f in man.forms if f.role in self.consumes]
        if not srcs:
            return []
        items: list[WorkItem] = []
        roles = {f.role for f in man.forms}
        produced_locs = {f.location for f in man.forms if f.role == self.produces}
        legacy = f"references/{ref_id}/{self.produces}.md"
        if len(srcs) == 1:
            # 单源:与原逻辑一致——produces role 已存在(不论来源)则跳过
            if self.produces not in roles:
                items.append(self._make(ref_id, srcs[0], legacy))
        else:
            # 多源:每源独立派生,用 keyed 格式
            for i, src in enumerate(srcs):
                keyed = f"references/{ref_id}/{_keyed_transform_filename(self.produces, src, srcs)}"
                done = keyed in produced_locs
                if not done and i == 0 and legacy in produced_locs:
                    done = True  # 迁移:legacy {produces}.md 归属第一个源
                if not done:
                    items.append(self._make(ref_id, src, keyed))
        return items

    def _make(self, ref_id: str, src: Form, key: str) -> WorkItem:
        input_hash = src.hash
        loc = Path(src.location)
        src_path = loc if loc.is_absolute() else self.ws.root / loc

        def run(state: State) -> None:
            if not src_path.exists():
                state.products[key] = ProductState(
                    input_hash=input_hash, status="blocked", reason="missing-source"
                )
                return
            if os.environ.get("KAIRO_STUB"):
                content = (
                    f"⚠️ STUB {self.produces.upper()}\n"
                    f"(source: {src.location}, hash: {src.hash})\n"
                    f"[stub 占位:无真实 {self.backend} 后端]\n"
                )
                self._emit(ref_id, key, content, f"{self.backend}-from:{src.hash}")
                state.products[key] = ProductState(
                    input_hash=input_hash,
                    produced_by={"provider": self.backend, "model": "stub"},
                )
                return
            outcome = run_backend(self.backend, src_path, src.hash)
            if outcome[0] == "blocked":
                state.products[key] = ProductState(
                    input_hash=input_hash, status="blocked", reason=outcome[1]
                )
                return
            _, text, origin = outcome
            self._emit(ref_id, key, text, origin)
            state.products[key] = ProductState(
                input_hash=input_hash,
                produced_by={"provider": self.backend, "model": origin},
            )

        def is_stale(state: State) -> bool:
            ps = state.products.get(key)
            if ps is None or ps.input_hash != input_hash:
                return True
            # blocked 产物在其前置条件变化时才重试(否则保持收敛)
            if ps.status == "blocked":
                if ps.reason == "missing-source":
                    return src_path.exists()
                if ps.reason == "no-asr":
                    return bool(os.environ.get("KAIRO_STUB")) or (
                        resolve_asr(self.backend) is not None
                    )
            return False

        return WorkItem(key, input_hash, run, is_stale)


_OUTPUT_DISCIPLINE = (
    "\n\n[输出纪律]\n"
    "- 只输出文档正文本身,不要旁白、元评论、寒暄,或「需要的话我可以…」式的提议。\n"
    "- 不寻常的专名(品牌/人名)若仅单一来源支持,标 ⚠️ 待核,不要默认采信为事实。"
)

_COMPOSE_DISCIPLINE = (
    "\n- 你只产出当前这一个文档,不要内联其它文档的内容。\n"
    "- 溯源使用来源目录中的短 ID〔S-…〕与文末索引;不要在正文堆叠完整 "
    "`references/.../digest.md` 路径(索引表链接除外)。\n"
    "- 你必须输出当前文档的**完整全文**(含未改动章节);即使本轮判断无需演进,"
    "也要原样重述全文,禁止只输出「为何不改」的变更说明或差异摘要。"
)

_CATALOG_DISCIPLINE = (
    "\n\n[阅读纪律]\n"
    "- 先按材料目录读完全部「必读」文件,再写产物。\n"
    "- 「按需」仅在必读仍不足时 Read。\n"
    "- 表格/清单只抽关键数字、口径、范围与异常,禁止整表抄入。\n"
    "- 不要编造文件中没有的事实。"
)

# 退化护栏(#28):上一版充分长却被骤缩覆盖 → 极可能是 agent 吐了变更说明而非全文。
# 阈值保守,仅拦灾难性缩水;正常的重组/修正/推翻不会触发。
# digest 写路径共用同一谓词,避免失败短文盖掉长纪要。
_COMPOSE_MIN_PRIOR_LEN = 2000
_COMPOSE_DEGRADE_RATIO = 0.5
UNDERSTANDING_MAX_CHARS = 20_000
REASON_COMPOSE_MIGRATION_REQUIRED = "compose-migration-required"
REASON_COMPOSE_OVER_BUDGET = "compose-over-budget"
REASON_EXPLICIT_RECOMPOSE = "explicit-recompose"
REASON_DIGEST_DEGRADED = "digest-degraded"


def is_catastrophic_shrink(prior: str, candidate: str) -> bool:
    """上一版充分长且候选不及一半 → 灾难性骤缩。无/过短 prior 不拦。"""
    return (
        len(prior) > _COMPOSE_MIN_PRIOR_LEN
        and len(candidate) < _COMPOSE_DEGRADE_RATIO * len(prior)
    )


def leftover_degraded_requires_migration(ws, path: str, ts) -> bool:
    """#176:超长 leftover compose-degraded 走既有 20k 迁移门禁。不新增阈值。"""
    if path != "understanding.md" or ts is None:
        return False
    if ts.status != "blocked" or ts.reason != "compose-degraded":
        return False
    doc = ws.root / path
    return doc.is_file() and len(doc.read_text()) > UNDERSTANDING_MAX_CHARS


def effective_compose_block_reason(ws, path: str, ts) -> str | None:
    """观察面 reason:超长 leftover degraded 显示为既有迁移门禁。"""
    if leftover_degraded_requires_migration(ws, path, ts):
        return REASON_COMPOSE_MIGRATION_REQUIRED
    return ts.reason if ts else None


class NormalizeRule:
    """ASR 派生的誊录(机器转写,有噪声)→ 规范化可读全文 prose(用 provider)。

    可选档案层(constitution.pipeline.normalize.enabled,默认关):prose 只给人读,
    不进 digest 路径——digest 恒从 transcript(信息上界),故无二次有损、无需护栏(#33)。
    只碰机器派生的 transcript(origin≠added);人提供的原文与 corpus 不碰。
    """

    def __init__(self, ws, provider, *, force_enabled: bool = False) -> None:
        self.ws = ws
        self.provider = provider
        # force_enabled:按需单次旁路(不写 constitution);普通 step 仍看 constitution 开关
        self.enabled = True if force_enabled else ws.constitution.pipeline.normalize.enabled
        self.prompt = ws.constitution.pipeline.normalize.prompt

    def discover(self, state: State | None = None) -> list[WorkItem]:
        if not self.enabled:  # 默认关:不产 prose(可选档案)
            return []
        items: list[WorkItem] = []
        from kairo.refs import member_sources

        for source_ws, ref_id, _rec in member_sources(self.ws):
            rule = (
                self
                if source_ws.root.resolve() == self.ws.root.resolve()
                else NormalizeRule(
                    source_ws, self.provider, force_enabled=self.enabled
                )
            )
            for item in rule._items_for_ref(ref_id):
                items.append(_bind_home_state(source_ws, self.ws, item))
        return items

    def _items_for_ref(self, ref_id: str) -> list[WorkItem]:
        man = self.ws.read_manifest(ref_id)
        # 源分层:corpus(fold=False)是只读参考层,不规范化(与不 digest 一致)。
        sc = self.ws.constitution.source_classes.get(man.source_class)
        if sc is not None and not sc.fold:
            return []
        roles = {f.role for f in man.forms}
        if "prose" in roles:
            return []
        # 只规范化机器派生的誊录;origin=added 是人给的原文(权威),不碰。
        tf = next(
            (f for f in man.forms if f.role == "transcript" and f.origin != "added"),
            None,
        )
        if tf is None:
            return []
        loc = Path(tf.location)
        p = loc if loc.is_absolute() else self.ws.root / loc
        key = f"references/{ref_id}/prose.md"
        if (self.ws.root / key).exists():
            return []
        return [self._make(ref_id, key, p.read_text())]

    def _make(self, ref_id: str, key: str, body: str) -> WorkItem:
        input_hash = _hash(f"{self.prompt}\n\n---誊录---\n{body}")

        def run(state: State) -> None:
            knowledge_context, knowledge_hash, knowledge_diagnostic = _knowledge_context(self.ws, body)
            content = _run_agent(
                self.provider,
                self.prompt
                + knowledge_context
                + _OUTPUT_DISCIPLINE,
                body,
                "prose.md",
            )
            (self.ws.root / key).write_text(content)
            m = self.ws.read_manifest(ref_id)
            m.forms.append(
                Form(
                    role="prose",
                    location=key,
                    hash=_hash(content),
                    origin=f"normalize-from:{_hash(body)}",
                )
            )
            self.ws.write_manifest(ref_id, m)
            state.products[key] = ProductState(
                input_hash=input_hash,
                produced_by={
                    "provider": self.provider.name,
                    "model": self.provider.model,
                },
                glossary_hash=_legacy_glossary_hash(self.ws),
                knowledge_hash=knowledge_hash,
                knowledge_diagnostic=knowledge_diagnostic,
                knowledge_generation=uuid.uuid4().hex,
            )

        def is_stale(state: State) -> bool:
            ps = state.products.get(key)
            return ps is None or ps.input_hash != input_hash

        return WorkItem(key, input_hash, run, is_stale)


class DigestRule:
    """有正文(transcript/source_text)且无 digest → 产高密度记忆纪要(用 provider)。

    #193:journal 不计该条回顾 source_text,避免折入后指纹循环。
    """

    def __init__(self, ws, provider) -> None:
        self.ws = ws
        self.provider = provider
        self.prompt = ws.constitution.pipeline.digest.prompt

    def _digest_roles(self) -> list[str]:
        roles = list(self.ws.constitution.body_roles)
        from kairo.kind import is_journal_workspace

        if is_journal_workspace(self.ws):
            return [r for r in roles if r != "source_text"]
        return roles

    def _read_body(self, man) -> str | None:
        """拼接正文供指纹与测试;不进入 provider prompt(#153)。"""
        chunks: list[str] = []
        for role in self._digest_roles():
            forms = sorted(
                (f for f in man.forms if f.role == role),
                key=lambda f: f.location,
            )
            for f in forms:
                p = _form_abs(self.ws, f)
                if not p.is_file():
                    continue
                try:
                    text = p.read_text()
                except UnicodeDecodeError:
                    continue
                # 匹配与知识上下文只看正文，文件名不应造成 filename-only 命中。
                chunks.append(text)
        return "\n\n".join(chunks) if chunks else None

    def _catalog_items(self, man) -> list[CatalogItem]:
        items: list[CatalogItem] = []
        for role in self._digest_roles():
            forms = sorted(
                (f for f in man.forms if f.role == role),
                key=lambda f: f.location,
            )
            for f in forms:
                p = _form_abs(self.ws, f)
                if not p.is_file():
                    continue
                try:
                    p.read_text()
                except UnicodeDecodeError:
                    continue
                items.append(
                    CatalogItem(
                        rel_path=_form_rel(self.ws, f, p),
                        abs_path=p,
                        role=f.role,
                        origin=f.origin,
                        required=True,
                        size=item_size(p),
                    )
                )
        atts = sorted(
            (f for f in man.forms if f.role == "attachment"),
            key=lambda f: f.location,
        )
        for f in atts:
            p = _form_abs(self.ws, f)
            if not p.is_file():
                continue
            items.append(
                CatalogItem(
                    rel_path=_form_rel(self.ws, f, p),
                    abs_path=p,
                    role=f.role,
                    origin=f.origin,
                    required=False,
                    size=item_size(p),
                )
            )
        return items

    def discover(self, state: State | None = None) -> list[WorkItem]:
        from kairo.kind import stage_enabled

        if not stage_enabled(self.ws, "digest"):
            return []
        items: list[WorkItem] = []
        from kairo.refs import member_sources

        for source_ws, ref_id, _rec in member_sources(self.ws):
            man = source_ws.read_manifest(ref_id)
            # 源分层(#13 v2):fold=False 的类(corpus)是只读参考层,不 digest。
            sc = source_ws.constitution.source_classes.get(man.source_class)
            if sc is not None and not sc.fold:
                continue
            source_rule = (
                self
                if source_ws.root.resolve() == self.ws.root.resolve()
                else DigestRule(source_ws, self.provider)
            )
            catalog = source_rule._catalog_items(man)
            if any(it.required for it in catalog):
                item = source_rule._make(
                    f"references/{ref_id}/digest.md",
                    man,
                    catalog,
                )
                items.append(_bind_home_state(source_ws, self.ws, item))
        return items

    def _make(
        self,
        key: str,
        man: Manifest,
        catalog: list[CatalogItem],
        *,
        ledger_key: str | None = None,
    ) -> WorkItem:
        product_key = ledger_key or key
        atts = sorted(
            (f for f in man.forms if f.role == "attachment"),
            key=lambda f: f.location,
        )
        body = self._read_body(man) or ""
        fingerprint = f"{self.prompt}\n\n---正文---\n{body}" + "".join(f.hash for f in atts)
        input_hash = _hash(fingerprint)

        def run(state: State) -> None:
            knowledge_context, knowledge_hash, knowledge_diagnostic = _knowledge_context(self.ws, body)
            persona = (
                self.prompt
                + knowledge_context
                + _CATALOG_DISCIPLINE
                + _OUTPUT_DISCIPLINE
            )
            context = format_catalog(catalog)
            try:
                content = _run_agent(
                    self.provider,
                    persona,
                    context,
                    "digest.md",
                    catalog_items=catalog,
                )
            except Exception as exc:  # #98:可归属 provider 失败 → 持久化诊断,不写半成品
                import sys

                # #105:写入任务流,供 Web classify 与人读日志(超时文案含 CLI agent timeout)
                print(
                    f"Error: provider-failed stage=digest: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
                state.products[product_key] = ProductState(
                    input_hash=input_hash,
                    status="blocked",
                    reason=REASON_PROVIDER_FAILED,
                    diagnostic=make_provider_diagnostic("digest", self.provider, exc),
                )
                return
            dest = self.ws.root / key
            prior = dest.read_text() if dest.is_file() else ""
            if is_catastrophic_shrink(prior, content):
                # 与 compose #28 同谓词:拒绝覆盖,input_hash 对齐使 step 收敛。
                state.products[product_key] = ProductState(
                    input_hash=input_hash,
                    status="blocked",
                    reason=REASON_DIGEST_DEGRADED,
                )
                return
            dest.write_text(content)
            from kairo.knowledge_review import extract_after_success

            state.products[product_key] = ProductState(
                input_hash=input_hash,
                produced_by={
                    "provider": self.provider.name,
                    "model": self.provider.model,
                },
                glossary_hash=_legacy_glossary_hash(self.ws),
                knowledge_hash=knowledge_hash,
                knowledge_diagnostic=knowledge_diagnostic,
                knowledge_generation=uuid.uuid4().hex,
            )
            ref_id = key.split("/")[1] if key.count("/") >= 2 else ""
            if ref_id:
                from kairo.refs import serve_root_of

                extract_after_success(
                    self.ws.root,
                    serve_root_of(self.ws),
                    source_kind="digest",
                    path=product_key,
                    text=content,
                    provider=self.provider,
                )
                # glossary_review 在首次读取时原子迁移，此后只保留 knowledge_review 单一路径。

        def is_stale(state: State) -> bool:
            # input_hash 匹配即收敛(含 provider-failed / digest-degraded 终态);hash 变才重试
            ps = state.products.get(product_key)
            return ps is None or ps.input_hash != input_hash

        return WorkItem(product_key, input_hash, run, is_stale)


_REVIEW_FOLD_PERSONA = (
    "把新纪要并入这篇时段回顾。"
    "保留原结构（发生了什么 / 待跟进事项 / 明显冲突与未调和点）；没有的节按材料需要可补。"
    "纪要中的新事实必须写入；与原回顾冲突时以纪要为准并点明。"
    "不要编造纪要和原文都没有的事实。不要把誊录当正文。只输出回顾全文。"
)


class ReviewFoldRule:
    """#193:journal 回顾在 digest 成功后把纪要写进该条 source_text。"""

    def __init__(self, ws, provider) -> None:
        self.ws = ws
        self.provider = provider

    def discover(self, state: State | None = None) -> list[WorkItem]:
        from kairo.kind import is_journal_workspace

        if not is_journal_workspace(self.ws):
            return []
        items: list[WorkItem] = []
        from kairo.refs import run_ref_ids

        for ref_id in run_ref_ids(self.ws):
            man = self.ws.read_manifest(ref_id)
            src = next((f for f in man.forms if f.role == "source_text"), None)
            if src is None:
                continue
            digest_key = f"references/{ref_id}/digest.md"
            digest_path = self.ws.root / digest_key
            if not digest_path.is_file():
                continue
            ps = None if state is None else state.products.get(digest_key)
            if ps is None or ps.status == "blocked" or not ps.input_hash:
                continue
            items.append(self._make(ref_id, src, digest_path, ps.input_hash))
        return items

    def _make(self, ref_id: str, src: Form, digest_path: Path, input_hash: str) -> WorkItem:
        key = f"references/{ref_id}/review_fold"
        src_path = _form_abs(self.ws, src)

        def run(state: State) -> None:
            if not src_path.is_file() or not digest_path.is_file():
                state.products[key] = ProductState(
                    input_hash=input_hash, status="blocked", reason="missing-source"
                )
                return
            materials = [
                CatalogItem(
                    rel_path=_form_rel(self.ws, src, src_path),
                    abs_path=src_path,
                    role="source_text",
                    origin=src.origin,
                    required=True,
                    size=item_size(src_path),
                ),
                CatalogItem(
                    rel_path=f"references/{ref_id}/digest.md",
                    abs_path=digest_path,
                    role="digest",
                    origin="digest",
                    required=True,
                    size=item_size(digest_path),
                ),
            ]
            try:
                content = _run_agent(
                    self.provider,
                    _REVIEW_FOLD_PERSONA + _CATALOG_DISCIPLINE + _OUTPUT_DISCIPLINE,
                    format_catalog(materials),
                    "review.md",
                    catalog_items=materials,
                )
            except Exception as exc:
                import sys

                print(
                    f"Error: provider-failed stage=review-fold: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
                state.products[key] = ProductState(
                    input_hash=input_hash,
                    status="blocked",
                    reason=REASON_PROVIDER_FAILED,
                    diagnostic=make_provider_diagnostic(
                        "review-fold", self.provider, exc
                    ),
                )
                return
            from kairo.review import strip_process_preamble

            body = strip_process_preamble(content)
            if not body.strip():
                state.products[key] = ProductState(
                    input_hash=input_hash,
                    status="blocked",
                    reason=REASON_PROVIDER_FAILED,
                    diagnostic=make_provider_diagnostic(
                        "review-fold",
                        self.provider,
                        RuntimeError("empty review fold"),
                    ),
                )
                return
            src_path.parent.mkdir(parents=True, exist_ok=True)
            src_path.write_text(body, encoding="utf-8")
            new_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
            man = self.ws.read_manifest(ref_id)
            man.forms = [
                f.model_copy(update={"hash": new_hash})
                if f.role == "source_text"
                else f
                for f in man.forms
            ]
            self.ws.write_manifest(ref_id, man)
            state.products[key] = ProductState(
                input_hash=input_hash,
                produced_by={
                    "provider": self.provider.name,
                    "model": self.provider.model,
                },
            )

        def is_stale(state: State) -> bool:
            ps = state.products.get(key)
            return ps is None or ps.input_hash != input_hash

        return WorkItem(key, input_hash, run, is_stale)


class ComposeRule:
    """某 target 有未融入的 Δdigest → 一次 op 批量融入(B-批量增量,挂源)。"""

    def __init__(self, ws, provider) -> None:
        self.ws = ws
        self.provider = provider
        self._member_digests = {}

    def _is_fold_class(self, source_class: str) -> bool:
        """该类源是否折叠进 target(fold=True);fold=False 为只读参考层(corpus)。"""
        sc = self.ws.constitution.source_classes.get(source_class)
        return sc is None or sc.fold

    def _all_digests(self) -> dict[str, str]:
        out: dict[str, str] = {}
        from kairo.refs import RefRecord, load_catalog, run_ref_ids, topic_members

        self._member_digests = {}
        try:
            records = topic_members(self.ws.root.parent, self.ws.root.name)
        except Exception:
            records = []
        if not records and not load_catalog(self.ws.root.parent).get(
            "strict_membership", False
        ):
            records = []
            for ref_id in run_ref_ids(self.ws):
                man = self.ws.read_manifest(ref_id)
                digest = self.ws.references_dir() / ref_id / "digest.md"
                records.append(
                    RefRecord(
                        home=self.ws.root.name,
                        id=ref_id,
                        title=man.title or ref_id,
                        source_class=man.source_class,
                        digest_path=digest if digest.is_file() else None,
                    )
                )
        for rec in records:
            # corpus 是只读参考层；其他 source class 沿用 Topic 的 fold 配置。
            if rec.source_class == "corpus" or not self._is_fold_class(rec.source_class):
                continue
            if rec.digest_path is not None and rec.digest_path.is_file():
                # 跨 home 一律用 Ref 身份键。本 Topic home 在未迁移前保留相对路径。
                key = (
                    rec.key
                    if rec.home != self.ws.root.name
                    or load_catalog(self.ws.root.parent).get("strict_membership", False)
                    else f"references/{rec.id}/digest.md"
                )
                out[key] = _hash(rec.digest_path.read_text())
                self._member_digests[key] = rec
        return out

    def corpus_drifted(self, target_path: str, state: State) -> bool:
        """corpus 自该 target 上次折叠后是否变更(advisory;不进 staleness 循环)。"""
        ts = state.targets.get(target_path)
        return ts is not None and ts.corpus_stamp != corpus.stamp(corpus.collect(self.ws))

    def _upstream_changed(self, target, state, ts) -> bool:
        # 从未 compose 过的 target 不靠「未记录上游」变 stale(#134)。
        # 首次 compose 由 delta / materials-changed / 手改驱动;级联只发生在已有账本上。
        if ts is None:
            return False
        for dep in target.depends_on:
            dep_out = (
                state.targets[dep].output_hash
                if (state and dep in state.targets)
                else ""
            )
            recorded = ts.upstream_hash.get(dep)
            if recorded != dep_out:
                return True
        return False

    def _is_edited(self, path: str, ts) -> bool:
        doc = self.ws.root / path
        return (
            ts is not None
            and ts.status != "blocked"  # 已 blocked 不重复处理
            and doc.exists()
            and _hash(doc.read_text()) != ts.output_hash
        )

    def discover(self, state: State | None = None) -> list[WorkItem]:
        from kairo.kind import stage_enabled

        if not stage_enabled(self.ws, "compose"):
            return []
        all_digests = self._all_digests()
        items: list[WorkItem] = []
        for target in self.ws.constitution.live_targets():
            ts = state.targets.get(target.path) if state else None
            folded = ts.folded if ts else {}
            delta = {p: h for p, h in all_digests.items() if folded.get(p) != h}
            materials_changed = ts is not None and ts.reason == "materials-changed"
            explicit_recompose = (
                ts is not None and ts.reason == REASON_EXPLICIT_RECOMPOSE
            )
            if (
                delta
                or materials_changed
                or explicit_recompose
                or self._upstream_changed(target, state, ts)
                or self._is_edited(target.path, ts)
            ):
                items.append(self._make(target, delta, all_digests))
        return items

    def _make(self, target, delta: dict[str, str], all_digests: dict[str, str]) -> WorkItem:
        key = target.path
        input_hash = _hash("".join(sorted(all_digests.values())))

        def run(state: State) -> None:
            doc_path = self.ws.root / key
            ts0 = state.targets.get(key)
            old_content = doc_path.read_text() if doc_path.exists() else ""
            # #77:材料集变更与显式 re-step 都按全量 digests 重综合,但只有后者
            # 表示用户确认把超长旧 understanding 迁移为有界正文。
            materials_changed = ts0 is not None and ts0.reason == "materials-changed"
            explicit_recompose = (
                ts0 is not None and ts0.reason == REASON_EXPLICIT_RECOMPOSE
            )
            full_recompose = materials_changed or explicit_recompose
            if not full_recompose and (
                ts0 and doc_path.exists() and _hash(old_content) != ts0.output_hash
            ):
                # 检测到手改 → 暂停该文档,不静默覆盖(D-status manual-edit)
                ts0.status = "blocked"
                ts0.reason = "manual-edit"
                ts0.retry_reason = None
                state.targets[key] = ts0
                return
            if (
                key == "understanding.md"
                and len(old_content) > UNDERSTANDING_MAX_CHARS
                and not explicit_recompose
            ):
                ts = ts0 or TargetState(
                    depends_on=list(target.depends_on),
                    output_hash=_hash(old_content),
                )
                ts.status = "blocked"
                ts.reason = REASON_COMPOSE_MIGRATION_REQUIRED
                ts.diagnostic = None
                ts.retry_reason = None
                state.targets[key] = ts
                return
            current = "" if full_recompose else old_content
            use_delta = dict(all_digests) if full_recompose else delta
            # #153:材料目录 + 授读;当前文档与 Δdigest 必读,corpus 按需。不内联正文。
            corpus_refs = corpus.collect(self.ws)
            has_corpus = bool(corpus_refs)
            materials: list[CatalogItem] = []
            if current and doc_path.is_file():
                materials.append(
                    CatalogItem(
                        rel_path=key,
                        abs_path=doc_path,
                        role="target",
                        origin="folded",
                        required=True,
                        size=item_size(doc_path),
                    )
                )
            for dep in target.depends_on:
                dep_path = self.ws.root / dep
                if dep_path.is_file():
                    materials.append(
                        CatalogItem(
                            rel_path=dep,
                            abs_path=dep_path,
                            role="upstream",
                            origin="target",
                            required=True,
                            size=item_size(dep_path),
                        )
                    )
            for p in sorted(use_delta):
                rec = self._member_digests[p]
                abs_p = rec.digest_path
                assert abs_p is not None
                materials.append(
                    CatalogItem(
                        rel_path=(
                            f"references/{rec.id}/digest.md"
                            if rec.home == self.ws.root.name
                            else f"references/{rec.id}@{rec.home or 'global'}/digest.md"
                        ),
                        abs_path=abs_p,
                        role="digest",
                        origin="digest",
                        required=True,
                        size=item_size(abs_p),
                    )
                )
            for cr in corpus_refs:
                rel = (
                    str(cr.path.relative_to(self.ws.root))
                    if cr.path.is_relative_to(self.ws.root)
                    else cr.path.name
                )
                materials.append(
                    CatalogItem(
                        rel_path=rel,
                        abs_path=cr.path,
                        role="corpus" if cr.kind == "file" else "corpus_tree",
                        origin="corpus",
                        required=False,
                        size=item_size(cr.path) if cr.kind == "file" else 0,
                    )
                )
            # #99:来源目录(全量 all_digests)——短 ID 稳定;context 中标注 S-…
            from kairo.provenance import SourceEntry, source_id_for

            used_source_ids: set[str] = set()
            catalog = []
            for ref_key in sorted(all_digests):
                rec = self._member_digests[ref_key]
                catalog.append(
                    SourceEntry(
                        source_id=source_id_for(
                            rec.id if rec.home == self.ws.root.name else ref_key,
                            used_source_ids,
                        ),
                        ref_id=rec.id if rec.home == self.ws.root.name else ref_key,
                        title=rec.title,
                        digest_path=(
                            f"references/{rec.id}/digest.md"
                            if rec.home == self.ws.root.name
                            else f"references/{rec.id}@{rec.home or 'global'}/digest.md"
                        ),
                        digest_hash=all_digests[ref_key],
                    )
                )
            reference_section = (
                corpus.reference_section(self.ws, corpus_refs) if has_corpus else ""
            )
            context = (
                format_catalog(materials)
                + format_source_catalog_block(catalog)
                + f"\n本步必读 Δdigest:{len(use_delta)} 条。\n"
            )
            # 显式 re-step/full compose 必须用本次实际必读 digest；普通运行才是 delta。
            knowledge_text = "\n\n".join(
                self._member_digests[path].digest_path.read_text()
                for path in sorted(use_delta)
                if self._member_digests[path].digest_path is not None
            )
            knowledge_context, knowledge_hash, knowledge_diagnostic = _knowledge_context(self.ws, knowledge_text)
            layer = getattr(target, "layer", None) or "fact"
            budget_discipline = (
                "\n- 完整 `understanding.md`（含标题、空白、正文、来源索引）不得超过 "
                f"{UNDERSTANDING_MAX_CHARS} 个 Unicode 字符；不得截断句子或来源索引。"
                if key == "understanding.md"
                else ""
            )
            try:
                content = _run_agent(
                    self.provider,
                    target.fold_protocol
                    + provenance_protocol_for(layer)
                    + knowledge_context
                    + reference_section
                    + _CATALOG_DISCIPLINE
                    + _OUTPUT_DISCIPLINE
                    + _COMPOSE_DISCIPLINE
                    + budget_discipline,
                    context,
                    "doc.md",
                    catalog_items=materials,
                )
            except Exception as exc:  # #98:不写新正文,保留已有文档,持久化诊断
                import sys

                print(
                    f"Error: provider-failed stage=compose: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
                ts = ts0 or TargetState(depends_on=list(target.depends_on))
                ts.status = "blocked"
                ts.reason = REASON_PROVIDER_FAILED
                ts.diagnostic = make_provider_diagnostic("compose", self.provider, exc)
                ts.retry_reason = (
                    REASON_EXPLICIT_RECOMPOSE
                    if explicit_recompose
                    else "materials-changed" if materials_changed else None
                )
                state.targets[key] = ts
                return
            if (
                key == "understanding.md"
                and len(content) > UNDERSTANDING_MAX_CHARS
            ):
                ts = ts0 or TargetState(depends_on=list(target.depends_on))
                ts.status = "blocked"
                ts.reason = REASON_COMPOSE_OVER_BUDGET
                ts.diagnostic = None
                ts.retry_reason = None
                state.targets[key] = ts
                return
            # #99:判断层 F-… 必须在实际的上游事实文档中声明，不能只符合格式。
            known_fact_ids = None
            if layer == "judgment":
                known_fact_ids = set()
                for dep in target.depends_on:
                    dep_path = self.ws.root / dep
                    if dep_path.is_file():
                        known_fact_ids.update(fact_anchor_ids(dep_path.read_text()))
            # 写盘前溯源结构校验;失败保留旧文,记 compose-provenance-invalid
            prov_errs = validate_provenance(
                content, catalog, layer=layer, known_fact_ids=known_fact_ids
            )
            if prov_errs:
                ts = ts0 or TargetState(depends_on=list(target.depends_on))
                ts.status = "blocked"
                ts.reason = REASON_PROVENANCE_INVALID
                ts.diagnostic = None
                ts.retry_reason = None
                # 不改 output_hash / folded / 文件,便于 re-step 恢复
                state.targets[key] = ts
                return
            # 退化护栏(#28):溯源有效后再判灾难性骤缩;全量重综合沿用既有例外。
            if not full_recompose and is_catastrophic_shrink(current, content):
                ts = ts0 or TargetState(depends_on=list(target.depends_on))
                ts.status = "blocked"
                ts.reason = "compose-degraded"
                ts.diagnostic = None
                ts.retry_reason = None
                state.targets[key] = ts
                return
            doc_path.write_text(content)
            ts = state.targets.get(key) or TargetState(depends_on=list(target.depends_on))
            ts.folded = dict(all_digests)
            ts.output_hash = _hash(content)
            ts.produced_by = {
                "provider": self.provider.name,
                "model": self.provider.model,
            }
            ts.upstream_hash = {
                dep: (state.targets[dep].output_hash if dep in state.targets else "")
                for dep in target.depends_on
            }
            ts.status = "ok"
            ts.reason = None
            ts.diagnostic = None  # 成功清除 #98 诊断
            ts.retry_reason = None
            ts.corpus_stamp = corpus.stamp(corpus_refs)  # 记 corpus 参考层版本戳(advisory)
            ts.glossary_hash = _legacy_glossary_hash(self.ws)
            # 空 delta 不会伪造一次“知识重新校正”。
            if use_delta:
                ts.knowledge_hash = knowledge_hash
                ts.knowledge_diagnostic = knowledge_diagnostic
                ts.knowledge_generation = uuid.uuid4().hex
            # 全量重综合(A)或材料集变更后的重综合 → 刷新漂移基线
            if ts0 is None or full_recompose:
                ts.last_major_folded = dict(all_digests)
            state.targets[key] = ts
            from kairo.knowledge_review import extract_after_success

            # 每个 delta digest 独立保留可定位出处；成功 target 也可提出跨材料变化。
            for digest_path in sorted(use_delta):
                digest_file = self._member_digests[digest_path].digest_path
                if digest_file.is_file():
                    extract_after_success(
                        self.ws.root,
                        self.ws.root.parent,
                        source_kind="compose",
                        path=digest_path,
                        text=digest_file.read_text(),
                        provider=self.provider,
                    )
            if key == "understanding.md":
                extract_after_success(
                    self.ws.root,
                    self.ws.root.parent,
                    source_kind="compose",
                    path=key,
                    text=content,
                    provider=self.provider,
                )

        def is_stale(state: State) -> bool:
            ts = state.targets.get(key)
            doc_path = self.ws.root / key
            # 所有可诊断的 compose 终态均不自动重试;仅显式 run/re-step 恢复。
            if ts and ts.status == "blocked" and ts.reason in (
                "compose-degraded",
                REASON_COMPOSE_MIGRATION_REQUIRED,
                REASON_COMPOSE_OVER_BUDGET,
                REASON_PROVIDER_FAILED,
                REASON_PROVENANCE_INVALID,
            ):
                return False
            # #77 / #161:全量重综合触发
            if ts and ts.reason in ("materials-changed", REASON_EXPLICIT_RECOMPOSE):
                return True
            if (
                ts
                and doc_path.exists()
                and _hash(doc_path.read_text()) != ts.output_hash
            ):
                return ts.status != "blocked"  # 手改未标 blocked → 需标记
            folded = ts.folded if ts else {}
            if any(folded.get(p) != h for p, h in all_digests.items()):
                return True
            return self._upstream_changed(target, state, ts)

        return WorkItem(key, input_hash, run, is_stale)
