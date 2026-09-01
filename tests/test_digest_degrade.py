"""digest 骤缩护栏:写路径拒绝覆盖,普通 Run 不得 unlink 已保护 digest。"""

from __future__ import annotations

from typer.testing import CliRunner

from kairo.cli import app
from kairo.engine import run_workspace, workspace_run_plan
from kairo.provider import AgentResult, _scan_artifacts
from kairo.rules import (
    REASON_DIGEST_DEGRADED,
    DigestRule,
    _COMPOSE_MIN_PRIOR_LEN,
)
from kairo.workspace import Workspace


class _FixedProvider:
    name = "fixed"
    model = "fixed"
    supports_read_dirs = True

    def __init__(self, content):
        self.content = content
        self.calls = 0

    def run(self, config, signal=None):
        self.calls += 1
        config.artifact_dir.mkdir(parents=True, exist_ok=True)
        (config.artifact_dir / (config.artifact or "output.md")).write_text(self.content)
        return AgentResult(artifacts=_scan_artifacts(config.artifact_dir))


def _blocked_long_digest(tmp_path):
    ws = Workspace.init(tmp_path / "ws", topic="t")
    source = tmp_path / "m.txt"
    source.write_text("会议正文")
    rid = ws.add([source])
    prior = "完整的记忆纪要。" * 400
    assert _COMPOSE_MIN_PRIOR_LEN < len(prior)
    digest = ws.root / "references" / rid / "digest.md"
    digest.write_text(prior)
    state = ws.read_state()
    item = DigestRule(ws, _FixedProvider("短失败说明")).discover(state)[0]
    item.run(state)
    ws.write_state(state)
    return ws, rid, item.key, digest, prior


def test_run_workspace_keeps_digest_degraded_prior(tmp_path):
    """digest-degraded 后普通 run 不得 unlink 已保护的长 digest。"""
    ws, _rid, key, digest, prior = _blocked_long_digest(tmp_path)
    assert digest.read_text() == prior
    assert ws.read_state().products[key].reason == REASON_DIGEST_DEGRADED

    plan = workspace_run_plan(ws)
    assert plan["retryable_blocked_count"] == 0
    assert any(
        not ref["retryable"]
        and any(b["reason"] == REASON_DIGEST_DEGRADED for b in ref["blocks"])
        for ref in plan["blocked_refs"]
    )

    run_workspace(ws, _FixedProvider("run 不得写入的短文"))
    assert digest.is_file()
    assert digest.read_text() == prior
    ps = ws.read_state().products[key]
    assert ps.status == "blocked" and ps.reason == REASON_DIGEST_DEGRADED


def test_cli_run_keeps_digest_degraded_prior(tmp_path, monkeypatch):
    """CLI kairo run 与 run_workspace 同语义:不得清掉 digest-degraded 的长纪要。"""
    ws, _rid, key, digest, prior = _blocked_long_digest(tmp_path)
    prior_bytes = digest.read_bytes()

    monkeypatch.chdir(ws.root)
    monkeypatch.setattr(
        "kairo.cli.select_provider", lambda **_: _FixedProvider("CLI run 不得写入")
    )
    status = CliRunner().invoke(app, ["status"])
    assert status.exit_code == 0, status.output
    assert REASON_DIGEST_DEGRADED in status.output
    assert "⚠" in status.output

    result = CliRunner().invoke(app, ["run"])
    assert digest.is_file()
    assert digest.read_bytes() == prior_bytes
    ps = ws.read_state().products[key]
    assert ps.status == "blocked" and ps.reason == REASON_DIGEST_DEGRADED
    assert result.exit_code == 1, result.output
    assert REASON_DIGEST_DEGRADED in (result.output or "") + (result.stderr or "")
