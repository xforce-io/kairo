"""流水线规则:ASR / Digest / Compose。

每条规则 `discover()` 扫出待办 WorkItem;engine 用 `is_stale` 判定是否要跑、
`run` 执行副作用(写产物 + 记账)。step 不懂规则干啥,只跑收敛循环。
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from kairo import corpus
from kairo.backends import run_backend
from kairo.machine import resolve_asr
from kairo.models import (
    DEFAULT_EVIDENCE_CARD_PROMPT,
    REASON_PROVIDER_FAILED,
    FailureDiagnostic,
    Form,
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
from kairo.provider import AgentConfig
from kairo.workspace import _slug

# #128:证据卡与主题文档固定预算。按 Unicode code point(len)计。
EVIDENCE_CARD_MAX_CHARS = 2_000
COMPOSE_MAX_CHARS = 20_000
REASON_DIGEST_INVALID = "digest-invalid"
REASON_CARD_INVALID = "card-invalid"
REASON_CARD_OVER_BUDGET = "card-over-budget"
REASON_COMPOSE_OVER_BUDGET = "compose-over-budget"
REASON_COMPOSE_INVALID = "compose-invalid"
_CARD_HEADINGS = ("## 摘要", "## 关键事实", "## 决策", "## 开放问题")
_LEGACY_TRUNCATION_MARKER = "[已截断，详见完整 digest]"

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


def _fold_digests(ws) -> dict[str, str]:
    """可 fold digest 的 path→内容 hash。"""
    out: dict[str, str] = {}
    for ref_id in ws.list_reference_ids():
        man = ws.read_manifest(ref_id)
        sc = ws.constitution.source_classes.get(man.source_class)
        if sc is not None and not sc.fold:
            continue
        path = f"references/{ref_id}/digest.md"
        digest = ws.root / path
        if digest.is_file():
            out[path] = _hash(digest.read_text())
    return out


def _fold_source_catalog(ws):
    """含尚未生成 digest 的全部 fold reference，保证 Digest 阶段 S-id 稳定。"""
    paths: dict[str, str] = {}
    for ref_id in ws.list_reference_ids():
        man = ws.read_manifest(ref_id)
        sc = ws.constitution.source_classes.get(man.source_class)
        if sc is not None and not sc.fold:
            continue
        path = f"references/{ref_id}/digest.md"
        digest = ws.root / path
        paths[path] = _hash(digest.read_text()) if digest.is_file() else ""
    return build_source_catalog(ws, paths)


def _evidence_input_hash(ws, entry, digest_hash: str) -> str:
    return _hash(
        "\n".join(
            (
                DEFAULT_EVIDENCE_CARD_PROMPT,
                entry.source_id,
                entry.ref_id,
                entry.title,
                digest_hash,
                ws.glossary_reference(),
            )
        )
    )


def _evidence_header(entry, digest_hash: str, origin: str) -> str:
    ref_id = entry.ref_id
    title = re.sub(r"\s+", " ", entry.title).strip() or ref_id
    date_match = re.match(r"\d{4}-\d{2}-\d{2}", ref_id)
    date = date_match.group(0) if date_match else "N/A"
    return (
        "# 证据卡\n\n"
        f"- ID: {entry.source_id}\n"
        f"- Reference: {ref_id}\n"
        f"- 标题: {title}\n"
        f"- 日期: {date}\n"
        f"- 来源: {entry.digest_path}\n"
        f"- Digest hash: {digest_hash}\n"
        f"- 生成: {origin}\n"
    )


def _valid_evidence_body(body: str) -> bool:
    return all(heading in body for heading in _CARD_HEADINGS)


def _legacy_excerpt(source: str, budget: int) -> str:
    source = source.strip()
    if len(source) <= budget:
        return source
    suffix = "\n\n" + _LEGACY_TRUNCATION_MARKER
    head = source[: budget - len(suffix)].rstrip()
    boundaries = [m.end() for m in re.finditer(r"\n\n|[。！？]", head)]
    complete = [end for end in boundaries if end >= len(head) // 2]
    if complete:
        head = head[: complete[-1]].rstrip()
    return head + suffix


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


def _write_materials(root: Path, materials: dict[str, str]) -> None:
    """#126:把 compose 主材料写进 artifact_dir;拒绝逃出目录。"""
    root = root.resolve()
    for rel, text in materials.items():
        dest = (root / rel).resolve()
        if dest != root and root not in dest.parents:
            raise ValueError(f"material path escapes artifact_dir:{rel}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)


def _run_agent(
    provider,
    persona: str,
    context: str,
    artifact: str,
    read_dirs=None,
    *,
    timeout_s: int | None = None,
    materials: dict[str, str] | None = None,
) -> str:
    """跑 agent,从隔离 artifact_dir 取回产物内容。写沙箱:artifact-only;
    read_dirs 为额外只读授权目录(corpus 参考层),agent 按需 Read。
    materials 为 compose 主材料(相对路径 → 正文),写入后再 run(#126)。

    #105:timeout_s 默认 DEFAULT_CLI_TIMEOUT_S;传入显式值可覆盖(测试/长任务)。
    """
    from kairo.provider import DEFAULT_CLI_TIMEOUT_S, resolve_cli_timeout

    # timeout_s is None → 默认;显式 int 保留(含短超时测试)
    effective = (
        DEFAULT_CLI_TIMEOUT_S if timeout_s is None else resolve_cli_timeout(timeout_s)
    )
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        if materials:
            _write_materials(root, materials)
        provider.run(
            AgentConfig(
                persona=persona,
                context=context,
                artifact_dir=root,
                model=provider.model,
                artifact=artifact,
                timeout_s=effective,
                read_dirs=list(read_dirs or []),
            )
        )
        path = root / artifact
        if not path.is_file():
            raise RuntimeError(f"provider produced no artifact: {artifact}")
        return path.read_text()


@dataclass
class WorkItem:
    key: str
    input_hash: str
    run: Callable[[State], None]
    is_stale: Callable[[State], bool]


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
        for ref_id in self.ws.list_reference_ids():
            man = self.ws.read_manifest(ref_id)
            sc = self.ws.constitution.source_classes.get(man.source_class)
            if sc is not None and not sc.fold:
                continue  # 基线=路径引用,不派生
            srcs = [f for f in man.forms if f.role in self.consumes]
            if not srcs:
                continue
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
                    keyed = f"references/{ref_id}/{self.produces}.{_slug(Path(src.location).name)}.md"
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

_DIGEST_EVIDENCE_START = "<KAIRO_EVIDENCE>"
_DIGEST_EVIDENCE_END = "</KAIRO_EVIDENCE>"
_DIGEST_BODY_START = "<KAIRO_DIGEST>"
_DIGEST_BODY_END = "</KAIRO_DIGEST>"


def _digest_output_protocol(card_body_limit: int) -> str:
    return f"""

[Digest 双产物输出协议]
一次回答同时产出定长元信息与完整纪要；严格使用下列四个分隔标记，标记外不要输出任何文字：
{_DIGEST_EVIDENCE_START}
## 摘要
...
## 关键事实
...
## 决策
...
## 开放问题
...
{_DIGEST_EVIDENCE_END}
{_DIGEST_BODY_START}
完整高密度记忆纪要
{_DIGEST_BODY_END}
元信息段最多 {card_body_limit} 个 Unicode 字符；完整纪要不受该预算限制。
"""


def _parse_digest_bundle(content: str) -> tuple[str, str] | None:
    def between(start: str, end: str) -> str | None:
        left = content.find(start)
        right = content.find(end, left + len(start)) if left >= 0 else -1
        if left < 0 or right < 0:
            return None
        return content[left + len(start) : right].strip()

    evidence = between(_DIGEST_EVIDENCE_START, _DIGEST_EVIDENCE_END)
    digest = between(_DIGEST_BODY_START, _DIGEST_BODY_END)
    if not evidence or not digest:
        return None
    return evidence, digest


_COMPOSE_DISCIPLINE = (
    "\n- 你只产出当前这一个文档,不要内联其它文档的内容"
    "(例如 understanding 中不要写 assessment 段落)。\n"
    "- 溯源使用来源目录中的短 ID〔S-…〕与文末索引;不要在正文堆叠完整 "
    "`references/.../digest.md` 路径(索引表链接除外)。\n"
    "- 事实层输入是全部定长证据卡,NEW/CHANGED 只是注意力标记,FOLDED 仍是有效证据;"
    "判断层只依据有界上游文档,不要搜索 cwd 或读取原始 digest。\n"
    "- 把材料重新综合成当前完整文档,不是历史百科;禁止输出变更说明、处理过程或差异。\n"
    f"- 完整输出不得超过 {COMPOSE_MAX_CHARS} 个 Unicode 字符。"
)


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
        for ref_id in self.ws.list_reference_ids():
            man = self.ws.read_manifest(ref_id)
            # 源分层:corpus(fold=False)是只读参考层,不规范化(与不 digest 一致)。
            sc = self.ws.constitution.source_classes.get(man.source_class)
            if sc is not None and not sc.fold:
                continue
            roles = {f.role for f in man.forms}
            if "prose" in roles:
                continue
            # 只规范化机器派生的誊录;origin=added 是人给的原文(权威),不碰。
            tf = next(
                (f for f in man.forms if f.role == "transcript" and f.origin != "added"),
                None,
            )
            if tf is None:
                continue
            loc = Path(tf.location)
            p = loc if loc.is_absolute() else self.ws.root / loc
            key = f"references/{ref_id}/prose.md"
            if not (self.ws.root / key).exists():
                items.append(self._make(ref_id, key, p.read_text()))
        return items

    def _make(self, ref_id: str, key: str, body: str) -> WorkItem:
        input_hash = _hash(f"{self.prompt}\n\n---誊录---\n{body}")

        def run(state: State) -> None:
            content = _run_agent(
                self.provider,
                self.prompt
                + self.ws.glossary_reference()
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
            )

        def is_stale(state: State) -> bool:
            ps = state.products.get(key)
            return ps is None or ps.input_hash != input_hash

        return WorkItem(key, input_hash, run, is_stale)


class DigestRule:
    """有正文(transcript/source_text)且无 digest → 产高密度记忆纪要(用 provider)。"""

    def __init__(self, ws, provider) -> None:
        self.ws = ws
        self.provider = provider
        self.prompt = ws.constitution.pipeline.digest.prompt

    def _read_body(self, man) -> str | None:
        chunks: list[str] = []
        for role in self.ws.constitution.body_roles:
            forms = sorted(
                (f for f in man.forms if f.role == role),
                key=lambda f: f.location,
            )
            for f in forms:
                loc = Path(f.location)
                p = loc if loc.is_absolute() else self.ws.root / loc
                if not p.is_file():
                    continue
                try:
                    text = p.read_text()
                except UnicodeDecodeError:
                    continue  # 误标为正文的二进制(如图片)不进 digest 正文,且不崩整条管线
                chunks.append(f"# {p.name}\n\n{text}")
        return "\n\n".join(chunks) if chunks else None

    def discover(self, state: State | None = None) -> list[WorkItem]:
        items: list[WorkItem] = []
        entries = {e.ref_id: e for e in _fold_source_catalog(self.ws)}
        for ref_id in self.ws.list_reference_ids():
            man = self.ws.read_manifest(ref_id)
            # 源分层(#13 v2):fold=False 的类(corpus)是只读参考层,不 digest。
            sc = self.ws.constitution.source_classes.get(man.source_class)
            if sc is not None and not sc.fold:
                continue
            body = self._read_body(man)
            key = f"references/{ref_id}/digest.md"
            if body is not None:
                items.append(self._make(ref_id, key, man, body, entries[ref_id]))
        return items

    def _make(
        self, ref_id: str, key: str, man: Manifest, body: str, entry
    ) -> WorkItem:
        atts = sorted(
            (f for f in man.forms if f.role == "attachment"),
            key=lambda f: f.location,
        )
        fingerprint = f"{self.prompt}\n\n---正文---\n{body}" + "".join(f.hash for f in atts)
        input_hash = _hash(fingerprint)
        ref_dir = self.ws.references_dir() / ref_id
        img_lines = []
        for f in atts:
            loc = Path(f.location)
            p = loc if loc.is_absolute() else self.ws.root / loc
            img_lines.append(str(p))

        def run(state: State) -> None:
            placeholder_header = _evidence_header(entry, "0" * 12, "digest")
            card_body_limit = EVIDENCE_CARD_MAX_CHARS - len(placeholder_header) - 2
            persona = self.prompt + self.ws.glossary_reference()
            if img_lines:
                persona += (
                    "\n\n[现场图片]本会议另有以下图片,请用 Read 工具逐一查看,"
                    "把其中与会议相关的信息(白板/幻灯/截图)并入纪要:\n"
                    + "\n".join(f"- {p}" for p in img_lines)
                )
            persona += _OUTPUT_DISCIPLINE + _digest_output_protocol(card_body_limit)
            try:
                bundle = _run_agent(
                    self.provider,
                    persona,
                    body,
                    "digest.bundle.md",
                    read_dirs=[ref_dir] if img_lines else None,
                )
            except Exception as exc:  # #98:可归属 provider 失败 → 持久化诊断,不写半成品
                import sys

                print(
                    f"Error: provider-failed stage=digest: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
                state.products[key] = ProductState(
                    input_hash=input_hash,
                    status="blocked",
                    reason=REASON_PROVIDER_FAILED,
                    diagnostic=make_provider_diagnostic("digest", self.provider, exc),
                )
                return
            parsed = _parse_digest_bundle(bundle)
            if parsed is None:
                state.products[key] = ProductState(
                    input_hash=input_hash,
                    status="blocked",
                    reason=REASON_DIGEST_INVALID,
                )
                return
            evidence_body, digest = parsed
            digest = digest.rstrip() + "\n"
            digest_hash = _hash(digest)
            evidence_key = f"references/{ref_id}/evidence.md"
            evidence = (
                _evidence_header(entry, digest_hash, "digest")
                + "\n"
                + evidence_body
                + "\n"
            )
            if not _valid_evidence_body(evidence_body):
                state.products[key] = ProductState(
                    input_hash=input_hash,
                    status="blocked",
                    reason=REASON_CARD_INVALID,
                )
                return
            if len(evidence) > EVIDENCE_CARD_MAX_CHARS:
                state.products[key] = ProductState(
                    input_hash=input_hash,
                    status="blocked",
                    reason=REASON_CARD_OVER_BUDGET,
                )
                return
            (self.ws.root / key).write_text(digest)
            (self.ws.root / evidence_key).write_text(evidence)
            produced_by = {
                "provider": self.provider.name,
                "model": self.provider.model,
            }
            state.products[key] = ProductState(
                input_hash=input_hash,
                produced_by=produced_by,
            )
            state.products[evidence_key] = ProductState(
                input_hash=_evidence_input_hash(self.ws, entry, digest_hash),
                produced_by=produced_by,
            )

        def is_stale(state: State) -> bool:
            # input_hash 匹配即收敛(含 #98 provider-failed 终态);hash 变(正文/附件)才重试
            ps = state.products.get(key)
            return ps is None or ps.input_hash != input_hash

        return WorkItem(key, input_hash, run, is_stale)


class LegacyEvidenceRule:
    """#128:仅给旧 digest 零模型补 evidence；新 evidence 已由 DigestRule 同次产出。"""

    def __init__(self, ws) -> None:
        self.ws = ws

    def discover(self, state: State | None = None) -> list[WorkItem]:
        digests = _fold_digests(self.ws)
        by_path = {e.digest_path: e for e in build_source_catalog(self.ws, digests)}
        return [
            self._make(
                by_path[digest_path],
                (self.ws.root / digest_path).read_text(),
                digest_hash,
            )
            for digest_path, digest_hash in sorted(digests.items())
        ]

    def _make(self, entry, digest: str, digest_hash: str) -> WorkItem:
        key = f"references/{entry.ref_id}/evidence.md"
        path = self.ws.root / key
        input_hash = _evidence_input_hash(self.ws, entry, digest_hash)

        def run(state: State) -> None:
            if path.is_file():
                current = path.read_text()
                old_legacy = (
                    "- 生成: legacy-derived" in current
                    and _LEGACY_TRUNCATION_MARKER not in current
                )
                if (
                    not old_legacy
                    and len(current) <= EVIDENCE_CARD_MAX_CHARS
                    and _valid_evidence_body(current)
                    and f"- Digest hash: {digest_hash}" in current
                ):
                    origin_match = re.search(r"(?m)^- 生成: (.+)$", current)
                    origin = origin_match.group(1) if origin_match else "existing"
                    body_at = current.find("## 摘要")
                    refreshed = (
                        _evidence_header(entry, digest_hash, origin)
                        + "\n"
                        + current[body_at:].lstrip()
                    )
                    if len(refreshed) <= EVIDENCE_CARD_MAX_CHARS:
                        path.write_text(refreshed)
                        state.products[key] = ProductState(
                            input_hash=input_hash,
                            produced_by={"provider": "existing", "model": "existing"},
                        )
                        return
            header = _evidence_header(entry, digest_hash, "legacy-derived")
            prefix = header + "\n## 摘要\n\n[legacy-derived] "
            suffix = (
                "\n\n## 关键事实\n\n- N/A（旧 digest 兼容摘录）\n\n"
                "## 决策\n\n- N/A（旧 digest 兼容摘录）\n\n"
                "## 开放问题\n\n- N/A（旧 digest 兼容摘录）\n"
            )
            budget = EVIDENCE_CARD_MAX_CHARS - len(prefix) - len(suffix)
            if budget <= 0:
                state.products[key] = ProductState(
                    input_hash=input_hash,
                    status="blocked",
                    reason=REASON_CARD_OVER_BUDGET,
                )
                return
            source = digest.strip()
            first_heading = re.search(r"(?m)^#\s+\S", source)
            if first_heading:
                source = source[first_heading.start() :]
            excerpt = _legacy_excerpt(source, budget)
            path.write_text(prefix + (excerpt or "N/A") + suffix)
            state.products[key] = ProductState(
                input_hash=input_hash,
                produced_by={
                    "provider": "legacy-adapter",
                    "model": "deterministic",
                },
            )

        def is_stale(state: State) -> bool:
            ps = state.products.get(key)
            return ps is None or ps.input_hash != input_hash or (
                ps.status == "ok" and not path.is_file()
            )

        return WorkItem(key, input_hash, run, is_stale)


class ComposeRule:
    """#128:全量定长证据卡 → 有界事实文档 → 有界判断文档。"""

    def __init__(self, ws, provider) -> None:
        self.ws = ws
        self.provider = provider

    def _all_digests(self) -> dict[str, str]:
        return _fold_digests(self.ws)

    def _all_cards(
        self, state: State | None, all_digests: dict[str, str]
    ) -> dict[str, str] | None:
        """全部 digest 都有当前成功 card 才返回 path→hash；否则 compose 门禁关闭。"""
        if state is None:
            return None
        expected = {
            item.key: item.input_hash
            for item in LegacyEvidenceRule(self.ws).discover(state)
        }
        out: dict[str, str] = {}
        for digest_path in all_digests:
            ref_id = digest_path.split("/")[1]
            key = f"references/{ref_id}/evidence.md"
            ps = state.products.get(key)
            path = self.ws.root / key
            if (
                ps is None
                or ps.status != "ok"
                or ps.input_hash != expected.get(key)
                or not path.is_file()
            ):
                return None
            out[key] = _hash(path.read_text())
        return out

    def _upstreams_ready(
        self, target, state: State, all_cards: dict[str, str]
    ) -> bool:
        """判断层只能在所有上游已成功覆盖当前 card 集后运行。"""
        for dep in target.depends_on:
            ts = state.targets.get(dep)
            if (
                ts is None
                or ts.status != "ok"
                or ts.folded != all_cards
                or not (self.ws.root / dep).is_file()
            ):
                return False
        return True

    def corpus_drifted(self, target_path: str, state: State) -> bool:
        """corpus 自该 target 上次折叠后是否变更(advisory;不进 staleness 循环)。"""
        ts = state.targets.get(target_path)
        return ts is not None and ts.corpus_stamp != corpus.stamp(corpus.collect(self.ws))

    def _upstream_changed(self, target, state, ts) -> bool:
        for dep in target.depends_on:
            dep_out = (
                state.targets[dep].output_hash
                if (state and dep in state.targets)
                else ""
            )
            recorded = ts.upstream_hash.get(dep) if ts else None
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
        if state is None:
            return []
        all_digests = self._all_digests()
        all_cards = self._all_cards(state, all_digests)
        if all_cards is None:
            return []
        items: list[WorkItem] = []
        for target in self.ws.constitution.targets:
            if not self._upstreams_ready(target, state, all_cards):
                continue
            ts = state.targets.get(target.path)
            cards_changed = (
                (ts is None and bool(all_cards))
                or (ts is not None and ts.folded != all_cards)
            )
            materials_changed = ts is not None and ts.reason == "materials-changed"
            if (
                cards_changed
                or materials_changed
                or self._upstream_changed(target, state, ts)
                or self._is_edited(target.path, ts)
            ):
                items.append(self._make(target, all_cards, all_digests))
        return items

    def _make(
        self,
        target,
        all_cards: dict[str, str],
        all_digests: dict[str, str],
    ) -> WorkItem:
        key = target.path
        input_hash = _hash("".join(sorted(all_cards.values())))

        def run(state: State) -> None:
            doc_path = self.ws.root / key
            ts0 = state.targets.get(key)
            materials_changed = ts0 is not None and ts0.reason == "materials-changed"
            legacy_folded = bool(
                ts0 and any(path.endswith("/digest.md") for path in ts0.folded)
            )
            if (
                ts0
                and not materials_changed
                and doc_path.exists()
                and _hash(doc_path.read_text()) != ts0.output_hash
            ):
                ts0.status = "blocked"
                ts0.reason = "manual-edit"
                state.targets[key] = ts0
                return

            corpus_refs = corpus.collect(self.ws)
            has_corpus = bool(corpus_refs)
            catalog = build_source_catalog(self.ws, all_digests)
            layer = getattr(target, "layer", None) or "fact"
            layout: dict[str, str] = {}
            inv = [
                "请 Read 清单中的有界材料,重新综合当前完整文档。路径相对 cwd。",
                "",
            ]
            ups = [dep for dep in target.depends_on if (self.ws.root / dep).is_file()]
            if ups:
                inv.append("有界上游:")
                for dep in ups:
                    name = Path(dep).name
                    layout[f"upstream/{name}"] = (self.ws.root / dep).read_text()
                    inv.append(f"- upstream/{name}")
            if layer == "judgment":
                inv.append("判断层只读取上述有界上游,不直接读取 cards 或 digest。")
            else:
                inv.append(f"证据卡({len(catalog)} 张,全部读取):")
                folded = ts0.folded if ts0 else {}
                for entry in catalog:
                    card_key = f"references/{entry.ref_id}/evidence.md"
                    card_hash = all_cards[card_key]
                    prior = folded.get(card_key)
                    status = (
                        "NEW"
                        if prior is None
                        else "FOLDED"
                        if prior == card_hash
                        else "CHANGED"
                    )
                    rel = f"cards/{entry.ref_id}.md"
                    layout[rel] = (
                        f"[状态:{status} | {entry.source_id}]\n"
                        f"{(self.ws.root / card_key).read_text()}"
                    )
                    inv.append(f"- [{status}] {entry.source_id} → {rel}")
            reference_section = (
                corpus.reference_section(self.ws, corpus_refs)
                if has_corpus and layer != "judgment"
                else ""
            )
            read_dirs = (
                corpus.read_dirs(corpus_refs) if layer != "judgment" else []
            )
            context = "\n".join(inv) + format_source_catalog_block(catalog)
            try:
                content = _run_agent(
                    self.provider,
                    target.fold_protocol
                    + provenance_protocol_for(layer)
                    + self.ws.glossary_reference()
                    + reference_section
                    + _OUTPUT_DISCIPLINE
                    + _COMPOSE_DISCIPLINE,
                    context,
                    "doc.md",
                    read_dirs=read_dirs,
                    materials=layout,
                )
            except Exception as exc:
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
                state.targets[key] = ts
                return
            # Grok 未写 artifact 而把执行旁白与正文标题拼在首行时，
            # 从该 H1 起取文档；正文后续材料里的 H1 不碰。
            first_line = content.lstrip().split("\n", 1)[0]
            h1 = re.search(r"(?<!#)#\s+\S", first_line)
            if h1 and first_line[: h1.start()].strip():
                content = content.lstrip()[h1.start() :]
                first_line = content.split("\n", 1)[0]
            if re.search(r"(?<!#)#\s+\S", first_line) and not re.match(
                r"#\s+\S", first_line
            ):
                ts = ts0 or TargetState(depends_on=list(target.depends_on))
                ts.status = "blocked"
                ts.reason = REASON_COMPOSE_INVALID
                ts.diagnostic = None
                state.targets[key] = ts
                return
            if len(content) > COMPOSE_MAX_CHARS:
                ts = ts0 or TargetState(depends_on=list(target.depends_on))
                ts.status = "blocked"
                ts.reason = REASON_COMPOSE_OVER_BUDGET
                ts.diagnostic = None
                state.targets[key] = ts
                return

            known_fact_ids = None
            if layer == "judgment":
                known_fact_ids = set()
                for dep in target.depends_on:
                    dep_path = self.ws.root / dep
                    if dep_path.is_file():
                        known_fact_ids.update(fact_anchor_ids(dep_path.read_text()))
            prov_errs = validate_provenance(
                content, catalog, layer=layer, known_fact_ids=known_fact_ids
            )
            missing_sources = [
                entry.source_id for entry in catalog if entry.source_id not in content
            ]
            if missing_sources:
                prov_errs.append(
                    "document missing evidence cards: " + ", ".join(missing_sources)
                )
            if prov_errs:
                ts = ts0 or TargetState(depends_on=list(target.depends_on))
                ts.status = "blocked"
                ts.reason = REASON_PROVENANCE_INVALID
                ts.diagnostic = None
                state.targets[key] = ts
                return

            doc_path.write_text(content)
            ts = state.targets.get(key) or TargetState(depends_on=list(target.depends_on))
            ts.folded = dict(all_cards)
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
            ts.diagnostic = None
            ts.corpus_stamp = corpus.stamp(corpus_refs)
            if ts0 is None or materials_changed or legacy_folded:
                ts.last_major_folded = dict(all_cards)
            state.targets[key] = ts

        def is_stale(state: State) -> bool:
            ts = state.targets.get(key)
            doc_path = self.ws.root / key
            if ts and ts.status == "blocked" and ts.reason in (
                "compose-degraded",  # 旧 state 兼容；须显式 re-step
                REASON_PROVIDER_FAILED,
                REASON_PROVENANCE_INVALID,
                REASON_COMPOSE_OVER_BUDGET,
                REASON_COMPOSE_INVALID,
            ):
                return False
            if ts and ts.reason == "materials-changed":
                return True
            if (
                ts
                and doc_path.exists()
                and _hash(doc_path.read_text()) != ts.output_hash
            ):
                return ts.status != "blocked"
            if ts is None or ts.folded != all_cards:
                return True
            return self._upstream_changed(target, state, ts)

        return WorkItem(key, input_hash, run, is_stale)
