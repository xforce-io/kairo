"""#182 知识条目、匹配器、审核与 Web 主路径。"""

from pathlib import Path

from fastapi.testclient import TestClient

from kairo.engine import step
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
    result = matcher.match("LOCAL AlphaX alpha beta", budget=MatchBudget(max_entries=1, max_chars=20))
    assert "local" in result.ambiguities
    assert [match.entry.title for match in result.matches] == ["Alpha"]
    assert result.truncated_count == 1
    assert matcher.suggest(["ＡＬＰＨＡ", "未知"]) == {"ＡＬＰＨＡ": "known", "未知": "unknown"}


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
    promote(ws.root, candidate.id)
    global_entry = accept_global(root, ws.root, candidate.id)
    assert global_entry.scope == "global"
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
    assert "还没有本地知识条目" in page.text
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
