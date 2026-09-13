"""#373: extract errors belong to a provider; `kairo run --all` is one bounded pass."""

from pathlib import Path

import yaml
from typer.testing import CliRunner

from kairo.cli import app
from kairo.engine import step
from kairo.knowledge_review import (
    extract_error_key,
    invalidate_stale,
    load_review,
    mark_extract_error,
    review_path,
)
from kairo.workspace import Workspace

runner = CliRunner()


def _ws(tmp_path: Path, name: str = "ws", *, produced_by: str | None = "stub") -> Workspace:
    ws = Workspace.init(tmp_path / name)
    digest = ws.root / "references/r/digest.md"
    digest.parent.mkdir(parents=True)
    digest.write_text("正文")
    if produced_by is not None:
        from kairo.models import TargetState

        state = ws.read_state()
        state.targets["understanding.md"] = TargetState(produced_by={"provider": produced_by, "model": "m"})
        ws.write_state(state)
    return ws


def test_legacy_errors_without_provider_are_purged_on_load(tmp_path, monkeypatch):
    """S1: records written before the provider field existed disappear on first load."""
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = _ws(tmp_path)
    path = review_path(ws.root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "candidates": [],
                "extract_errors": {
                    "digest:references/r/digest.md": "codex 无 last-message 输出:/tmp/x",
                    "compose:团队管理/2026-09-01-文智沟通_260901": "codex 无 last-message 输出:/tmp/y",
                },
                "extract_error_versions": {"digest:references/r/digest.md": 1},
                "extract_error_meta": {
                    "digest:references/r/digest.md": {"source_kind": "digest", "path": "references/r/digest.md", "version": "1"}
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    assert len(load_review(ws.root).extract_errors) == 2
    review = invalidate_stale(ws.root)
    assert review.extract_errors == {}
    assert review.extract_error_versions == {} and review.extract_error_meta == {}
    # Persisted, not just filtered in memory.
    assert load_review(ws.root).extract_errors == {}


def test_foreign_provider_errors_are_purged_and_current_ones_kept(tmp_path, monkeypatch):
    """S2: an error recorded by another provider is noise; the current provider's stays."""
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = _ws(tmp_path)
    mark_extract_error(ws.root, "references/r/digest.md", "codex timeout", source_kind="digest", provider_name="codex")
    mark_extract_error(ws.root, "references/r/digest.md", "stub boom", source_kind="compose", provider_name="stub")
    stored = load_review(ws.root)
    assert stored.extract_error_meta[extract_error_key("compose", "references/r/digest.md")]["provider"] == "stub"
    review = invalidate_stale(ws.root)
    assert set(review.extract_errors) == {extract_error_key("compose", "references/r/digest.md")}
    assert review.extract_errors[extract_error_key("compose", "references/r/digest.md")] == "stub boom"


def test_errors_are_kept_until_another_provider_acts_on_the_topic(tmp_path, monkeypatch):
    """Only provider evidence purges: a pre-field record on a never-composed Topic stays until a real run."""
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = _ws(tmp_path, produced_by=None)
    mark_extract_error(ws.root, "references/r/digest.md", "codex timeout", source_kind="digest", provider_name="codex")
    review = load_review(ws.root)
    review.last_extract_provider = ""  # simulate a record written before the field existed
    review.extract_error_meta[extract_error_key("digest", "references/r/digest.md")].pop("provider")
    from kairo.knowledge_review import save_review

    save_review(ws.root, review)
    assert len(invalidate_stale(ws.root).extract_errors) == 1
    # The next extraction by any provider is the evidence; the orphan record is then noise.
    from kairo.knowledge_review import ingest_candidates

    ingest_candidates(ws.root, source_kind="compose", path="understanding.md", source_text="正文", drafts=[], provider_name="grok")
    assert load_review(ws.root).extract_errors == {}


def test_fresh_error_from_manual_retry_survives_until_provider_changes_again(tmp_path, monkeypatch):
    """Manual retry under a newly configured provider is evidence: its error stays, older ones go."""
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = _ws(tmp_path, produced_by="codex")
    mark_extract_error(ws.root, "references/r/digest.md", "codex 无 last-message 输出", source_kind="digest", provider_name="codex")
    assert len(invalidate_stale(ws.root).extract_errors) == 1
    mark_extract_error(ws.root, "references/r/digest.md", "grok: yaml 不是列表", source_kind="compose", provider_name="grok")
    review = invalidate_stale(ws.root)
    assert review.last_extract_provider == "grok"
    assert set(review.extract_errors) == {extract_error_key("compose", "references/r/digest.md")}
    # A later successful extraction by grok keeps the grok error keyed elsewhere; ingest clears its own key only.
    from kairo.knowledge_review import ingest_candidates

    ingest_candidates(ws.root, source_kind="digest", path="references/r/digest.md", source_text="正文", drafts=[], provider_name="grok")
    assert set(load_review(ws.root).extract_errors) == {extract_error_key("compose", "references/r/digest.md")}


def test_extract_after_success_records_provider_of_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    from kairo.knowledge_review import extract_after_success

    ws = _ws(tmp_path, produced_by="grok")

    class Boom:
        name = "grok"

    extract_after_success(
        ws.root, tmp_path, source_kind="digest", path="references/r/digest.md", text="正文",
        provider=Boom(), extractor=lambda *_: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    meta = load_review(ws.root).extract_error_meta[extract_error_key("digest", "references/r/digest.md")]
    assert meta["provider"] == "grok"


class _FailProvider:
    name = "fail-prov"
    model = "m"
    supports_read_dirs = True

    def run(self, config, signal=None):
        raise RuntimeError("Grok request failed status 502")


def test_run_all_runs_each_non_clean_topic_once(tmp_path, monkeypatch):
    """S3: clean skipped, pending and retryable each run exactly once, leftovers → exit 1."""
    monkeypatch.setenv("KAIRO_STUB", "1")
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(tmp_path))
    from kairo.refs import create_tag

    for slug in ("a-clean", "b-pending", "c-failed"):
        create_tag(tmp_path, slug)
    clean = Workspace.init(tmp_path / "a-clean", topic="a-clean")
    pending = Workspace.init(tmp_path / "b-pending", topic="b-pending")
    failed = Workspace.init(tmp_path / "c-failed", topic="c-failed")
    for ws, text in ((pending, "待处理"), (failed, "会失败")):
        m = tmp_path / f"{ws.root.name}.txt"
        m.write_text(text)
        ws.add([m])
    step(failed, _FailProvider())
    assert any(ps.status == "blocked" for ps in failed.read_state().products.values())

    calls: list[str] = []
    import kairo.cli as cli

    real_run = cli.engine_run_workspace

    def counting(ws, provider, **kw):
        calls.append(ws.root.name)
        return real_run(ws, provider, **kw)

    monkeypatch.setattr(cli, "engine_run_workspace", counting)
    # First pass: the failed Topic is retried once under the stub provider and recovers.
    result = runner.invoke(app, ["run", "--all"])
    assert result.exit_code == 0, result.output
    assert calls == ["b-pending", "c-failed"]
    assert "a-clean: up to date" in result.output
    assert "run --all: ran 2, up to date 1, needs attention 0, failed 0" in result.output
    assert (pending.root / "understanding.md").is_file() and (failed.root / "understanding.md").is_file()

    # Second pass: everything is clean → nothing runs.
    calls.clear()
    result = runner.invoke(app, ["run", "--all"])
    assert result.exit_code == 0 and calls == []


def test_run_all_reports_leftover_failures_nonzero(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(tmp_path))
    from kairo.refs import create_tag

    create_tag(tmp_path, "t")
    ws = Workspace.init(tmp_path / "t", topic="t")
    m = tmp_path / "m.txt"
    m.write_text("材料")
    ws.add([m])
    import kairo.cli as cli

    monkeypatch.setattr(cli, "select_provider", lambda **_: _FailProvider())
    result = runner.invoke(app, ["run", "--all"])
    assert result.exit_code == 1
    assert "t: failed (provider-failed)" in result.output
    assert "still failed: t" in result.output


def test_run_all_from_inside_a_topic_scans_the_serve_root(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    monkeypatch.delenv("KAIRO_SERVE_ROOT", raising=False)
    from kairo.refs import create_tag

    for slug in ("one", "two"):
        create_tag(tmp_path, slug)
    one = Workspace.init(tmp_path / "one", topic="one")
    two = Workspace.init(tmp_path / "two", topic="two")
    m = tmp_path / "m.txt"
    m.write_text("材料")
    two.add([m])
    monkeypatch.chdir(one.root)
    result = runner.invoke(app, ["run", "--all"])
    assert result.exit_code == 0, result.output
    assert "one: up to date" in result.output and "two: ran" in result.output


def test_run_all_stops_at_attention_topics_like_single_run(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(tmp_path))
    from kairo.refs import create_tag
    from kairo.models import TargetState
    from kairo.rules import REASON_COMPOSE_MIGRATION_REQUIRED

    create_tag(tmp_path, "t")
    ws = Workspace.init(tmp_path / "t", topic="t")
    (ws.root / "understanding.md").write_text("旧正文")
    state = ws.read_state()
    state.targets["understanding.md"] = TargetState(status="blocked", reason=REASON_COMPOSE_MIGRATION_REQUIRED)
    ws.write_state(state)
    import kairo.cli as cli

    calls = []
    monkeypatch.setattr(cli, "engine_run_workspace", lambda ws, p, **kw: calls.append(ws.root.name))
    result = runner.invoke(app, ["run", "--all"])
    assert result.exit_code == 1
    assert calls == []
    assert "t: needs attention" in result.output and "needs attention: t" in result.output


def test_run_all_and_topic_are_mutually_exclusive(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    result = runner.invoke(app, ["run", "--all", "--topic", "x"])
    assert result.exit_code == 2
