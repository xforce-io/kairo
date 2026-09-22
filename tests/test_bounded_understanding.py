"""#408：真实 CLI/引擎 + 确定性 provider；不冒充真实模型语义评估。"""
from __future__ import annotations
import re
import pytest
import typer
from typer.testing import CliRunner
from kairo.cli import app
from kairo.cli import _exit_if_run_failed
from kairo.engine import run_workspace, step, workspace_run_plan
from kairo.models import TargetState
from kairo.provider import AgentResult, StubProvider, _scan_artifacts
from kairo.rules import DigestRule, _hash
from kairo.workspace import Workspace


def add_digest(ws, name, size=9_000):
    source = ws.root.parent / f"{name}.txt"
    source.write_text(f"事实：{name}\n" + "材料背景。" * (size // 5))
    rid = ws.add([source])
    state = ws.read_state()
    item = next(it for it in DigestRule(ws, StubProvider()).discover(state)
                if it.key == f"references/{rid}/digest.md")
    item.run(state)
    ws.write_state(state)
    (ws.root / item.key).write_text(source.read_text())
    return item.key


class CompactProvider(StubProvider):
    """读取实际授读文件，保留 fixture 事实与来源，支持指定综合调用故障。"""
    def __init__(self, actions=()):
        self.actions = list(actions)
        self.calls = []

    def run(self, config, signal=None):
        if config.artifact != "doc.md":
            return super().run(config, signal)
        reads, sources = [], {}
        for line in config.context.splitlines():
            c = [v.strip() for v in line.split("|")]
            if len(c) == 8 and c[1] == "必读":
                reads.append((c[2], c[4], (config.artifact_dir / c[5]).read_text()))
            if len(c) == 6 and re.fullmatch(r"S-[0-9a-f]+", c[1]):
                sources[c[4]] = c[1]
        self.calls.append((config, reads))
        action = self.actions.pop(0) if self.actions else "ok"
        if action == "timeout":
            raise TimeoutError("fixture compose timeout")
        facts = []
        for role, path, text in reads:
            for line in text.splitlines():
                if line.startswith("事实："):
                    fact = line if role == "target" else f"{line}〔{sources[path]}〕"
                    if fact not in facts:
                        facts.append(fact)
        body = "# 当前结论\n\n" + "\n".join(facts) + "\n"
        body += "背景仍待后续材料核对。\n" * max(0, (12_100 - len(body)) // len("背景仍待后续材料核对。\n"))
        body += "\n## 来源索引\n| ID | 材料 | 可核对来源 |\n|---|---|---|\n"
        for path, sid in sources.items():
            if any(sid in fact for fact in facts):
                body += f"| {sid} | 材料 | [digest]({path}) |\n"
        if action == "long":
            body = "x" * 20_001
        elif action == "invalid":
            body += "〔S-deadbeef〕"
        elif action == "shrink":
            body = "# 空洞结论\n\n## 来源索引\n"
        config.artifact_dir.mkdir(parents=True, exist_ok=True)
        (config.artifact_dir / "doc.md").write_text(body)
        return AgentResult(artifacts=_scan_artifacts(config.artifact_dir))


def cli_run(ws, provider, monkeypatch):
    """#408 仍测引擎整主题。#419 之后无参 `kairo run` 不再进入这里。"""
    import io
    from contextlib import redirect_stderr, redirect_stdout
    from types import SimpleNamespace

    monkeypatch.chdir(ws.root)
    buf = io.StringIO()
    code = 0
    with redirect_stdout(buf), redirect_stderr(buf):
        try:
            run_workspace(ws, provider)
            _exit_if_run_failed(ws)
        except typer.Exit as exc:
            code = int(exc.exit_code or 0)
    return SimpleNamespace(exit_code=code, output=buf.getvalue())


def test_s1_five_cli_rounds_keep_old_and_new_facts(tmp_path, monkeypatch):
    ws = Workspace.init(tmp_path / "ws", topic="持续综合")
    provider, keys = CompactProvider(), []
    for n in range(5):
        keys.append(add_digest(ws, f"FACT-{n}"))
        result = cli_run(ws, provider, monkeypatch)
        assert result.exit_code == 0, result.output
        text = (ws.root / "understanding.md").read_text()
        assert 12_000 <= len(text) <= 20_000
        for prior in range(n + 1):
            assert re.search(rf"事实：FACT-{prior}〔S-[0-9a-f]+〕", text)
        assert set(ws.read_state().targets["understanding.md"].folded) == set(keys)
        assert workspace_run_plan(ws)["mode"] == "clean"
    assert sum(len((ws.root / k).read_text()) for k in keys) > 20_000
    assert len(provider.calls) == 5
    assert all(sum(r == "digest" for r, _, _ in reads) == 1 for _, reads in provider.calls)


def test_s2_batches_resume_after_second_batch_failure(tmp_path, monkeypatch):
    ws = Workspace.init(tmp_path / "ws", topic="分批恢复")
    keys = [add_digest(ws, f"BATCH-{n}", 13_000) for n in range(3)]
    provider = CompactProvider(["ok", "timeout"])
    failed = cli_run(ws, provider, monkeypatch)
    assert failed.exit_code == 1, failed.output
    st = ws.read_state().targets["understanding.md"]
    completed = set(st.folded)
    assert len(completed) == 1 and st.reason == "provider-failed"
    assert len(provider.calls) == 2 and "batch 2/3" in failed.output
    assert "事实：BATCH-" in (ws.root / "understanding.md").read_text()
    recovered = cli_run(ws, provider, monkeypatch)
    assert recovered.exit_code == 0, recovered.output
    assert set(ws.read_state().targets["understanding.md"].folded) == set(keys)
    later_inputs = {p for _, reads in provider.calls[2:] for r, p, _ in reads if r == "digest"}
    assert not (later_inputs & completed)
    assert len(provider.calls) == 4
    assert all(f"事实：BATCH-{n}" in (ws.root / "understanding.md").read_text() for n in range(3))


@pytest.mark.parametrize("actions,reason,calls", [
    (["long", "long"], "compose-over-budget", 2),
    (["invalid"], "compose-provenance-invalid", 1),
    (["shrink"], "compose-degraded", 1),
    (["timeout"], "provider-failed", 1),
])
def test_s3_invalid_candidates_keep_last_committed_version(tmp_path, monkeypatch, actions, reason, calls):
    ws = Workspace.init(tmp_path / "ws")
    add_digest(ws, "ORIGINAL")
    assert cli_run(ws, CompactProvider(), monkeypatch).exit_code == 0
    old = (ws.root / "understanding.md").read_bytes()
    folded = dict(ws.read_state().targets["understanding.md"].folded)
    add_digest(ws, "NEW")
    provider = CompactProvider(actions)
    result = cli_run(ws, provider, monkeypatch)
    assert result.exit_code == 1, result.output
    assert len(provider.calls) == calls
    assert (ws.root / "understanding.md").read_bytes() == old
    ts = ws.read_state().targets["understanding.md"]
    assert ts.folded == folded and ts.reason == reason
    if reason == "compose-over-budget":
        assert "kairo run" in result.output
        assert workspace_run_plan(ws)["retryable_blocked_count"] == 1
        assert cli_run(ws, CompactProvider(), monkeypatch).exit_code == 0


def test_s3_one_budget_revision_succeeds(tmp_path, monkeypatch):
    ws = Workspace.init(tmp_path / "ws")
    add_digest(ws, "REVISED")
    provider = CompactProvider(["long", "ok"])
    result = cli_run(ws, provider, monkeypatch)
    assert result.exit_code == 0, result.output
    assert len(provider.calls) == 2 and "budget revision 1/1" in result.output
    assert "事实：REVISED" in (ws.root / "understanding.md").read_text()


@pytest.mark.parametrize("reason", ["compose-migration-required", "compose-over-budget", "compose-degraded"])
def test_s4_legacy_capacity_block_without_delta_recovers(tmp_path, monkeypatch, reason):
    ws = Workspace.init(tmp_path / "ws")
    add_digest(ws, "HISTORY")
    assert cli_run(ws, CompactProvider(), monkeypatch).exit_code == 0
    old = (ws.root / "understanding.md").read_text().replace("## 来源索引", "旧历史。" * 5_000 + "\n## 来源索引")
    (ws.root / "understanding.md").write_text(old)
    state = ws.read_state()
    ts = state.targets["understanding.md"]
    ts.output_hash, ts.status, ts.reason = _hash(old), "blocked", reason
    ws.write_state(state)
    provider = CompactProvider()
    assert "kairo run" in CliRunner().invoke(app, ["status"]).output
    result = cli_run(ws, provider, monkeypatch)
    assert result.exit_code == 0, result.output
    assert len(provider.calls) == 1
    text = (ws.root / "understanding.md").read_text()
    assert 10_000 <= len(text) <= 20_000 and "事实：HISTORY" in text
    assert cli_run(ws, provider, monkeypatch).exit_code == 0
    assert len(provider.calls) == 1


def test_s4_capacity_recovery_preserves_manual_edit(tmp_path, monkeypatch):
    ws = Workspace.init(tmp_path / "ws")
    old = "历史正文" * 6_000
    (ws.root / "understanding.md").write_text(old + "人工修订")
    state = ws.read_state()
    state.targets["understanding.md"] = TargetState(output_hash=_hash(old), status="blocked", reason="compose-migration-required")
    ws.write_state(state)
    provider = CompactProvider()
    assert cli_run(ws, provider, monkeypatch).exit_code == 1
    assert not provider.calls
    assert ws.read_state().targets["understanding.md"].reason == "manual-edit"
    assert (ws.root / "understanding.md").read_text() == old + "人工修订"


def test_batch_count_limit_and_oversized_singleton(tmp_path, monkeypatch):
    import kairo.rules as rules
    monkeypatch.setattr(rules, "COMPOSE_BATCH_REFS", 2)
    ws = Workspace.init(tmp_path / "ws")
    for n in range(5):
        add_digest(ws, f"SMALL-{n}", 100)
    p = CompactProvider()
    assert cli_run(ws, p, monkeypatch).exit_code == 0
    assert [sum(r == "digest" for r, _, _ in reads) for _, reads in p.calls] == [2, 2, 1]
    add_digest(ws, "INDIVISIBLE", 25_000)
    assert cli_run(ws, p, monkeypatch).exit_code == 0
    assert len([text for r, _, text in p.calls[-1][1] if r == "digest"][0]) > 24_000


def test_normal_no_delta_has_zero_compose_calls(tmp_path):
    ws = Workspace.init(tmp_path / "ws")
    p = CompactProvider()
    assert step(ws, p) is False
    assert not p.calls


def test_old_uncited_sources_do_not_grow_prompt_catalog(tmp_path, monkeypatch):
    ws = Workspace.init(tmp_path / "ws")
    old_key = add_digest(ws, "OLD")
    p = CompactProvider()
    assert cli_run(ws, p, monkeypatch).exit_code == 0
    # 已整理的结论不再引用该来源；folded 仍如实保留历史加工记录。
    doc = ws.root / "understanding.md"
    text = doc.read_text()
    text = "\n".join(line for line in text.splitlines() if "事实：OLD" not in line and "[digest]" not in line)
    doc.write_text(text)
    state = ws.read_state()
    state.targets["understanding.md"].output_hash = _hash(text)
    ws.write_state(state)
    add_digest(ws, "CURRENT")
    assert cli_run(ws, p, monkeypatch).exit_code == 0
    assert old_key not in p.calls[-1][0].context
    assert old_key in ws.read_state().targets["understanding.md"].folded


def test_capacity_retry_keeps_trigger_after_provider_failure(tmp_path, monkeypatch):
    ws = Workspace.init(tmp_path / "ws")
    add_digest(ws, "OLD")
    assert cli_run(ws, CompactProvider(), monkeypatch).exit_code == 0
    state = ws.read_state()
    ts = state.targets["understanding.md"]
    ts.status, ts.reason = "blocked", "compose-migration-required"
    ws.write_state(state)
    assert cli_run(ws, CompactProvider(["timeout"]), monkeypatch).exit_code == 1
    assert ws.read_state().targets["understanding.md"].retry_reason == "compose-capacity-retry"
    p = CompactProvider()
    assert cli_run(ws, p, monkeypatch).exit_code == 0
    assert len(p.calls) == 1
