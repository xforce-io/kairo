"""#146 journal kind:无 U/A fold;digest/compose 阶段关闭;回顾不进原料。"""

import datetime as dt

from kairo.engine import pending, workspace_run_plan
from kairo.kind import KIND_JOURNAL, KIND_TOPIC, effective_kind, resolve_kind, stage_enabled
from kairo.provider import AgentConfig, AgentResult
from kairo.review import produce_review
from kairo.workspace import Workspace


def test_resolve_kind_summary_topic_is_journal():
    assert resolve_kind(None, "总结") == KIND_JOURNAL
    assert resolve_kind("topic", "总结") == KIND_JOURNAL
    assert resolve_kind("journal", "能源梳理") == KIND_JOURNAL
    assert resolve_kind(None, "能源梳理") == KIND_TOPIC


def test_init_summary_workspace_has_no_ua_targets(tmp_path):
    journal = Workspace.init(tmp_path / "总结", topic="总结")
    assert journal.constitution.kind == KIND_JOURNAL
    assert journal.constitution.targets == []
    assert journal.constitution.review_input is False
    assert journal.constitution.pipeline.digest.enabled is False
    topic = Workspace.init(tmp_path / "能源", topic="能源梳理")
    assert topic.constitution.kind == KIND_TOPIC
    assert [t.path for t in topic.constitution.targets] == ["understanding.md"]


def test_open_existing_总结_is_journal_without_kind_field(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = Workspace.init(tmp_path / "总结", topic="能源梳理")
    con = ws.constitution
    con.topic = "总结"
    con.kind = "topic"
    ws.write_constitution(con)
    opened = Workspace.open(ws.root)
    assert effective_kind(opened) == KIND_JOURNAL
    assert [t.path for t in opened.constitution.targets] == ["understanding.md"]
    assert opened.constitution.live_targets() == []
    assert not stage_enabled(opened, "digest")
    assert not stage_enabled(opened, "compose")
    src = tmp_path / "a.txt"
    src.write_text("回顾正文")
    opened.add([src], ref_id="note", occurred_at="2026-08-20")
    assert pending(opened) == []


def test_open_leftover_总结_yaml_without_kind_key_has_empty_live_targets(tmp_path):
    import yaml

    ws = Workspace.init(tmp_path / "总结", topic="能源梳理")
    data = yaml.safe_load((ws.root / "constitution.yaml").read_text())
    data.pop("kind", None)
    data["topic"] = "总结"
    (ws.root / "constitution.yaml").write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    opened = Workspace.open(ws.root)
    raw = yaml.safe_load((opened.root / "constitution.yaml").read_text())
    assert "kind" not in raw
    assert [t.path for t in opened.constitution.targets] == ["understanding.md"]
    assert opened.constitution.live_targets() == []
    assert effective_kind(opened) == KIND_JOURNAL


def test_journal_pending_skips_digest_and_compose(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    journal = Workspace.init(tmp_path / "总结", topic="总结")
    src = tmp_path / "a.txt"
    src.write_text("回顾正文")
    journal.add([src], ref_id="note", occurred_at="2026-08-20")
    assert pending(journal) == []
    plan = workspace_run_plan(journal)
    assert plan["pending_count"] == 0

    topic = Workspace.init(tmp_path / "能源", topic="能源")
    topic.add([src], ref_id="meet", occurred_at="2026-08-20")
    keys = {i.key for i in pending(topic)}
    assert any(k.endswith("digest.md") for k in keys)
    assert not any(k.endswith("assessment.md") for k in keys)


def test_produce_review_excludes_journal_kind(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    root = tmp_path / "root"
    root.mkdir()
    alpha = Workspace.init(root / "alpha", topic="能源梳理")
    journal = Workspace.init(root / "回顾仓", topic="回顾仓", kind="journal")
    dest = Workspace.init(root / "dest", topic="dest")
    (tmp_path / "m.txt").write_text("周会")
    (tmp_path / "j.txt").write_text("旧刊")
    rid = alpha.add([tmp_path / "m.txt"], ref_id="meet", occurred_at="2026-08-20")
    (alpha.references_dir() / rid / "digest.md").write_text("课题DIGEST")
    jid = journal.add([tmp_path / "j.txt"], ref_id="old", occurred_at="2026-08-20")
    (journal.references_dir() / jid / "digest.md").write_text("JOURNALSECRET")
    captured = {}

    class Cap:
        name = "cap"
        model = "cap"

        def run(self, config: AgentConfig, signal=None):
            captured["context"] = config.context
            texts = []
            digest_root = config.artifact_dir / "digest"
            if digest_root.is_dir():
                texts = [p.read_text() for p in digest_root.rglob("*.md")]
            captured["plated"] = texts
            (config.artifact_dir / "review.md").write_text("# 回顾\n课题DIGEST\n")
            return AgentResult(result_text="ok")

    produce_review(
        root, dest, dt.date(2026, 8, 20), dt.date(2026, 8, 20), provider=Cap()
    )
    blob = captured["context"] + "\n".join(captured["plated"])
    assert "JOURNALSECRET" not in blob
    assert "课题DIGEST" in "\n".join(captured["plated"])
    assert "课题DIGEST" not in captured["context"]
