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

from kairo.models import Form, ProductState, State, TargetState
from kairo.provider import AgentConfig


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _run_agent(provider, persona: str, context: str, artifact: str) -> str:
    """跑 agent,从隔离 artifact_dir 取回产物内容。sandbox:artifact-only。"""
    with tempfile.TemporaryDirectory() as d:
        provider.run(
            AgentConfig(
                persona=persona,
                context=context,
                artifact_dir=Path(d),
                model=provider.model,
                artifact=artifact,
            )
        )
        return (Path(d) / artifact).read_text()


@dataclass
class WorkItem:
    key: str
    input_hash: str
    run: Callable[[State], None]
    is_stale: Callable[[State], bool]


class AsrRule:
    """有 audio form 且无 transcript form → 产标记占位 transcript(M0 恒 stub)。"""

    def __init__(self, ws) -> None:
        self.ws = ws

    def discover(self, state: State | None = None) -> list[WorkItem]:
        items: list[WorkItem] = []
        for ref_id in self.ws.list_reference_ids():
            roles = {f.role for f in self.ws.read_manifest(ref_id).forms}
            if "audio" in roles and "transcript" not in roles:
                items.append(self._make(ref_id))
        return items

    def _make(self, ref_id: str) -> WorkItem:
        man = self.ws.read_manifest(ref_id)
        audio = next(f for f in man.forms if f.role == "audio")
        key = f"references/{ref_id}/transcript.md"
        input_hash = audio.hash

        def run(state: State) -> None:
            loc = Path(audio.location)
            audio_path = loc if loc.is_absolute() else self.ws.root / loc
            if not audio_path.exists():
                # 源不可达且需重派生(D-source)
                state.products[key] = ProductState(
                    input_hash=input_hash, status="blocked", reason="missing-source"
                )
                return
            if not os.environ.get("KAIRO_STUB"):
                # 真实模式无 ASR 后端(P4 接入);不在假转写上跑 Digest
                state.products[key] = ProductState(
                    input_hash=input_hash, status="blocked", reason="no-asr"
                )
                return
            content = (
                "⚠️ STUB TRANSCRIPT\n"
                f"(audio: {audio.location}, hash: {audio.hash})\n"
                "[stub 占位:无真实 ASR 后端]\n"
            )
            (self.ws.root / key).write_text(content)
            m = self.ws.read_manifest(ref_id)
            m.forms.append(
                Form(
                    role="transcript",
                    location=key,
                    hash=_hash(content),
                    origin=f"asr-from:{audio.hash}",
                )
            )
            self.ws.write_manifest(ref_id, m)
            state.products[key] = ProductState(
                input_hash=input_hash,
                produced_by={"provider": "asr-stub", "model": "stub"},
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
        for role in ("transcript", "source_text"):
            for f in man.forms:
                if f.role == role:
                    loc = Path(f.location)
                    p = loc if loc.is_absolute() else self.ws.root / loc
                    return p.read_text()
        return None

    def discover(self, state: State | None = None) -> list[WorkItem]:
        items: list[WorkItem] = []
        for ref_id in self.ws.list_reference_ids():
            man = self.ws.read_manifest(ref_id)
            body = self._read_body(man)
            key = f"references/{ref_id}/digest.md"
            if body is not None and not (self.ws.root / key).exists():
                items.append(self._make(key, body))
        return items

    def _make(self, key: str, body: str) -> WorkItem:
        input_hash = _hash(f"{self.prompt}\n\n---正文---\n{body}")

        def run(state: State) -> None:
            content = _run_agent(self.provider, self.prompt, body, "digest.md")
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

    def _all_digests(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for ref_id in self.ws.list_reference_ids():
            d = self.ws.root / f"references/{ref_id}/digest.md"
            if d.exists():
                out[f"references/{ref_id}/digest.md"] = _hash(d.read_text())
        return out

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
            digest_blocks = [
                f"[来源:{p}]\n{(self.ws.root / p).read_text()}" for p in sorted(delta)
            ]
            context = (
                f"---当前文档---\n{current}\n\n"
                + ("\n\n".join(upstream_blocks) + "\n\n" if upstream_blocks else "")
                + f"---新增 digest({len(delta)} 条,批量融入)---\n"
                + "\n\n".join(digest_blocks)
            )
            content = _run_agent(self.provider, target.fold_protocol, context, "doc.md")
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
            if ts0 is None:  # 全量重综合(A)→ 刷新漂移基线
                ts.last_major_folded = dict(all_digests)
            state.targets[key] = ts

        def is_stale(state: State) -> bool:
            ts = state.targets.get(key)
            doc_path = self.ws.root / key
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
