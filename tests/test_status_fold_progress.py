"""#401 status 待处理／已融入／增量／--ref 四态。"""

from __future__ import annotations

import json

from typer.testing import CliRunner

import pytest

from kairo.cli import app
from kairo.refs import create_tag
from kairo.workspace import Workspace

runner = CliRunner()


@pytest.fixture
def topic_dir(tmp_path):
    create_tag(tmp_path, "main")
    d = tmp_path / "main"
    d.mkdir()
    return d


def _load(result):
    assert result.exit_code == 0, result.output + result.stderr
    return json.loads(result.stdout)


def _six_folded(topic_dir, monkeypatch):
    monkeypatch.chdir(topic_dir)
    monkeypatch.setenv("KAIRO_STUB", "1")
    assert runner.invoke(app, ["init"]).exit_code == 0
    for i in range(6):
        p = topic_dir / f"m{i}.txt"
        p.write_text(f"内容{i}")
        assert runner.invoke(app, ["add", str(p)]).exit_code == 0
        assert runner.invoke(app, ["step"]).exit_code == 0
    ws = Workspace.open(topic_dir)
    state = ws.read_state()
    ts = state.targets["understanding.md"]
    keys = list(ts.folded.keys())
    assert len(keys) == 6
    ts.last_major_folded = {k: ts.folded[k] for k in keys[:2]}
    ws.write_state(state)
    ids = []
    for key in keys:
        parts = key.split("/")
        ids.append(parts[1] if len(parts) >= 3 else parts[-2])
    return ids


def test_s1_human_counts(topic_dir, monkeypatch):
    _six_folded(topic_dir, monkeypatch)
    s = runner.invoke(app, ["status"])
    assert s.exit_code == 0, s.output
    text = s.stdout
    assert "已融入 6" in text
    assert "全量综合后已增量融入 4" in text
    assert "待处理 0" in text
    assert "距上次 A" not in text
    assert "stale=" not in text
    pending_line = [ln for ln in text.splitlines() if ln.startswith("topic ")][0]
    assert "待处理 0" in pending_line
    assert "待处理 4" not in text


def test_s2_json_matches_human(topic_dir, monkeypatch):
    _six_folded(topic_dir, monkeypatch)
    human = runner.invoke(app, ["status"])
    payload = _load(runner.invoke(app, ["status", "--json"]))
    assert payload["ok"] is True
    assert payload["pending"] == 0
    assert payload["blocked"] == 0
    assert "stale" not in payload
    und = next(t for t in payload["targets"] if t["path"] == "understanding.md")
    assert und["folded"] == 6
    assert und["incremental_after_full_compose"] == 4
    assert f"已融入 {und['folded']}" in human.stdout
    assert f"全量综合后已增量融入 {und['incremental_after_full_compose']}" in human.stdout
    assert f"待处理 {payload['pending']}" in human.stdout
    assert f"blocked {payload['blocked']}" in human.stdout


def test_s2_pending_and_blocked_classes(topic_dir, monkeypatch):
    monkeypatch.chdir(topic_dir)
    monkeypatch.setenv("KAIRO_STUB", "1")
    runner.invoke(app, ["init"])
    p = topic_dir / "m.txt"
    p.write_text("内容")
    runner.invoke(app, ["add", str(p)])
    pending = _load(runner.invoke(app, ["status", "--json"]))
    assert pending["pending"] > 0
    human = runner.invoke(app, ["status"])
    assert f"待处理 {pending['pending']}" in human.stdout
    runner.invoke(app, ["step"])
    (topic_dir / "understanding.md").write_text("手改")
    runner.invoke(app, ["step"])
    blocked = _load(runner.invoke(app, ["status", "--json"]))
    assert blocked["blocked"] > 0
    assert blocked["blocked_reasons"]
    assert any(item.get("reason") for item in blocked["blocked_reasons"])
    blocked_human = runner.invoke(app, ["status"])
    assert f"blocked {blocked['blocked']}" in blocked_human.stdout
    assert "⚠ blocked:" in blocked_human.stdout


def test_s3_four_states(topic_dir, monkeypatch):
    ids = _six_folded(topic_dir, monkeypatch)
    current_id, stale_id, missing_id = ids[0], ids[1], ids[2]
    digest = topic_dir / "references" / stale_id / "digest.md"
    digest.write_text(digest.read_text() + "\n改")
    (topic_dir / "references" / missing_id / "digest.md").unlink()
    extra = topic_dir / "fresh.txt"
    extra.write_text("新")
    add = runner.invoke(app, ["add", str(extra)])
    assert add.exit_code == 0, add.output
    ws = Workspace.open(topic_dir)
    fresh_id = [rid for rid in ws.list_reference_ids() if rid not in ids][0]
    (topic_dir / "references" / fresh_id / "digest.md").parent.mkdir(parents=True, exist_ok=True)
    (topic_dir / "references" / fresh_id / "digest.md").write_text("未折")

    def one(ref, expect):
        human = runner.invoke(app, ["status", "--ref", ref])
        assert human.exit_code == 0, human.output
        payload = _load(runner.invoke(app, ["status", "--ref", ref, "--json"]))
        assert payload["state"] == expect
        assert payload["id"] == ref
        assert payload["home"] == "main"
        assert payload["target"] == "understanding.md"
        labels = {
            "folded_current": "当前 digest 已融入",
            "not_folded": "尚未融入",
            "folded_stale": "更新后未再融入",
            "digest_missing": "digest 未生成",
        }
        assert labels[expect] in human.stdout
        return payload

    one(current_id, "folded_current")
    one(stale_id, "folded_stale")
    one(missing_id, "digest_missing")
    one(fresh_id, "not_folded")
    stale_human = runner.invoke(app, ["status", "--ref", stale_id])
    assert "当前 digest 已融入" not in stale_human.stdout


def test_s3_missing_ref_json(topic_dir, monkeypatch):
    monkeypatch.chdir(topic_dir)
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["status", "--ref", "no-such", "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["code"] == "not_found"
    assert "content" not in payload
