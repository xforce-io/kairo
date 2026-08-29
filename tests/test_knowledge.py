"""#182 知识条目、匹配器、审核与 Web 主路径。"""

import re

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
    accept_global,
    accept_workspace,
    ingest_candidates,
    invalidate_stale,
    load_review,
    promote,
)
from kairo.provider import StubProvider
from kairo.web.server import create_app
from kairo.workspace import Workspace
from kairo.models import ProductState


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
        sources=[KnowledgeSource(kind="reference", path="references/x/manifest.yaml")],
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


def test_knowledge_drift_is_visible_and_offers_manual_restep(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws")
    entry = new_entry(title="current", scope="workspace")
    save_workspace(ws.root, load_workspace(ws.root)[0].model_copy(update={"entries": [entry]}))
    state = ws.read_state()
    state.products["references/r/digest.md"] = ProductState(input_hash="x", knowledge_hash="old")
    ws.write_state(state)
    page = TestClient(create_app(root)).get("/knowledge?workspace=ws", headers={"accept-language": "en"})
    assert "Knowledge context drift" in page.text
    assert 'name="target" value="references/r/digest.md"' in page.text
    assert "Re-step with current knowledge" in page.text


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
    error = load_review(ws.root).extract_errors["references/r/digest.md"]
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
