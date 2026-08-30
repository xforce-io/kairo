"""#146/#193 journal kind:无 U/A fold;digest 开但排除回顾正文;compose 关;回顾不进原料。"""

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
    assert journal.constitution.pipeline.digest.enabled is True
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
    assert stage_enabled(opened, "digest")
    assert not stage_enabled(opened, "compose")
    src = tmp_path / "a.txt"
    src.write_text("回顾正文")
    opened.add([src], ref_id="note", occurred_at="2026-08-20", role="source_text")
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
    assert stage_enabled(opened, "digest")
    assert not stage_enabled(opened, "compose")


def test_journal_pending_skips_compose_and_bare_review_digest(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    journal = Workspace.init(tmp_path / "总结", topic="总结")
    src = tmp_path / "a.txt"
    src.write_text("回顾正文")
    journal.add([src], ref_id="note", occurred_at="2026-08-20", role="source_text")
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


def test_journal_leftover_yaml_digest_disabled_still_runs_digest(tmp_path):
    journal = Workspace.init(tmp_path / "总结", topic="总结")
    con = journal.constitution
    con.pipeline.digest.enabled = False
    journal.write_constitution(con)
    opened = Workspace.open(journal.root)
    assert opened.constitution.pipeline.digest.enabled is False
    assert stage_enabled(opened, "digest")
    assert not stage_enabled(opened, "compose")


def test_journal_review_attachment_digests_and_folds(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    from kairo.engine import step
    from kairo.provider import StubProvider
    from kairo.rules import DigestRule

    journal = Workspace.init(tmp_path / "总结", topic="总结")
    review = tmp_path / "review.md"
    review.write_text("# 时段回顾\n\n## 发生了什么\n\n- 旧点\n")
    rid = journal.add(
        [review],
        ref_id="rev",
        occurred_at="2026-08-28",
        role="source_text",
        copy=True,
    )
    assert pending(journal) == []
    spoken = tmp_path / "spoken.md"
    spoken.write_text("分流大约十分之一走新算法，只能测精确率不能测召回。")
    journal.add([spoken], ref_id=rid, role="transcript", copy=True)
    cat = DigestRule(journal, StubProvider())._catalog_items(journal.read_manifest(rid))
    assert {c.role for c in cat} == {"transcript"}
    body = DigestRule(journal, StubProvider())._read_body(journal.read_manifest(rid))
    assert "十分之一" in (body or "")
    assert "旧点" not in (body or "")
    step(journal, StubProvider())
    digest = (journal.root / f"references/{rid}/digest.md").read_text()
    assert "十分之一" in digest
    assert "旧点" not in digest
    src = next(f for f in journal.read_manifest(rid).forms if f.role == "source_text")
    folded = (journal.root / src.location).read_text()
    assert "十分之一" in folded
    assert pending(journal) == []
    assert (journal.root / "understanding.md").exists() is False


def test_journal_review_fold_keeps_body_on_provider_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    from kairo.engine import step
    from kairo.models import REASON_PROVIDER_FAILED
    from kairo.provider import StubProvider

    journal = Workspace.init(tmp_path / "总结", topic="总结")
    review = tmp_path / "review.md"
    review.write_text("ORIGINAL-REVIEW-BODY")
    rid = journal.add([review], ref_id="rev", role="source_text", copy=True)
    spoken = tmp_path / "spoken.md"
    spoken.write_text("新口述要点XYZ")
    journal.add([spoken], ref_id=rid, role="transcript", copy=True)

    class Split:
        name = "split"
        model = "split"
        supports_read_dirs = True

        def run(self, config, signal=None):
            if config.artifact == "digest.md":
                return StubProvider().run(config, signal)
            raise RuntimeError("fold boom")

    step(journal, Split())
    src = next(f for f in journal.read_manifest(rid).forms if f.role == "source_text")
    assert (journal.root / src.location).read_text() == "ORIGINAL-REVIEW-BODY"
    fold = journal.read_state().products[f"references/{rid}/review_fold"]
    assert fold.status == "blocked"
    assert fold.reason == REASON_PROVIDER_FAILED
    assert pending(journal) == []  # blocked hash-matched,终态
