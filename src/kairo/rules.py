"""流水线规则:ASR / Digest / Compose。

每条规则 `discover()` 扫出待办 WorkItem;engine 用 `is_stale` 判定是否要跑、
`run` 执行副作用(写产物 + 记账)。step 不懂规则干啥,只跑收敛循环。
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from kairo import corpus
from kairo.backends import run_backend
from kairo.machine import resolve_asr
from kairo.models import Form, Manifest, ProductState, State, TargetState
from kairo.provider import AgentConfig
from kairo.workspace import _slug


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _run_agent(
    provider, persona: str, context: str, artifact: str, read_dirs=None
) -> str:
    """跑 agent,从隔离 artifact_dir 取回产物内容。写沙箱:artifact-only;
    read_dirs 为额外只读授权目录(corpus 参考层),agent 按需 Read。"""
    with tempfile.TemporaryDirectory() as d:
        provider.run(
            AgentConfig(
                persona=persona,
                context=context,
                artifact_dir=Path(d),
                model=provider.model,
                artifact=artifact,
                read_dirs=list(read_dirs or []),
            )
        )
        return (Path(d) / artifact).read_text()


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
    markitdown 转换失败 convert-failed。corpus(fold=False)是只读参考层,不派生(跳过)。
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
                continue
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

_COMPOSE_DISCIPLINE = (
    "\n- 你只产出当前这一个文档,不要内联其它文档的内容"
    "(例如 understanding 中不要写 assessment 段落)。\n"
    "- 正文中的 [来源:...] 是溯源标签,不是磁盘文件路径,无需也不应去读取。\n"
    "- 你必须输出当前文档的**完整全文**(含未改动章节);即使本轮判断无需演进,"
    "也要原样重述全文,禁止只输出「为何不改」的变更说明或差异摘要。"
)

# 退化护栏(#28):上一版充分长却被骤缩覆盖 → 极可能是 agent 吐了变更说明而非全文。
# 阈值保守,仅拦灾难性缩水;正常的重组/修正/推翻不会触发。
_COMPOSE_MIN_PRIOR_LEN = 2000
_COMPOSE_DEGRADE_RATIO = 0.5


class NormalizeRule:
    """ASR 派生的誊录(机器转写,有噪声)→ 规范化可读全文 prose(用 provider)。

    可选档案层(constitution.pipeline.normalize.enabled,默认关):prose 只给人读,
    不进 digest 路径——digest 恒从 transcript(信息上界),故无二次有损、无需护栏(#33)。
    只碰机器派生的 transcript(origin≠added);人提供的原文与 corpus 不碰。
    """

    def __init__(self, ws, provider) -> None:
        self.ws = ws
        self.provider = provider
        self.enabled = ws.constitution.pipeline.normalize.enabled
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
                + self.ws.constitution.glossary_reference()
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
    """有正文(transcript/source_text)且无 digest → 产忠实纪要(用 provider)。"""

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
                if p.is_file():
                    chunks.append(f"# {p.name}\n\n{p.read_text()}")
        return "\n\n".join(chunks) if chunks else None

    def discover(self, state: State | None = None) -> list[WorkItem]:
        items: list[WorkItem] = []
        for ref_id in self.ws.list_reference_ids():
            man = self.ws.read_manifest(ref_id)
            # 源分层(#13 v2):fold=False 的类(corpus)是只读参考层,不 digest。
            sc = self.ws.constitution.source_classes.get(man.source_class)
            if sc is not None and not sc.fold:
                continue
            body = self._read_body(man)
            key = f"references/{ref_id}/digest.md"
            if body is not None:
                items.append(self._make(ref_id, key, man, body))
        return items

    def _make(self, ref_id: str, key: str, man: Manifest, body: str) -> WorkItem:
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
            persona = self.prompt + self.ws.constitution.glossary_reference()
            if img_lines:
                persona += (
                    "\n\n[现场图片]本会议另有以下图片,请用 Read 工具逐一查看,"
                    "把其中与会议相关的信息(白板/幻灯/截图)并入纪要:\n"
                    + "\n".join(f"- {p}" for p in img_lines)
                )
            persona += _OUTPUT_DISCIPLINE
            content = _run_agent(
                self.provider,
                persona,
                body,
                "digest.md",
                read_dirs=[ref_dir] if img_lines else None,
            )
            (self.ws.root / key).write_text(content)
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

    def _is_fold_class(self, source_class: str) -> bool:
        """该类源是否折叠进 target(fold=True);fold=False 为只读参考层(corpus)。"""
        sc = self.ws.constitution.source_classes.get(source_class)
        return sc is None or sc.fold

    def _all_digests(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for ref_id in self.ws.list_reference_ids():
            # 源分层(#13 v2):只折叠 fold=True 的源;corpus(参考层)的 digest 不计入。
            if not self._is_fold_class(self.ws.read_manifest(ref_id).source_class):
                continue
            d = self.ws.root / f"references/{ref_id}/digest.md"
            if d.exists():
                out[f"references/{ref_id}/digest.md"] = _hash(d.read_text())
        return out

    # ---- 源分层(#13 v2):fold 类(stream)折叠;非 fold 类(corpus)作只读参考层 ----

    def _delta_classes(self, delta: dict[str, str]) -> dict[str, str]:
        """每条 delta digest path 映射到其 reference 的 class(均为 fold 类)。"""
        out: dict[str, str] = {}
        for p in delta:
            ref_id = p.split("/")[1]  # references/<id>/digest.md
            out[p] = self.ws.read_manifest(ref_id).source_class
        return out

    def _fold_label(self, cls: str) -> str:
        """fold 块的来源标签加 ·标签(如 ·观测);仅当存在 corpus 参考层时调用。"""
        sc = self.ws.constitution.source_classes.get(cls)
        return f" ·{sc.label if sc else cls}"

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
        all_digests = self._all_digests()
        items: list[WorkItem] = []
        for target in self.ws.constitution.targets:
            ts = state.targets.get(target.path) if state else None
            folded = ts.folded if ts else {}
            delta = {p: h for p, h in all_digests.items() if folded.get(p) != h}
            if (
                delta
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
            if (
                ts0
                and doc_path.exists()
                and _hash(doc_path.read_text()) != ts0.output_hash
            ):
                # 检测到手改 → 暂停该文档,不静默覆盖(D-status manual-edit)
                ts0.status = "blocked"
                ts0.reason = "manual-edit"
                state.targets[key] = ts0
                return
            current = doc_path.read_text() if doc_path.exists() else ""
            upstream_blocks = [
                f"---上游 {dep}---\n{(self.ws.root / dep).read_text()}"
                for dep in target.depends_on
                if (self.ws.root / dep).exists()
            ]
            # 源分层(#13 v2):corpus(fold=False)作只读参考层,不进 context 折叠块;
            # 经 read_dirs 授读 + persona 列出文件,agent 按需 Read 校正/锚定。
            # 存在参考层时,fold 块(stream)标 ·观测 提示需对基线校准;无 corpus 时与今天一致。
            corpus_refs = corpus.collect(self.ws)
            has_corpus = bool(corpus_refs)
            classes = self._delta_classes(delta)
            digest_blocks = [
                f"[来源:{p}{self._fold_label(classes[p]) if has_corpus else ''}]\n"
                f"{(self.ws.root / p).read_text()}"
                for p in sorted(delta)
            ]
            reference_section = (
                corpus.reference_section(self.ws, corpus_refs) if has_corpus else ""
            )
            read_dirs = corpus.read_dirs(corpus_refs)
            context = (
                f"---当前文档---\n{current}\n\n"
                + ("\n\n".join(upstream_blocks) + "\n\n" if upstream_blocks else "")
                + f"---新增 digest({len(delta)} 条,批量融入)---\n"
                + "\n\n".join(digest_blocks)
            )
            content = _run_agent(
                self.provider,
                target.fold_protocol
                + self.ws.constitution.glossary_reference()
                + reference_section
                + _OUTPUT_DISCIPLINE
                + _COMPOSE_DISCIPLINE,
                context,
                "doc.md",
                read_dirs=read_dirs,
            )
            # 退化护栏(#28):有充分长的上一版,新输出却骤缩 → 不覆盖,标 blocked,
            # 保留旧文档(避免单次 LLM 退化输出静默销毁整篇)。需人工 re-step 重综合。
            if (
                len(current) > _COMPOSE_MIN_PRIOR_LEN
                and len(content) < _COMPOSE_DEGRADE_RATIO * len(current)
            ):
                ts = ts0 or TargetState(depends_on=list(target.depends_on))
                ts.status = "blocked"
                ts.reason = "compose-degraded"
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
            ts.corpus_stamp = corpus.stamp(corpus_refs)  # 记 corpus 参考层版本戳(advisory)
            if ts0 is None:  # 全量重综合(A)→ 刷新漂移基线
                ts.last_major_folded = dict(all_digests)
            state.targets[key] = ts

        def is_stale(state: State) -> bool:
            ts = state.targets.get(key)
            doc_path = self.ws.root / key
            # compose-degraded 视为终态:不自动重试(否则对退化输出死循环),手动 re-step 重综合。
            if ts and ts.status == "blocked" and ts.reason == "compose-degraded":
                return False
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
