"""#419：单条 run、单独综合、未折入。证明走 CLI，不用 Web。"""
from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from kairo.cli import app
from kairo.models import TargetState
from kairo.rules import _hash
from kairo.status_view import unfolded_count
from kairo.workspace import Workspace
from test_bounded_understanding import CompactProvider, add_digest

runner = CliRunner()


def _status(monkeypatch, ws):
    monkeypatch.chdir(ws.root)
    human = runner.invoke(app, ["status"])
    payload = json.loads(runner.invoke(app, ["status", "--json"]).stdout)
    target = next(item for item in payload["targets"] if item["path"] == "understanding.md")
    return human, payload, target


def _understanding(ws) -> bytes:
    path = ws.root / "understanding.md"
    return path.read_bytes() if path.is_file() else b""


def test_s1_run_ref_stops_before_understanding(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = Workspace.init(tmp_path / "ws", topic="单条")
    monkeypatch.chdir(ws.root)
    first = tmp_path / "first.txt"
    first.write_text("已有材料")
    ws.add([first])
    assert runner.invoke(app, ["step"]).exit_code == 0
    body = _understanding(ws)
    folded = dict(ws.read_state().targets["understanding.md"].folded)
    _, _, before = _status(monkeypatch, ws)
    second = tmp_path / "second.txt"
    second.write_text("新材料只要纪要")
    ref_id = ws.add([second])
    result = runner.invoke(app, ["run", "--ref", ref_id])
    assert result.exit_code == 0, result.output
    digest = ws.root / "references" / ref_id / "digest.md"
    assert digest.is_file() and digest.read_text().strip()
    assert _understanding(ws) == body
    assert ws.read_state().targets["understanding.md"].folded == folded
    _, _, after = _status(monkeypatch, ws)
    assert after["unfolded"] == before["unfolded"] + 1
    assert f"未折入 {after['unfolded']}" in _status(monkeypatch, ws)[0].stdout
    again = runner.invoke(app, ["run", "--ref", ref_id])
    assert again.exit_code == 0, again.output
    assert _understanding(ws) == body
    assert ws.read_state().targets["understanding.md"].folded == folded
    assert _status(monkeypatch, ws)[2]["unfolded"] == after["unfolded"]


def test_s2_understanding_block_does_not_stop_one_ref(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = Workspace.init(tmp_path / "ws", topic="阻塞")
    monkeypatch.chdir(ws.root)
    add_digest(ws, "OLD")
    assert runner.invoke(app, ["step"]).exit_code == 0
    body = _understanding(ws)
    state = ws.read_state()
    state.targets["understanding.md"].status = "blocked"
    state.targets["understanding.md"].reason = "compose-provenance-invalid"
    ws.write_state(state)
    fresh = tmp_path / "fresh.txt"
    fresh.write_text("阻塞期间仍要转写")
    ref_id = ws.add([fresh])
    result = runner.invoke(app, ["run", "--ref", ref_id])
    assert result.exit_code == 0, result.output
    assert (ws.root / "references" / ref_id / "digest.md").is_file()
    assert _understanding(ws) == body
    ts = ws.read_state().targets["understanding.md"]
    assert ts.status == "blocked" and ts.reason == "compose-provenance-invalid"
    products = ws.read_state().products
    assert all(
        not key.startswith(f"references/{ref_id}/") or item.status != "blocked"
        for key, item in products.items()
    )


def test_run_without_ref_or_conflict_exits_2(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = Workspace.init(tmp_path / "ws")
    monkeypatch.chdir(ws.root)
    bare = runner.invoke(app, ["run"])
    assert bare.exit_code == 2
    assert not (ws.root / "understanding.md").exists()
    both = runner.invoke(app, ["run", "--ref", "x", "--all"])
    assert both.exit_code == 2
    missing = runner.invoke(app, ["run", "--ref", "missing"])
    assert missing.exit_code == 2
    corpus = tmp_path / "base.md"
    corpus.write_text("基线")
    ref_id = ws.add([corpus], source_class="corpus")
    rejected = runner.invoke(app, ["run", "--ref", ref_id])
    assert rejected.exit_code == 2
    assert not (ws.root / "references" / ref_id / "digest.md").exists()


def _bind(monkeypatch, ws, provider):
    monkeypatch.chdir(ws.root)
    monkeypatch.setattr("kairo.cli.select_provider", lambda **_kw: provider)


def test_s3_status_and_understanding_only_success(tmp_path, monkeypatch):
    ws = Workspace.init(tmp_path / "ws", topic="综合")
    add_digest(ws, "A", 100)
    add_digest(ws, "B", 100)
    provider = CompactProvider()
    _bind(monkeypatch, ws, provider)
    human, _payload, target = _status(monkeypatch, ws)
    assert target["unfolded"] == 2
    assert f"未折入 {target['unfolded']}" in human.stdout
    result = runner.invoke(app, ["step", "--understanding-only"])
    assert result.exit_code == 0, result.output
    after = _status(monkeypatch, ws)[2]
    assert after["folded"] == 2
    assert after["unfolded"] == 0
    assert target["unfolded"] - after["unfolded"] == after["folded"]
    assert after["blocked_reason"] is None


@pytest.mark.parametrize("actions,reason", [(["long", "long"], "compose-over-budget"), (["invalid"], "compose-provenance-invalid")])
def test_s3_failure_keeps_unfolded_when_nothing_commits(tmp_path, monkeypatch, actions, reason):
    ws = Workspace.init(tmp_path / "ws")
    add_digest(ws, "ONLY")
    provider = CompactProvider(actions)
    _bind(monkeypatch, ws, provider)
    before_n = _status(monkeypatch, ws)[2]["unfolded"]
    assert before_n > 0
    body = _understanding(ws)
    result = runner.invoke(app, ["step", "--understanding-only"])
    assert result.exit_code not in (0, 2), result.output
    assert _understanding(ws) == body
    human, _payload, target = _status(monkeypatch, ws)
    assert target["unfolded"] == before_n
    assert target["blocked_reason"] == reason
    assert f"未折入 {before_n}" in human.stdout
    assert runner.invoke(app, ["status"]).exit_code == 0
    assert runner.invoke(app, ["status", "--json"]).exit_code == 0


def test_s3_second_batch_failure_keeps_first_commit(tmp_path, monkeypatch):
    ws = Workspace.init(tmp_path / "ws")
    for name in ("B0", "B1", "B2"):
        add_digest(ws, name, 13_000)
    provider = CompactProvider(["ok", "invalid"])
    _bind(monkeypatch, ws, provider)
    before = _status(monkeypatch, ws)[2]["unfolded"]
    assert before == 3
    result = runner.invoke(app, ["step", "--understanding-only"])
    assert result.exit_code not in (0, 2), result.output
    target = _status(monkeypatch, ws)[2]
    written = target["folded"]
    assert written > 0
    assert target["unfolded"] == before - written
    assert target["unfolded"] > 0
    assert target["blocked_reason"] == "compose-provenance-invalid"
    text = _understanding(ws).decode()
    assert "事实：B0" in text or "事实：B1" in text or "事实：B2" in text
    assert "S-deadbeef" not in text


@pytest.mark.parametrize("reason", ["compose-over-budget", "compose-provenance-invalid"])
def test_s3_preexisting_block_does_not_succeed(tmp_path, monkeypatch, reason):
    ws = Workspace.init(tmp_path / "ws")
    add_digest(ws, "HELD")
    (ws.root / "understanding.md").write_text("已有正文\n")
    state = ws.read_state()
    state.targets["understanding.md"] = TargetState(
        output_hash=_hash("已有正文\n"),
        status="blocked",
        reason=reason,
        folded={},
    )
    ws.write_state(state)
    provider = CompactProvider()
    _bind(monkeypatch, ws, provider)
    body = _understanding(ws)
    before = unfolded_count(ws, "understanding.md", ws.read_state())
    assert before > 0
    result = runner.invoke(app, ["step", "--understanding-only"])
    assert result.exit_code not in (0, 2), result.output
    assert not provider.calls
    assert _understanding(ws) == body
    target = _status(monkeypatch, ws)[2]
    assert target["unfolded"] == before
    assert target["blocked_reason"] == reason


def test_unfolded_skips_missing_digest_and_corpus(tmp_path):
    ws = Workspace.init(tmp_path / "ws")
    plain = tmp_path / "plain.txt"
    plain.write_text("还没有纪要")
    ws.add([plain])
    base = tmp_path / "base.md"
    base.write_text("基线")
    ws.add([base], source_class="corpus")
    assert unfolded_count(ws, "understanding.md", ws.read_state()) == 0
    add_digest(ws, "COUNTED", 20)
    assert unfolded_count(ws, "understanding.md", ws.read_state()) == 1


def test_skill_matches_single_material_commands():
    from pathlib import Path

    skill = Path("src/kairo/data/SKILL.md").read_text(encoding="utf-8")
    assert "kairo run --ref" in skill
    assert "kairo step --understanding-only" in skill
    assert "targets[].unfolded" in skill
    assert "无参 `kairo run` 退出码 2" in skill
    assert "授权后 `kairo run`" not in skill
