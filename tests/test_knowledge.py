"""#182 知识条目、匹配器、审核与 Web 主路径。"""

import re
from pathlib import Path

import pytest

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from kairo.engine import step
from kairo.cli import app
from kairo.knowledge import (
    KnowledgeAlias,
    KnowledgeEntry,
    KnowledgeSource,
    load_global,
    load_workspace,
    migrate_global,
    migrate_workspace,
    new_entry,
    save_global,
    save_workspace,
)
from kairo.knowledge_matcher import KnowledgeMatcher, MatchBudget
from kairo.knowledge_review import (
    MAX_DRAFTS_PER_SOURCE,
    accept_global,
    accept_workspace,
    ingest_candidates,
    invalidate_stale,
    load_review,
    parse_extract_yaml,
    promote,
)
from kairo.provider import StubProvider
from kairo.web.server import create_app
from kairo.workspace import Workspace
from kairo.models import Manifest, ProductState, TargetState


def _entry(title: str, *, scope: str = "global", aliases=(), description=""):
    return KnowledgeEntry(
        id=f"ke-{title}",
        title=title,
        aliases=[KnowledgeAlias(value=value) for value in aliases],
        description=description,
        scope=scope,
    )


def test_matcher_normalizes_boundaries_ambiguity_and_budget():
    local = _entry("本地锚", scope="workspace", aliases=["LOCAL"])
    global_entry = _entry("公共锚", aliases=["local"])
    # alias 同词但不同条目时不自动注入；ASCII 不可嵌在更长 token 中。
    matcher = KnowledgeMatcher([local, global_entry, _entry("Alpha", description="A"), _entry("Beta", description="B")])
    result = matcher.match("LOCAL AlphaX alpha beta", budget=MatchBudget(max_entries=1, max_chars=160))
    assert "local" in result.ambiguities
    assert [match.entry.title for match in result.matches] == ["Alpha"]
    assert result.truncated_count == 1
    assert matcher.suggest(["ＡＬＰＨＡ", "未知"]) == {"ＡＬＰＨＡ": "merge:ke-Alpha", "未知": "unknown"}


def test_migration_uses_one_authority_file(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "glossary.yaml").write_text("- name: 公共\n  aka: [公]\n")
    ws = Workspace.init(root / "ws")
    constitution = (ws.root / "constitution.yaml").read_text() + "glossary:\n  - name: 本地\n    note: 说明\n"
    (ws.root / "constitution.yaml").write_text(constitution)

    assert load_global(root)[1] is True and load_workspace(ws.root)[1] is True
    migrate_global(root)
    migrate_workspace(ws.root)
    assert load_global(root)[1] is False and load_workspace(ws.root)[1] is False
    assert "version: 2" in (root / "glossary.yaml").read_text()
    assert "knowledge:" in (ws.root / "constitution.yaml").read_text()
    assert "glossary:" not in (ws.root / "constitution.yaml").read_text()


