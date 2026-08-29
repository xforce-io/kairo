from __future__ import annotations

from typer.testing import CliRunner

from kairo.cli import app
from kairo.engine import step
from kairo.models import TargetState
from kairo.provider import AgentResult, StubProvider, _scan_artifacts
from kairo.rules import (
    REASON_COMPOSE_MIGRATION_REQUIRED,
    REASON_COMPOSE_OVER_BUDGET,
    UNDERSTANDING_MAX_CHARS,
    DigestRule,
    _hash,
)
from kairo.engine import workspace_run_plan
from kairo.workspace import Workspace


def _add_pending_digest(ws: Workspace, tmp_path, name: str = "new.txt") -> None:
    source = tmp_path / name
    source.write_text("新增的确定事实")
    ref_id = ws.add([source])
    state = ws.read_state()
    item = next(
        item
        for item in DigestRule(ws, StubProvider()).discover(state)
        if item.key == f"references/{ref_id}/digest.md"
    )
    item.run(state)
    ws.write_state(state)


def _workspace_with_prior(tmp_path) -> Workspace:
    ws = Workspace.init(tmp_path / "ws", topic="bounded")
    source = tmp_path / "base.txt"
    source.write_text("既有事实")
    ws.add([source])
    step(ws, StubProvider())
    _add_pending_digest(ws, tmp_path)
    return ws


class _LongProvider:
    name = "long"
    model = "long"
    supports_read_dirs = True

    def run(self, config, signal=None):
        config.artifact_dir.mkdir(parents=True, exist_ok=True)
        content = "x" * (UNDERSTANDING_MAX_CHARS + 1)
        path = config.artifact_dir / (config.artifact or "output.md")
        path.write_text(content)
        return AgentResult(artifacts=_scan_artifacts(config.artifact_dir))


def test_cli_status_and_run_leftover_oversized_degraded(tmp_path, monkeypatch):
    """#176:status/run 把超长 leftover compose-degraded 当成既有迁移门禁。"""
    ws = Workspace.init(tmp_path / "ws", topic="leftover")
    old = "旧历史" * (UNDERSTANDING_MAX_CHARS // 3 + 1)
    understanding = ws.root / "understanding.md"
    understanding.write_text(old)
    state = ws.read_state()
    state.targets["understanding.md"] = TargetState(
        output_hash=_hash(old),
        folded={},
        status="blocked",
        reason="compose-degraded",
    )
    ws.write_state(state)
    monkeypatch.chdir(ws.root)
    monkeypatch.setenv("KAIRO_STUB", "1")

    status = CliRunner().invoke(app, ["status"])
    assert status.exit_code == 0, status.output
    assert REASON_COMPOSE_MIGRATION_REQUIRED in status.output
    assert "re-step understanding.md" in status.output
    assert "compose-degraded" not in status.output
    assert understanding.read_text() == old

    prior = understanding.read_bytes()
    gated = CliRunner().invoke(app, ["run"])
    assert gated.exit_code == 1, gated.output
    assert REASON_COMPOSE_MIGRATION_REQUIRED in gated.output
    assert "re-step understanding.md" in gated.output
    assert understanding.read_bytes() == prior
    ts = ws.read_state().targets["understanding.md"]
    assert ts.reason == REASON_COMPOSE_MIGRATION_REQUIRED
    assert ts.status == "blocked"
    plan = workspace_run_plan(ws)
    assert plan["blocked_targets"][0]["reason"] == REASON_COMPOSE_MIGRATION_REQUIRED


def test_cli_run_gates_legacy_document_then_re_step_migrates(
    tmp_path, monkeypatch
):
    ws = _workspace_with_prior(tmp_path)
    old = "旧历史" * (UNDERSTANDING_MAX_CHARS // 3 + 1)
    understanding = ws.root / "understanding.md"
    understanding.write_text(old)
    assessment = ws.root / "assessment.md"
    assessment.write_text("停更判断哨兵")
    state = ws.read_state()
    state.targets["understanding.md"].output_hash = _hash(old)
    folded = dict(state.targets["understanding.md"].folded)
    ws.write_state(state)
    monkeypatch.chdir(ws.root)
    monkeypatch.setenv("KAIRO_STUB", "1")

    gated = CliRunner().invoke(app, ["run"])

    assert gated.exit_code == 1
    assert REASON_COMPOSE_MIGRATION_REQUIRED in gated.output
    assert understanding.read_text() == old
    assert ws.read_state().targets["understanding.md"].folded == folded

    migrated = CliRunner().invoke(app, ["re-step", "understanding.md"])

    assert migrated.exit_code == 0, migrated.output
    assert len(understanding.read_text()) <= UNDERSTANDING_MAX_CHARS
    assert ws.read_state().targets["understanding.md"].status == "ok"
    assert assessment.read_text() == "停更判断哨兵"


def test_cli_mixed_pending_and_attention_runs_pending_but_exits_nonzero(
    tmp_path, monkeypatch
):
    ws = Workspace.init(tmp_path / "ws", topic="mixed")
    understanding = ws.root / "understanding.md"
    understanding.write_text("旧正文")
    state = ws.read_state()
    state.targets["understanding.md"] = TargetState(
        output_hash=_hash("旧正文"),
        status="blocked",
        reason=REASON_COMPOSE_MIGRATION_REQUIRED,
    )
    ws.write_state(state)
    source = tmp_path / "pending.txt"
    source.write_text("待处理事实")
    ref_id = ws.add([source])
    monkeypatch.chdir(ws.root)
    monkeypatch.setenv("KAIRO_STUB", "1")

    result = CliRunner().invoke(app, ["run"])

    assert result.exit_code == 1
    assert (ws.root / f"references/{ref_id}/digest.md").is_file()
    assert ws.read_state().targets["understanding.md"].reason == (
        REASON_COMPOSE_MIGRATION_REQUIRED
    )


def test_cli_over_budget_keeps_document_and_folded(tmp_path, monkeypatch):
    ws = _workspace_with_prior(tmp_path)
    understanding = ws.root / "understanding.md"
    old = understanding.read_text()
    folded = dict(ws.read_state().targets["understanding.md"].folded)
    monkeypatch.chdir(ws.root)
    monkeypatch.delenv("KAIRO_STUB", raising=False)
    monkeypatch.setattr("kairo.cli.select_provider", lambda **_: _LongProvider())

    result = CliRunner().invoke(app, ["run"])

    assert result.exit_code == 1
    assert REASON_COMPOSE_OVER_BUDGET in result.output
    assert understanding.read_text() == old
    state = ws.read_state()
    assert state.targets["understanding.md"].folded == folded
    assert state.targets["understanding.md"].reason == REASON_COMPOSE_OVER_BUDGET