def test_review_accept_global_and_stale_source(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws")
    digest = ws.root / "references" / "r" / "digest.md"
    digest.parent.mkdir(parents=True)
    digest.write_text("锚点来自材料")
    ingest_candidates(
        ws.root,
        source_kind="digest",
        path="references/r/digest.md",
        source_text=digest.read_text(),
        drafts=[{"title": "锚点", "description": "定义", "quote": "锚点来自材料"}],
    )
    candidate = load_review(ws.root).candidates[0]
    entry = accept_workspace(ws.root, candidate.id)
    assert entry.scope == "workspace" and load_workspace(ws.root)[1] is False
    # 新候选可走 global 审核；删出处不会反向删知识。
    ingest_candidates(
        ws.root,
        source_kind="digest",
        path="references/r/digest.md",
        source_text=digest.read_text(),
        drafts=[{"title": "全局锚", "quote": "锚点来自材料"}],
    )
    candidate = load_review(ws.root).candidates[-1]
    promoted_local = accept_workspace(ws.root, candidate.id)
    promote(ws.root, promoted_local.id)
    candidate = load_review(ws.root).candidates[-1]
    global_entry = accept_global(root, ws.root, candidate.id)
    assert global_entry.scope == "global" and global_entry.id == promoted_local.id
    assert not any(item.id == promoted_local.id for item in load_workspace(ws.root)[0].entries)
    ingest_candidates(
        ws.root,
        source_kind="digest",
        path="references/r/digest.md",
        source_text=digest.read_text(),
        drafts=[{"title": "待失效", "quote": "锚点来自材料"}],
    )
    digest.unlink()
    review = invalidate_stale(ws.root)
    assert any(c.status == "stale" for c in review.candidates)
    assert load_workspace(ws.root)[0].entries[0].title == "锚点"


def test_digest_and_compose_only_use_confirmed_matching_knowledge(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws")
    public = new_entry(
        title="电网锚",
        scope="global",
        description="仅作参考",
        sources=[KnowledgeSource(kind="reference", path="references/x/manifest.yaml", content_hash="a" * 64)],
    )
    from kairo.knowledge import KnowledgeDocument

    save_global(root, KnowledgeDocument(entries=[public]))
    source = tmp_path / "source.txt"
    source.write_text("今天讨论电网锚。")
    ws.add([source])
    step(ws, StubProvider())
    rid = ws.list_reference_ids()[0]
    digest = (ws.root / f"references/{rid}/digest.md").read_text()
    understanding = (ws.root / "understanding.md").read_text()
    assert "领域知识上下文" in digest and "电网锚" in digest
    assert "领域知识上下文" in understanding and "电网锚" in understanding


def test_knowledge_web_add_and_candidate_actions(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws")
    client = TestClient(create_app(root))
    page = client.get("/knowledge?workspace=ws")
    assert page.status_code == 200
    assert "No local knowledge entries yet" in page.text
    page = client.post("/w/ws/knowledge", data={"title": "本地锚", "description": "说明"})
    assert page.status_code == 200 and "本地锚" in page.text
    digest = ws.root / "references" / "r" / "digest.md"
    digest.parent.mkdir(parents=True)
    digest.write_text("候选证据")
    ingest_candidates(ws.root, source_kind="digest", path="references/r/digest.md", source_text="候选证据", drafts=[{"title": "候选锚", "quote": "候选证据"}])
    candidate = load_review(ws.root).candidates[0]
    page = client.post(f"/w/ws/knowledge/candidates/{candidate.id}/accept")
    assert page.status_code == 200 and "候选锚" in page.text
    assert 'href="/knowledge"' in client.get("/").text


def test_knowledge_narrow_screen_layout_uses_cards_and_wrapping_navigation(tmp_path):
    """390px 视口：知识页不再让表格和内联审核表单撑宽整个文档。"""
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ui-lab")
    ws.add_glossary_entry("本地知识", note="可编辑的窄屏条目", aka=["本地别名"])

    page = TestClient(create_app(root)).get("/knowledge?workspace=ui-lab")
    assert page.status_code == 200
    assert 'class="gl-console knowledge-console knowledge-responsive"' in page.text
    assert 'class="gl-ws-panel"' in page.text
    assert 'class="top-end"' in page.text and 'class="lang-switch"' in page.text

    css = (Path(__file__).parents[1] / "src/kairo/web/static/app.css").read_text(encoding="utf-8")
    narrow = css[css.index("@media (max-width: 520px)") :]
    assert "header.top" in narrow and "flex-wrap: wrap" in narrow
    assert ".knowledge-responsive .glossary-table tr" in narrow
    assert "display: block" in narrow and "width: 100% !important" in narrow
    assert ".knowledge-responsive .gl-ws-panel .glossary-table td.mf-actions form" in narrow
    assert ".knowledge-responsive .dlg-bullets li form" in narrow
    assert ".knowledge-responsive .ref-blocks { overflow-wrap: anywhere; }" in narrow


def test_knowledge_desktop_edit_form_uses_bounded_grid_for_full_width_save(tmp_path):
    """桌面编辑表单的 Save 不得在内联字段末尾重新取一遍父宽。"""
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "desktop-lab")
    ws.add_glossary_entry("本地知识", note="桌面编辑", aka=["别名"])

    page = TestClient(create_app(root)).get("/knowledge?workspace=desktop-lab")
    assert page.status_code == 200
    assert re.search(
        r'<form method="post" action="/w/desktop-lab/knowledge/ke-[^"]+">'
        r'<input name="title"',
        page.text,
    )
    css = (Path(__file__).parents[1] / "src/kairo/web/static/app.css").read_text(encoding="utf-8")
    selector = '.knowledge-console .gl-ws-panel .glossary-table td.mf-actions form:has(input[name="title"])'
    start = css.index(selector)
    rule = css[start : start + 500]
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in rule
    assert "max-width: 100%" in rule
    assert "grid-column: 1 / -1" in css[start : start + 1000]


def test_knowledge_review_queue_omits_stale_candidates(tmp_path):
    """#219: 待审名单只含可处理项；过期档案不占 attention。"""
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws")
    live = ws.root / "references/ok/digest.md"
    live.parent.mkdir(parents=True)
    live.write_text("keep me")
    ingest_candidates(
        ws.root,
        source_kind="digest",
        path="references/ok/digest.md",
        source_text="keep me",
        drafts=[{"title": "LiveTerm", "quote": "keep me"}],
    )
    expired = ws.root / "references/old/digest.md"
    expired.parent.mkdir(parents=True)
    expired.write_text("old quote here")
    ingest_candidates(
        ws.root,
        source_kind="digest",
        path="references/old/digest.md",
        source_text="old quote here",
        drafts=[{"title": "ExpiredTerm", "quote": "old quote here"}],
    )
    expired.write_text("rewritten without the excerpt")
    invalidate_stale(ws.root)
    assert any(c.status == "stale" and c.title == "ExpiredTerm" for c in load_review(ws.root).candidates)

    page = TestClient(create_app(root)).get(
        "/knowledge?workspace=ws", headers={"accept-language": "en"}
    )
    queue = page.text.split("Knowledge candidates to review", 1)[1]
    assert "LiveTerm" in queue
    assert "Accept to this workspace" in queue
    assert "ExpiredTerm" not in queue
    assert "Completed: stale" not in page.text
    assert "Knowledge candidates to review · 1" in page.text
    assert load_review(ws.root).candidates  # 档案仍在磁盘


def test_knowledge_page_en_uses_catalog_and_exposes_merge_preview(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws")
    entry = new_entry(title="existing", scope="workspace")
    save_workspace(ws.root, load_workspace(ws.root)[0].model_copy(update={"entries": [entry]}))
    digest = ws.root / "references/r/digest.md"
    digest.parent.mkdir(parents=True)
    digest.write_text("evidence")
    ingest_candidates(ws.root, source_kind="digest", path="references/r/digest.md", source_text="evidence", drafts=[{"title": "candidate", "quote": "evidence"}])
    page = TestClient(create_app(root)).get("/knowledge?workspace=ws", headers={"accept-language": "en"})
    assert "Merge target" in page.text and "aliases and source" in page.text
    # 顶栏语言切换按钮固定显示“中”；知识功能区域本身的英文页不得漏出中文。
    knowledge_region = page.text.split('<div class="dash-head">', 1)[1]
    assert not re.search(r"[\u4e00-\u9fff]", knowledge_region)
    chinese = TestClient(create_app(root)).get("/knowledge?workspace=ws", headers={"accept-language": "zh"})
    assert "待审核知识候选" in chinese.text and "采纳到本工作区" in chinese.text
    assert "confirmed ·" not in chinese.text and "digest · pending" not in chinese.text


def _drift_region(html: str) -> str:
    m = re.search(
        r'<aside class="knowledge-drift"[^>]*>.*?</aside>', html, re.S
    )
    assert m, "missing knowledge-drift status region"
    return m.group(0)


def test_knowledge_drift_is_visible_and_offers_manual_restep(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws")
    ws.write_manifest(
        "r", Manifest(id="r", title="Kickoff", occurred_at="2026-08-11")
    )
    entry = new_entry(title="current", scope="workspace")
    save_workspace(ws.root, load_workspace(ws.root)[0].model_copy(update={"entries": [entry]}))
    state = ws.read_state()
    state.products["references/r/digest.md"] = ProductState(input_hash="x", knowledge_hash="old")
    ws.write_state(state)
    page = TestClient(create_app(root)).get("/knowledge?workspace=ws", headers={"accept-language": "en"})
    region = _drift_region(page.text)
    assert 'role="status"' in region
    assert 'role="alert"' not in region
    assert "ref-blocks" not in region
    assert "Knowledge has been updated" in region
    assert "not yet recorrected" in region
    assert "later" in region
    assert "Kickoff" in region and "2026-08-11" in region
    assert 'href="/w/ws?ref=r"' in region
    assert 'name="target" value="r"' in region
    assert "Recorrect digest with current knowledge" in region
    assert "Recorrect overview with current knowledge" not in region
    assert "Knowledge context drift" not in page.text
    assert "knowledge changed after this product was made" not in page.text
    assert "Recorrect all" not in page.text
    assert "Re-step with current knowledge" not in page.text
    chinese = TestClient(create_app(root)).get(
        "/knowledge?workspace=ws", headers={"accept-language": "zh"}
    )
    zh = _drift_region(chinese.text)
    assert "知识已更新" in zh
    assert "尚未按当前知识校正" in zh
    assert "稍后" in zh
    assert "按当前知识重做纪要" in zh
    assert "知识上下文漂移" not in chinese.text


def test_knowledge_drift_only_lists_products_that_consume_knowledge(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws")
    ws.write_manifest(
        "r", Manifest(id="r", title="Kickoff", occurred_at="2026-08-11")
    )
    ws.write_manifest(
        "meeting", Manifest(id="meeting", title="Weekly", occurred_at="2026-08-18")
    )
    entry = new_entry(title="current", scope="workspace")
    save_workspace(ws.root, load_workspace(ws.root)[0].model_copy(update={"entries": [entry]}))
    state = ws.read_state()
    for path in (
        "references/r/transcript.md",
        "references/r/source_text.md",
        "references/r/evidence.md",
    ):
        state.products[path] = ProductState(input_hash="source")
    state.products["references/r/digest.md"] = ProductState(input_hash="digest")
    state.products["references/r/prose.md"] = ProductState(input_hash="prose")
    state.products["references/meeting/digest.md"] = ProductState(input_hash="digest2")
    state.targets["understanding.md"] = TargetState()
    state.targets["assessment.md"] = TargetState()
    ws.write_state(state)
    # legacy 兼容写会把旧产物的 None 标为 ""；这仍不能把原料误判成知识消费者。
    ws.add_glossary_entry("兼容写入")

    page = TestClient(create_app(root)).get(
        "/knowledge?workspace=ws", headers={"accept-language": "en"}
    )
    region = _drift_region(page.text)
    assert "4 product" in region
    assert "Kickoff" in region and "Weekly" in region
    assert "understanding.md" in region
    assert 'href="/w/ws?ref=r"' in region
    assert 'href="/w/ws?ref=meeting"' in region
    assert 'href="/w/ws"' in region
    assert page.text.count('name="target" value="r"') == 2
    assert 'name="target" value="meeting"' in region
    assert 'value="understanding.md"' in region
    assert "Recorrect digest with current knowledge" in region
    assert "Recorrect readable prose with current knowledge" in region
    assert "Recorrect overview with current knowledge" in region
    assert "Recorrect digest with current knowledge" != "Recorrect overview with current knowledge"
    assert 'value="assessment.md"' not in page.text
    assert "transcript.md" not in region
    assert "source_text.md" not in region
    assert "evidence.md" not in region
    assert "knowledge changed after this product was made" not in page.text
    assert "Recorrect all" not in page.text


def test_knowledge_drift_restep_retries_the_reference(tmp_path, monkeypatch):
    """产物漂移按钮必须提交 ref_id，并保留按需 prose。"""
    monkeypatch.setenv("KAIRO_STUB", "1")
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws")
    source = tmp_path / "source.wav"
    source.write_bytes(b"stub audio")
    ws.add([source], ref_id="r", role="audio")
    step(ws, StubProvider())
    from kairo.engine import generate_prose

    generate_prose(ws, StubProvider(), "r")
    ws.add_glossary_entry("Alpha")

    client = TestClient(create_app(root))
    page = client.get("/knowledge?workspace=ws", headers={"accept-language": "en"})
    region = _drift_region(page.text)
    assert 'name="target" value="r"' in region
    assert 'name="target" value="references/r/digest.md"' not in page.text
    assert "knowledge changed after this product was made" not in page.text
    assert "Recorrect digest with current knowledge" in region
    assert "Recorrect readable prose with current knowledge" in region

    response = client.post("/w/ws/step", data={"target": "r"})
    task_id = re.search(r"/w/ws/step/([0-9a-f]+)/stream", response.text)
    assert response.status_code == 200 and task_id
    stream = client.get(f"/w/ws/step/{task_id.group(1)}/stream")
    assert "reference 不存在" not in stream.text
    from kairo.knowledge import current_hash

    state = ws.read_state()
    assert state.products["references/r/digest.md"].knowledge_hash == current_hash(
        root, ws.root
    )
    assert (ws.root / "references/r/prose.md").is_file()
    assert state.products["references/r/prose.md"].knowledge_hash == current_hash(
        root, ws.root
    )


@pytest.mark.parametrize("ref_id", ["no-such", "../outside"])
def test_retry_reference_rejects_unknown_id_before_reading_manifest(tmp_path, ref_id):
    ws = Workspace.init(tmp_path / "ws")
    from kairo.engine import retry_reference

    with pytest.raises(ValueError, match="reference 不存在"):
        retry_reference(ws, StubProvider(), ref_id)


def test_workspace_knowledge_todo_count_does_not_double_count_legacy_advisory(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws")
    entry = new_entry(title="current", scope="workspace")
    save_workspace(ws.root, load_workspace(ws.root)[0].model_copy(update={"entries": [entry]}))
    state = ws.read_state()
    state.products["references/r/transcript.md"] = ProductState(
        input_hash="raw", knowledge_hash="", glossary_hash=""
    )
    state.products["references/r/digest.md"] = ProductState(
        input_hash="digest", knowledge_hash="", glossary_hash=""
    )
    state.targets["understanding.md"] = TargetState(
        knowledge_hash="", glossary_hash=""
    )
    ws.write_state(state)

    page = TestClient(create_app(root)).get("/w/ws", headers={"accept-language": "en"})
    assert "Knowledge: 2 item(s) need attention" in page.text
    assert "Knowledge: 4 item(s) need attention" not in page.text


def test_legacy_workspace_api_and_old_candidate_route_keep_v2_authority(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws")
    ws.add_glossary_entry("兼容条目", note="说明", aka=["别名"])
    raw = (ws.root / "constitution.yaml").read_text()
    assert "knowledge:" in raw and "glossary:" not in raw
    entry = load_workspace(ws.root)[0].entries[0]
    assert entry.id.startswith("ke-") and entry.created_at and entry.aliases[0].auto_match
    ws.remove_glossary_entry(0)
    assert load_workspace(ws.root)[0].entries == []
    digest = ws.root / "references/r/digest.md"
    digest.parent.mkdir(parents=True)
    digest.write_text("证据")
    ingest_candidates(ws.root, source_kind="digest", path="references/r/digest.md", source_text="证据", drafts=[{"title": "候选", "quote": "证据"}])
    candidate = load_review(ws.root).candidates[0]
    response = TestClient(create_app(root)).post(f"/w/ws/glossary/candidates/{candidate.id}/accept")
    assert response.status_code == 200
    accepted = load_workspace(ws.root)[0].entries[0]
    assert accepted.sources and accepted.sources[0].content_hash and "glossary:" not in (ws.root / "constitution.yaml").read_text()


def test_v2_rejects_missing_audit_or_source_hash_and_matcher_snapshot_isolated(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    from kairo.knowledge import KnowledgeError, validate_entries
    import pytest

    bad = KnowledgeEntry(id="ke-bad", title="bad", scope="global")
    with pytest.raises(KnowledgeError):
        validate_entries([bad], scope="global")
    entry = new_entry(title="锚点", scope="global", sources=[KnowledgeSource(kind="digest", path="references/r/digest.md", content_hash="b" * 64)])
    matcher = KnowledgeMatcher([entry])
    entry.title = "被外部改写"
    assert [hit.entry.title for hit in matcher.match("锚点").matches] == ["锚点"]
    refreshed = matcher.refresh([entry])
    assert refreshed is not matcher and matcher.version != refreshed.version


def test_global_accept_replays_journal_before_stale_after_local_failure(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws")
    digest = ws.root / "references/r/digest.md"
    digest.parent.mkdir(parents=True)
    digest.write_text("evidence")
    ingest_candidates(ws.root, source_kind="digest", path="references/r/digest.md", source_text="evidence", drafts=[{"title": "global", "quote": "evidence"}])
    local = accept_workspace(ws.root, load_review(ws.root).candidates[0].id)
    promotion = promote(ws.root, local.id)
    import kairo.knowledge_review as module

    original = module.save_workspace
    calls = {"n": 0}

    def fail_once(path, document):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("local save failed")
        return original(path, document)

    monkeypatch.setattr(module, "save_workspace", fail_once)
    import pytest
    with pytest.raises(OSError):
        accept_global(root, ws.root, promotion.id)
    digest.unlink()
    entry = accept_global(root, ws.root, promotion.id)
    assert entry.id == local.id
    assert not any(item.id == local.id for item in load_workspace(ws.root)[0].entries)
    assert next(item for item in load_review(ws.root).candidates if item.id == promotion.id).status == "accepted"


def test_extract_static_route_precedes_entry_and_preserves_compose_kind(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws")
    path = ws.root / "references/r/digest.md"
    path.parent.mkdir(parents=True)
    path.write_text("done")
    from kairo.knowledge_review import mark_extract_error
    mark_extract_error(ws.root, "references/r/digest.md", "safe", source_kind="compose")
    seen = {}
    import kairo.knowledge_review as module

    def fake_extract(*args, **kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(module, "extract_after_success", fake_extract)
    response = TestClient(create_app(root)).post("/w/ws/knowledge/extract", data={"path": "references/r/digest.md"})
    assert response.status_code == 200 and seen["source_kind"] == "compose"
    assert TestClient(create_app(root)).post("/w/ws/knowledge/extract", data={"path": "constitution.yaml"}).status_code == 200


def test_matcher_suggest_keeps_short_and_manual_aliases_out_of_auto_match():
    entry = _entry("A", aliases=["XY", "manual"])
    entry = entry.model_copy(update={"aliases": [KnowledgeAlias(value="XY", auto_match=False), KnowledgeAlias(value="manual", auto_match=False)]})
    matcher = KnowledgeMatcher([entry, _entry("另一个", scope="workspace", aliases=["冲突"])])
    assert matcher.match("XY manual").matches == ()
    assert matcher.suggest(["XY", "manual", "A"]) == {"XY": "merge:ke-A", "manual": "merge:ke-A", "A": "merge:ke-A"}


def test_legacy_review_migrates_once_and_old_web_routes_use_knowledge(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws")
    digest = ws.root / "references/r/digest.md"
    digest.parent.mkdir(parents=True)
    digest.write_text("旧候选证据")
    review_dir = ws.root / ".kairo"
    review_dir.mkdir(exist_ok=True)
    (review_dir / "glossary_review.yaml").write_text("candidates:\n  - id: gc-old\n    name: 旧候选\n    ref_id: r\n    quote: 旧候选证据\n")
    review = load_review(ws.root)
    assert review.candidates[0].source_kind == "digest"
    assert (review_dir / "knowledge_review.yaml").is_file()
    assert (review_dir / "glossary_review.yaml.migrated").is_file()
    client = TestClient(create_app(root))
    assert client.get("/glossary", follow_redirects=False).headers["location"] == "/knowledge"
    page = client.post("/w/ws/glossary", data={"name": "兼容写入", "scope": "workspace"})
    assert page.status_code == 200
    assert any(item.title == "兼容写入" for item in load_workspace(ws.root)[0].entries)


def test_global_accept_retries_to_consistent_authorities(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws")
    digest = ws.root / "references/r/digest.md"
    digest.parent.mkdir(parents=True)
    digest.write_text("可定位证据")
    ingest_candidates(ws.root, source_kind="digest", path="references/r/digest.md", source_text="可定位证据", drafts=[{"title": "待提升", "quote": "可定位证据"}])
    local = accept_workspace(ws.root, load_review(ws.root).candidates[0].id)
    promotion = promote(ws.root, local.id)
    import kairo.knowledge_review as review_module

    original = review_module.save_review
    calls = {"n": 0}

    def fail_once(path, review):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("injected review write failure")
        return original(path, review)

    monkeypatch.setattr(review_module, "save_review", fail_once)
    try:
        accept_global(root, ws.root, promotion.id)
    except OSError:
        pass
    entry = accept_global(root, ws.root, promotion.id)
    assert entry.id == local.id
    assert any(item.id == local.id for item in load_global(root)[0].entries)
    assert not any(item.id == local.id for item in load_workspace(ws.root)[0].entries)
    assert next(item for item in load_review(ws.root).candidates if item.id == promotion.id).status == "accepted"


def test_extraction_is_side_effect_only_when_review_is_broken(tmp_path):
    from kairo.knowledge_review import extract_after_success

    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws")
    (ws.root / ".kairo").mkdir(exist_ok=True)
    (ws.root / ".kairo/knowledge_review.yaml").write_text("not: [valid")
    # 不得抛出：Digest/Compose 已写出的主产物不能被审核旁路撤销。
    extract_after_success(ws.root, root, source_kind="digest", path="references/r/digest.md", text="正文", extractor=lambda *_: [])


def test_cross_scope_conflict_is_local_ambiguity_not_global_disable():
    global_entry = _entry("公共", aliases=["冲突"])
    local = _entry("本地", scope="workspace", aliases=["冲突"])
    matcher = KnowledgeMatcher([global_entry, local, _entry("仍可用", scope="workspace")])
    result = matcher.match("冲突 仍可用")
    assert result.ambiguities == ("冲突",)
    assert [hit.entry.title for hit in result.matches] == ["仍可用"]


def test_rejected_entry_repromotes_without_duplicate_and_manual_source_stays_empty(tmp_path):
    from kairo.knowledge_review import promote_entry, reject_global

    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "中文空间")
    entry = new_entry(title="人工条目", scope="workspace")
    from kairo.knowledge import KnowledgeDocument, save_workspace

    save_workspace(ws.root, KnowledgeDocument(entries=[entry]))
    first = promote_entry(ws.root, entry.id)
    reject_global(ws.root, first.id, "仅本地")
    second = promote_entry(ws.root, entry.id)
    assert second.id == first.id and second.status == "pending_global" and not second.reject_reason
    promoted = accept_global(root, ws.root, second.id)
    assert promoted.id == entry.id and promoted.sources == []


def test_legacy_list_is_read_only_and_v2_legacy_delete_keeps_v2(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws")
    (root / "glossary.yaml").write_text("- name: 旧公共\n", encoding="utf-8")
    before = (root / "glossary.yaml").read_bytes()
    monkeypatch.chdir(ws.root)
    result = CliRunner().invoke(app, ["glossary", "list"])
    assert result.exit_code == 0
    assert (root / "glossary.yaml").read_bytes() == before
    client = TestClient(create_app(root))
    client.post("/glossary", data={"name": "新公共"})
    deleted = client.post("/glossary/0/delete")
    assert deleted.status_code == 200
    raw = (root / "glossary.yaml").read_text(encoding="utf-8")
    assert "version: 2" in raw and "title:" in raw


def test_pending_candidate_can_be_edited_in_web(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws")
    digest = ws.root / "references/r/digest.md"
    digest.parent.mkdir(parents=True)
    digest.write_text("候选证据")
    ingest_candidates(ws.root, source_kind="digest", path="references/r/digest.md", source_text="候选证据", drafts=[{"title": "旧标题", "quote": "候选证据"}])
    candidate = load_review(ws.root).candidates[0]
    page = TestClient(create_app(root)).post(f"/w/ws/knowledge/candidates/{candidate.id}", data={"title": "新标题", "description": "已编辑", "aliases": "别名", "tags": "tag"})
    assert page.status_code == 200 and "新标题" in page.text
    changed = load_review(ws.root).candidates[0]
    assert changed.title == "新标题" and changed.tags == ["tag"]


def test_workspace_accept_replays_journal_after_review_failure(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws")
    digest = ws.root / "references/r/digest.md"
    digest.parent.mkdir(parents=True)
    digest.write_text("证据")
    ingest_candidates(ws.root, source_kind="digest", path="references/r/digest.md", source_text="证据", drafts=[{"title": "可恢复", "quote": "证据"}])
    candidate = load_review(ws.root).candidates[0]
    import kairo.knowledge_review as review_module

    original = review_module.save_review
    monkeypatch.setattr(review_module, "save_review", lambda *_: (_ for _ in ()).throw(OSError("review fail")))
    try:
        accept_workspace(ws.root, candidate.id)
    except OSError:
        pass
    monkeypatch.setattr(review_module, "save_review", original)
    entry = accept_workspace(ws.root, candidate.id)
    assert entry.title == "可恢复"
    assert load_review(ws.root).candidates[0].status == "accepted"
    assert not (ws.root / ".kairo/knowledge_transaction.yaml").exists()


def test_parse_extract_yaml_keeps_block_list_with_flow_tags():
    """#195:tags/aliases 的 [] 不得被当成整份文档截断。"""
    text = (
        "- title: 节能算法\n"
        "  description: 回路加时间\n"
        "  aliases: [节能]\n"
        "  tags: [算法, 能源]\n"
        "  quote: 只能测精确率\n"
        "- title: 一张网\n"
        "  tags: [能源]\n"
        "  quote: 11月1日要上\n"
    )
    items = parse_extract_yaml(text)
    assert [i["title"] for i in items] == ["节能算法", "一张网"]
    assert items[0]["tags"] == ["算法", "能源"]


def test_parse_extract_yaml_survives_preamble_and_fence():
    fenced = (
        "```yaml\n"
        "- title: 电网锚\n"
        "  tags: [算法, 能源]\n"
        "  quote: 今天讨论电网锚\n"
        "```\n"
    )
    assert parse_extract_yaml(fenced)[0]["title"] == "电网锚"
    with_preamble = "先读取材料\n\n- title: 电网锚\n  tags: [算法]\n  quote: 讨论电网\n"
    assert parse_extract_yaml(with_preamble)[0]["title"] == "电网锚"


def test_extract_after_success_ingests_flow_tags(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "总结", topic="总结")
    digest = ws.root / "references/r/digest.md"
    digest.parent.mkdir(parents=True)
    body = "只能测精确率。11月1日要上。"
    digest.write_text(body)
    yaml_text = (
        "- title: 节能算法\n"
        "  tags: [算法, 能源]\n"
        "  quote: 只能测精确率\n"
        "- title: 一张网\n"
        "  tags: [能源]\n"
        "  quote: 11月1日要上\n"
    )
    from kairo.knowledge_review import extract_after_success

    extract_after_success(
        ws.root,
        root,
        source_kind="digest",
        path="references/r/digest.md",
        text=body,
        extractor=lambda *_: parse_extract_yaml(yaml_text),
    )
    review = load_review(ws.root)
    assert review.extract_errors == {}
    assert {c.title for c in review.candidates} == {"节能算法", "一张网"}


def test_ingest_replaces_pending_for_same_path_and_caps_drafts(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws")
    path = "references/r/digest.md"
    body = "证据甲 证据乙 证据丙"
    digest = ws.root / path
    digest.parent.mkdir(parents=True)
    digest.write_text(body)
    ingest_candidates(
        ws.root,
        source_kind="digest",
        path=path,
        source_text=body,
        drafts=[{"title": "旧甲", "quote": "证据甲"}, {"title": "旧乙", "quote": "证据乙"}],
    )
    kept = accept_workspace(ws.root, load_review(ws.root).candidates[0].id)
    drafts = [{"title": f"新{i}", "quote": "证据丙"} for i in range(MAX_DRAFTS_PER_SOURCE + 5)]
    ingest_candidates(ws.root, source_kind="digest", path=path, source_text=body, drafts=drafts)
    review = load_review(ws.root)
    pending = [c for c in review.candidates if c.status == "pending"]
    assert all(c.title.startswith("新") for c in pending)
    assert len(pending) == MAX_DRAFTS_PER_SOURCE
    assert any(c.status == "accepted" and c.title == "旧甲" for c in review.candidates)
    assert kept.id in {c.merged_into for c in review.candidates if c.status == "accepted"}


def test_knowledge_queue_is_compact_without_merge_when_empty(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws")
    digest = ws.root / "references/r/digest.md"
    digest.parent.mkdir(parents=True)
    digest.write_text("证据")
    ingest_candidates(
        ws.root,
        source_kind="digest",
        path="references/r/digest.md",
        source_text="证据",
        drafts=[{"title": "数据质量智能体", "quote": "证据"}],
    )
    html = TestClient(create_app(root)).get("/knowledge?workspace=ws").text
    assert "数据质量智能体" in html
    assert "Merge target" not in html and "合并目标" not in html
    assert "→ unknown" not in html
    assert "<details" in html and "knowledge-candidate-edit" in html
    assert "Edit proposed entry" in html or "编辑拟议条目" in html
    queue, _, _ = html.partition("<details")
    assert "Save candidate edit" not in queue and "保存候选编辑" not in queue


def test_unicode_workspace_slug_is_retained_in_source(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "能源梳理")
    digest = ws.root / "references/r/digest.md"
    digest.parent.mkdir(parents=True)
    digest.write_text("证据")
    ingest_candidates(ws.root, source_kind="digest", path="references/r/digest.md", source_text="证据", drafts=[{"title": "中文范围", "quote": "证据"}])
    entry = accept_workspace(ws.root, load_review(ws.root).candidates[0].id)
    assert entry.sources[0].workspace_slug == "能源梳理"


def test_candidate_provider_only_receives_current_product_and_redacts_error(tmp_path, monkeypatch):
    from kairo.knowledge_review import extract_after_success, provider_extractor

    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws")
    seen = {}

    def fake_run(_provider, persona, context, artifact):
        seen.update(persona=persona, context=context, artifact=artifact)
        return "[]"

    monkeypatch.setattr("kairo.rules._run_agent", fake_run)
    provider_extractor(object())("本次产物", [_entry("不应泄露的已知知识")], "references/r/digest.md")
    assert "不应泄露" not in seen["context"] and "本次产物" in seen["context"]
    extract_after_success(ws.root, root, source_kind="digest", path="references/r/digest.md", text="正文", extractor=lambda *_: (_ for _ in ()).throw(RuntimeError("Authorization: Bearer super-secret-token api_key=hidden")))
    from kairo.knowledge_review import extract_error_key
    error = load_review(ws.root).extract_errors[extract_error_key("digest", "references/r/digest.md")]
    assert "super-secret-token" not in error and "hidden" not in error and "[redacted]" in error


def test_matcher_cache_uses_semantic_snapshot_and_time_is_strict():
    from kairo.knowledge import KnowledgeDocument, KnowledgeError, validate_entries
    from kairo.knowledge_matcher import matcher_for

    entry = new_entry(title="缓存条目", scope="global", description="说明")
    assert matcher_for([entry]) is matcher_for([entry])
    assert matcher_for([entry]).match("缓存条目").version
    bad = entry.model_copy(update={"created_at": "2026-01-01"})
    try:
        validate_entries([bad], scope="global")
    except KnowledgeError:
        pass
    else:
        raise AssertionError("缺少时区的时间必须被拒绝")
    assert KnowledgeDocument(entries=[entry]).entries[0].created_at.endswith("+00:00")


def test_transaction_replay_covers_workspace_and_global_merge_after_source_removed(tmp_path, monkeypatch):
    """P1-1：三类跨文件动作在 stale 前按 journal 的已落盘权威收敛。"""
    from kairo.knowledge_review import merge_global, merge_workspace
    import pytest

    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws")
    digest = ws.root / "references/r/digest.md"
    digest.parent.mkdir(parents=True)
    digest.write_text("证据")
    base = new_entry(title="目标", scope="workspace")
    save_workspace(ws.root, load_workspace(ws.root)[0].model_copy(update={"entries": [base]}))
    ingest_candidates(ws.root, source_kind="digest", path="references/r/digest.md", source_text="证据", drafts=[{"title": "合并", "quote": "证据"}])
    candidate = load_review(ws.root).candidates[0]
    import kairo.knowledge_review as module
    original = module.save_review
    monkeypatch.setattr(module, "save_review", lambda *_: (_ for _ in ()).throw(OSError("review")))
    with pytest.raises(OSError):
        merge_workspace(ws.root, candidate.id, base.id)
    digest.unlink()
    monkeypatch.setattr(module, "save_review", original)
    merge_workspace(ws.root, candidate.id, base.id)
    assert load_review(ws.root).candidates[0].status == "merged"
    assert not (ws.root / ".kairo/knowledge_transaction.yaml").exists()

    # global merge 同样通过 source_entry_id 移除本地 authority，再补终态。
    local = new_entry(title="待合并公共", scope="workspace")
    save_workspace(ws.root, load_workspace(ws.root)[0].model_copy(update={"entries": [local]}))
    promotion = promote(ws.root, local.id)
    global_target = new_entry(title="公共目标", scope="global")
    from kairo.knowledge import KnowledgeDocument
    save_global(root, KnowledgeDocument(entries=[global_target]))
    monkeypatch.setattr(module, "save_review", lambda *_: (_ for _ in ()).throw(OSError("review")))
    with pytest.raises(OSError):
        merge_global(root, ws.root, promotion.id, global_target.id)
    monkeypatch.setattr(module, "save_review", original)
    merge_global(root, ws.root, promotion.id, global_target.id)
    assert not any(item.id == local.id for item in load_workspace(ws.root)[0].entries)
    assert next(item for item in load_review(ws.root).candidates if item.id == promotion.id).status == "merged"


@pytest.mark.parametrize("operation", ["accept_workspace", "merge_workspace", "accept_global", "merge_global"])
@pytest.mark.parametrize("remove_source", [False, True])
def test_prepared_journal_recovers_when_second_transaction_write_fails(tmp_path, monkeypatch, operation, remove_source):
    """P1-1：四种动作第二次 journal 写失败后，以预期 authority 后态收敛。"""
    from kairo.knowledge_review import merge_global, merge_workspace

    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws")
    digest = ws.root / "references/r/digest.md"
    digest.parent.mkdir(parents=True)
    digest.write_text("证据")
    ingest_candidates(ws.root, source_kind="digest", path="references/r/digest.md", source_text="证据", drafts=[{"title": f"候选-{operation}", "quote": "证据"}])
    candidate = load_review(ws.root).candidates[0]
    target_id = ""
    if operation == "merge_workspace":
        target = new_entry(title="本地目标", scope="workspace")
        save_workspace(ws.root, load_workspace(ws.root)[0].model_copy(update={"entries": [target]}))
        target_id = target.id
        def call():
            return merge_workspace(ws.root, candidate.id, target_id)
    elif operation == "accept_workspace":
        def call():
            return accept_workspace(ws.root, candidate.id)
    else:
        local = accept_workspace(ws.root, candidate.id)
        candidate = promote(ws.root, local.id)
        if operation == "accept_global":
            def call():
                return accept_global(root, ws.root, candidate.id)
        else:
            target = new_entry(title="公共目标", scope="global")
            from kairo.knowledge import KnowledgeDocument
            save_global(root, KnowledgeDocument(entries=[target]))
            target_id = target.id
            def call():
                return merge_global(root, ws.root, candidate.id, target_id)
    import kairo.knowledge_review as module

    original = module._write_transaction

    def fail_second(path, payload):
        if payload.get("stage") == "authority-written":
            raise OSError("injected second journal write")
        return original(path, payload)

    monkeypatch.setattr(module, "_write_transaction", fail_second)
    with pytest.raises(OSError, match="second journal"):
        call()
    if remove_source:
        digest.unlink()
    monkeypatch.setattr(module, "_write_transaction", original)
    replayed = call()
    review = load_review(ws.root)
    terminal = next(item for item in review.candidates if item.id == candidate.id)
    assert terminal.status == ("accepted" if operation.startswith("accept") else "merged")
    assert not (ws.root / ".kairo/knowledge_transaction.yaml").exists()
    if operation == "accept_workspace":
        assert replayed.id == terminal.merged_into
    if operation in {"accept_global", "merge_global"}:
        assert not any(entry.id == candidate.entry_id for entry in load_workspace(ws.root)[0].entries)


def test_extract_errors_are_keyed_by_kind_and_path_and_clear_independently(tmp_path):
    """P1-4：Digest 与 Compose 相同路径可各自重试、各自清除。"""
    from kairo.knowledge_review import extract_error_key, mark_extract_error
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws")
    path = "references/r/digest.md"
    mark_extract_error(ws.root, path, "digest-error", source_kind="digest")
    mark_extract_error(ws.root, path, "compose-error", source_kind="compose")
    review = load_review(ws.root)
    assert set(review.extract_errors) == {extract_error_key("digest", path), extract_error_key("compose", path)}
    ingest_candidates(ws.root, source_kind="digest", path=path, source_text="证据", drafts=[])
    assert set(load_review(ws.root).extract_errors) == {extract_error_key("compose", path)}


def test_legacy_root_candidate_keeps_gc_route_and_stable_local_entry(tmp_path):
    """P1-2：gc-* 旧链接可继续审核，pending_root/root_rejected 均锚定本地 ke-*。"""
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws")
    digest = ws.root / "references/r/digest.md"
    digest.parent.mkdir(parents=True)
    digest.write_text("旧证据")
    (ws.root / ".kairo").mkdir(exist_ok=True)
    (ws.root / ".kairo/glossary_review.yaml").write_text(
        "candidates:\n"
        "  - id: gc-pending\n    name: 待提升\n    ref_id: r\n    quote: 旧证据\n    status: pending_root\n"
        "  - id: gc-rejected\n    name: 退回条目\n    ref_id: r\n    quote: 旧证据\n    status: root_rejected\n    reject_reason: 需编辑\n",
        encoding="utf-8",
    )
    review = load_review(ws.root)
    pending = next(candidate for candidate in review.candidates if candidate.legacy_id == "gc-pending")
    rejected = next(candidate for candidate in review.candidates if candidate.legacy_id == "gc-rejected")
    assert pending.entry_id.startswith("ke-") and rejected.entry_id.startswith("ke-")
    accepted = accept_global(root, ws.root, "gc-pending")
    assert accepted.id == pending.entry_id
    from kairo.knowledge_review import update_workspace_entry
    update_workspace_entry(ws.root, rejected.entry_id, title="退回条目已编辑", description="可再次提升", aliases=[], tags=[])
    refreshed = promote(ws.root, rejected.entry_id)
    assert refreshed.id == rejected.id and refreshed.status == "pending_global" and not refreshed.reject_reason


def test_legacy_root_rejected_gc_promote_route_reuses_editable_entry(tmp_path):
    """P1-2：真实旧 gc-* promote URL 对 root_rejected 不会重复 accept。"""
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws")
    digest = ws.root / "references/r/digest.md"
    digest.parent.mkdir(parents=True)
    digest.write_text("旧证据")
    (ws.root / ".kairo").mkdir(exist_ok=True)
    (ws.root / ".kairo/glossary_review.yaml").write_text(
        "candidates:\n  - id: gc-rejected-route\n    name: 旧退回\n    ref_id: r\n    quote: 旧证据\n    status: root_rejected\n",
        encoding="utf-8",
    )
    rejected = load_review(ws.root).candidates[0]
    from kairo.knowledge_review import update_workspace_entry
    update_workspace_entry(ws.root, rejected.entry_id, title="已编辑旧退回", description="", aliases=[], tags=[])
    response = TestClient(create_app(root)).post("/w/ws/glossary/candidates/gc-rejected-route/promote")
    assert response.status_code == 200
    promoted = next(item for item in load_review(ws.root).candidates if item.legacy_id == "gc-rejected-route")
    assert promoted.id == rejected.id and promoted.entry_id == rejected.entry_id and promoted.status == "pending_global"


def test_promotion_preserves_all_sources_when_one_source_disappears(tmp_path):
    """P1-3：提升候选保留全部出处；首出处消失而另一个有效时不可 stale。"""
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws")
    a = ws.root / "references/a/digest.md"
    b = ws.root / "references/b/digest.md"
    a.parent.mkdir(parents=True)
    b.parent.mkdir(parents=True)
    a.write_text("甲证据")
    b.write_text("乙证据")
    entry = new_entry(title="多出处", scope="workspace", sources=[
        KnowledgeSource(kind="digest", path="references/a/digest.md", quote="甲证据", content_hash="a" * 64, workspace_slug="ws"),
        KnowledgeSource(kind="digest", path="references/b/digest.md", quote="乙证据", content_hash="b" * 64, workspace_slug="ws"),
    ])
    # 使用真实 digest hash，保证第二出处可定位。
    entry.sources[0].content_hash = __import__("hashlib").sha256(a.read_text().encode()).hexdigest()
    entry.sources[1].content_hash = __import__("hashlib").sha256(b.read_text().encode()).hexdigest()
    save_workspace(ws.root, load_workspace(ws.root)[0].model_copy(update={"entries": [entry]}))
    candidate = promote(ws.root, entry.id)
    a.unlink()
    assert next(item for item in invalidate_stale(ws.root).candidates if item.id == candidate.id).status == "pending_global"
    global_entry = accept_global(root, ws.root, candidate.id)
    assert len(global_entry.sources) == 2


def test_matcher_display_scope_renderer_budget_and_semantic_projection():
    """P2-5/P2-8：展示词、scope、版本和预算与实际 renderer/hash 同步。"""
    from kairo.knowledge import semantic_hash
    from kairo.knowledge_matcher import format_knowledge_context
    entry = new_entry(title="Canonical", scope="global", aliases=[KnowledgeAlias(value="Alias")], description="说明")
    local = new_entry(title="本地条目", scope="workspace", description="本地")
    matcher = KnowledgeMatcher([entry, local], semantic_version="v1")
    hit = matcher.match("Alias 本地条目", scope="global", budget=MatchBudget(max_chars=10_000)).matches[0]
    assert hit.term == "Alias" and matcher.version == "v1"
    result = matcher.match("Alias 本地条目", budget=MatchBudget(max_entries=1, max_chars=10_000))
    assert len(format_knowledge_context(result)) <= 10_000 and result.matches[0].entry.scope == "workspace"
    changed_tags = entry.model_copy(update={"tags": ["不会注入"]})
    changed_quote = entry.model_copy(update={"sources": [KnowledgeSource(kind="digest", path="references/r/digest.md", quote="不同", content_hash="a" * 64, workspace_slug="ws")]})
    same_path = changed_quote.model_copy(update={"sources": [KnowledgeSource(kind="digest", path="references/r/digest.md", quote="再变", content_hash="b" * 64, workspace_slug="other")]})
    assert semantic_hash([entry]) == semantic_hash([changed_tags])
    assert semantic_hash([changed_quote]) == semantic_hash([same_path])


def test_matcher_scope_filters_owners_before_ambiguity_and_keeps_normalized_term():
    """P2-3：scope 内唯一 owner 不应被另一范围同 alias 误判歧义。"""
    global_entry = new_entry(title="全球", scope="global", aliases=[KnowledgeAlias(value="共享别名")])
    local_entry = new_entry(title="本地", scope="workspace", aliases=[KnowledgeAlias(value="共享别名")])
    # ㍿ 经 NFKC 变为「株式会社」，排序依赖规范化词而非原始兼容字符。
    kabushiki = new_entry(title="㍿甲", scope="workspace")
    matcher = KnowledgeMatcher([global_entry, local_entry, kabushiki])
    global_hit = matcher.match("共享别名", scope="global").matches[0]
    local_hit = matcher.match("共享别名 株式会社甲", scope="workspace").matches
    assert global_hit.entry.id == global_entry.id and not matcher.match("共享别名", scope="global").ambiguities
    assert local_hit[0].entry.id == kabushiki.id or local_hit[0].entry.id == local_entry.id
    alias_hit = next(hit for hit in local_hit if hit.entry.id == local_entry.id)
    assert alias_hit.normalized_term == "共享别名" and alias_hit.display_term == "共享别名"


def test_knowledge_filter_and_global_source_link_include_availability(tmp_path):
    """P2-6：同一筛选覆盖 global/local/candidate，global 出处回链到实际 workspace。"""
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws")
    digest = ws.root / "references/r/digest.md"
    digest.parent.mkdir(parents=True)
    digest.write_text("证据")
    source = KnowledgeSource(kind="digest", path="references/r/digest.md", quote="证据", content_hash=__import__("hashlib").sha256("证据".encode()).hexdigest(), workspace_slug="ws")
    from kairo.knowledge import KnowledgeDocument
    save_global(root, KnowledgeDocument(entries=[new_entry(title="只查我", scope="global", sources=[source])]))
    page = TestClient(create_app(root)).get("/knowledge?workspace=ws&filter=%E5%8F%AA%E6%9F%A5%E6%88%91")
    assert page.status_code == 200 and "只查我" in page.text and '/w/ws?ref=r' in page.text


def test_promotion_card_renders_entry_fields_and_all_source_links(tmp_path):
    """P2-4：全局审核卡从 entry_id 展示完整条目，而非过期候选快照。"""
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws")
    first = ws.root / "references/a/digest.md"
    second = ws.root / "references/b/digest.md"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text("甲来源")
    second.write_text("乙来源")
    import hashlib
    entry = new_entry(title="待提升完整", scope="workspace", description="完整说明", tags=["能源", "审核"], aliases=[KnowledgeAlias(value="别名关闭", auto_match=False)], sources=[
        KnowledgeSource(kind="digest", path="references/a/digest.md", quote="甲来源", content_hash=hashlib.sha256("甲来源".encode()).hexdigest(), workspace_slug="ws"),
        KnowledgeSource(kind="digest", path="references/b/digest.md", quote="乙来源", content_hash=hashlib.sha256("乙来源".encode()).hexdigest(), workspace_slug="ws"),
    ])
    save_workspace(ws.root, load_workspace(ws.root)[0].model_copy(update={"entries": [entry]}))
    promote(ws.root, entry.id)
    page = TestClient(create_app(root)).get("/knowledge?workspace=ws")
    assert page.status_code == 200
    for value in ("完整说明", "能源", "审核", "别名关闭", "not auto-matched", "references/a/digest.md", "references/b/digest.md"):
        assert value in page.text
    assert '/w/ws?ref=a' in page.text and '/w/ws?ref=b' in page.text


def test_knowledge_web_errors_are_localized_without_exception_chinese(tmp_path, monkeypatch):
    """P2-5：严格 YAML、scope、重复、候选状态、保存/迁移错误均走英文 catalog。"""
    root = tmp_path / "root"
    root.mkdir()
    Workspace.init(root / "ws")
    client = TestClient(create_app(root))
    headers = {"accept-language": "en"}
    client.post("/knowledge/global", data={"title": "duplicate"}, headers=headers)
    duplicate = client.post("/knowledge/global", data={"title": "duplicate"}, headers=headers)
    duplicate_error = duplicate.text.split('role="alert">', 1)[1].split("</p>", 1)[0]
    assert "canonical title or alias conflicts" in duplicate_error and not re.search(r"[\u4e00-\u9fff]", duplicate_error)
    from starlette.requests import Request
    from kairo.web.views import _knowledge_error_text
    scope_request = Request({"type": "http", "headers": [(b"accept-language", b"en")]})
    assert _knowledge_error_text(scope_request, "未知 scope 'shared'") == "This action is not permitted in the selected knowledge scope."
    stale = client.post("/w/ws/knowledge/candidates/kc-00000000000000000000/accept", headers=headers)
    assert "no longer available" in stale.text
    import kairo.knowledge as knowledge_module
    original_save = knowledge_module.save_global
    from kairo.knowledge import KnowledgeError
    monkeypatch.setattr(knowledge_module, "save_global", lambda *_: (_ for _ in ()).throw(KnowledgeError("保存失败")))
    save_failed = client.post("/knowledge/global", data={"title": "save-failed"}, headers=headers)
    save_error = save_failed.text.split('role="alert">', 1)[1].split("</p>", 1)[0]
    assert "could not be saved" in save_error and not re.search(r"[\u4e00-\u9fff]", save_error)
    monkeypatch.setattr(knowledge_module, "save_global", original_save)
    (root / "glossary.yaml").write_text("version: 2\nentries: [", encoding="utf-8")
    invalid = client.get("/knowledge", headers=headers)
    assert "Knowledge document is invalid" in invalid.text
    # 迁移非法也不能把异常文本直接泄漏到英文页面。
    (root / "glossary.yaml").write_text("- missing-name\n", encoding="utf-8")
    migration = client.post("/knowledge/global", data={"title": "migration"}, headers=headers)
    migration_error = migration.text.split('role="alert">', 1)[1].split("</p>", 1)[0]
    assert not re.search(r"[\u4e00-\u9fff]", migration_error)


def test_legacy_glossary_http_errors_use_knowledge_catalog_in_english(tmp_path):
    """最终 P2：旧 workspace/root 写删与候选端点也不输出中文异常。"""
    root = tmp_path / "root"
    root.mkdir()
    Workspace.init(root / "ws")
    client = TestClient(create_app(root))
    headers = {"accept-language": "en"}

    def error_of(response):
        assert response.status_code == 200
        error = response.text.split('role="alert">', 1)[1].split("</p>", 1)[0]
        assert not re.search(r"[\u4e00-\u9fff]", error)
        return error

    assert "scope" in error_of(client.post("/w/ws/glossary", data={"name": "x", "scope": "global"}, headers=headers))
    client.post("/glossary", data={"name": "duplicate"}, headers=headers)
    assert "canonical title or alias conflicts" in error_of(client.post("/glossary", data={"name": "duplicate"}, headers=headers))
    assert "operation failed" in error_of(client.post("/w/ws/glossary/9/delete", headers=headers))
    assert "operation failed" in error_of(client.post("/glossary/9/delete", headers=headers))
    assert "candidate" in error_of(client.post("/w/ws/glossary/candidates/kc-00000000000000000000/accept", headers=headers))
    assert "candidate" in error_of(client.post("/glossary/candidates/ws/kc-00000000000000000000/accept", headers=headers))


def test_legacy_workspace_write_rejects_unrelated_serve_root(tmp_path):
    """P2-9：兼容 add/remove 在读写前校验显式 root 归属。"""
    import pytest
    root = tmp_path / "root"
    other = tmp_path / "other"
    root.mkdir()
    other.mkdir()
    ws = Workspace.init(root / "ws")
    with pytest.raises(ValueError):
        ws.add_glossary_entry("不能写", serve_root=other)
    with pytest.raises(ValueError):
        ws.remove_glossary_entry(0, serve_root=other)


def test_readme_v2_example_has_strict_audit_fields(tmp_path):
    """P2-10：README 的 v2 片段可被严格知识仓储加载。"""
    import yaml
    from kairo.knowledge import KnowledgeDocument, validate_entries
    text = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")
    block = text.split("```yaml\nknowledge:\n", 1)[1].split("```", 1)[0]
    document = KnowledgeDocument.model_validate(yaml.safe_load("knowledge:\n" + block)["knowledge"])
    validate_entries(document.entries, scope="workspace")
