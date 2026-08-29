"""#165 Digest 候选与两级审核。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from kairo.engine import step
from kairo.glossary import load_glossary_file, load_workspace_glossary
from kairo.glossary_review import (
    STATUS_IGNORED,
    STATUS_PENDING_ROOT,
    STATUS_ROOT_REJECTED,
    accept_root,
    accept_workspace,
    extract_after_digest,
    ignore_candidate,
    ingest_candidates,
    invalidate_stale,
    load_review,
    open_candidates,
    promote_candidate,
    reject_root,
    todo_count,
)
from kairo.provider import AgentResult, StubProvider
from kairo.web.server import create_app
from kairo.workspace import Workspace


def _ws_with_digest(tmp_path) -> tuple[Workspace, str, Path]:
    root = tmp_path / "root"
    root.mkdir()
    ws = Workspace.init(root / "ws", topic="t")
    src = root / "n.txt"
    src.write_text("讨论天溯系统")
    rid = ws.add([src])
    step(ws, provider=StubProvider())
    digest = ws.root / "references" / rid / "digest.md"
    # 保证证据原文在 digest 中
    text = digest.read_text()
    if "天溯系统" not in text:
        digest.write_text(text + "\n天溯系统\n")
    return ws, rid, root


def test_ingest_requires_quote_in_digest(tmp_path):
    ws, rid, _ = _ws_with_digest(tmp_path)
    ingest_candidates(
        ws.root,
        rid,
        [{"name": "天溯", "quote": "天溯系统"}],
    )
    open_ = open_candidates(ws.root)
    assert len(open_) == 1
    assert open_[0].name == "天溯"
    ingest_candidates(ws.root, rid, [{"name": "无证据", "quote": "不存在的话"}])
    assert [c.name for c in open_candidates(ws.root)] == ["天溯"]


def test_ignore_suppresses_same_fingerprint(tmp_path):
    ws, rid, _ = _ws_with_digest(tmp_path)
    ingest_candidates(ws.root, rid, [{"name": "天溯", "quote": "天溯系统"}])
    cid = open_candidates(ws.root)[0].id
    ignore_candidate(ws.root, cid)
    ingest_candidates(ws.root, rid, [{"name": "天溯", "quote": "天溯系统"}])
    assert open_candidates(ws.root) == []
    assert any(c.status == STATUS_IGNORED for c in load_review(ws.root).candidates)


def test_accept_writes_workspace_only(tmp_path):
    ws, rid, root = _ws_with_digest(tmp_path)
    ingest_candidates(ws.root, rid, [{"name": "天溯", "quote": "天溯系统"}])
    cid = open_candidates(ws.root)[0].id
    accept_workspace(ws, cid)
    assert load_workspace_glossary(ws.root)[0].name == "天溯"
    assert not (root / "glossary.yaml").exists() or load_glossary_file(root / "glossary.yaml") == []


def test_extract_error_does_not_change_digest(tmp_path):
    ws, rid, _ = _ws_with_digest(tmp_path)
    digest = ws.root / "references" / rid / "digest.md"
    before = digest.read_bytes()

    def boom(*_a, **_k):
        raise RuntimeError("extract-boom")

    extract_after_digest(ws, rid, digest.read_text(), extractor=boom)
    assert digest.read_bytes() == before
    assert "extract-boom" in load_review(ws.root).extract_errors[rid]


def test_digest_success_uses_provider_to_create_review_candidate(tmp_path):
    class CandidateProvider(StubProvider):
        def run(self, config, signal=None):
            if config.artifact == "knowledge-candidates.yaml":
                config.artifact_dir.mkdir(parents=True, exist_ok=True)
                path = config.artifact_dir / "knowledge-candidates.yaml"
                path.write_text(
                    "- title: 天溯\n  description: 系统名称\n  quote: 天溯系统\n"
                )
                return AgentResult(artifacts=[path], result_text=path.read_text())
            return super().run(config, signal)

    ws, rid, _ = _ws_with_digest(tmp_path)
    # 重新处理 digest，验证正式规则链路而不是直接调用存储层。
    ws.root.joinpath("references", rid, "digest.md").unlink()
    state = ws.read_state()
    del state.products[f"references/{rid}/digest.md"]
    ws.write_state(state)

    step(ws, provider=CandidateProvider())

    from kairo.knowledge_review import open_candidates as knowledge_open_candidates

    candidates = knowledge_open_candidates(ws.root)
    assert len(candidates) == 1
    assert candidates[0].title == "天溯"
    assert candidates[0].quote == "天溯系统"


def test_delete_ref_invalidates_pending(tmp_path):
    ws, rid, _ = _ws_with_digest(tmp_path)
    ingest_candidates(ws.root, rid, [{"name": "天溯", "quote": "天溯系统"}])
    assert open_candidates(ws.root)
    import shutil

    shutil.rmtree(ws.root / "references" / rid)
    invalidate_stale(ws.root)
    assert open_candidates(ws.root) == []


def test_root_reject_does_not_write_root(tmp_path):
    ws, rid, root = _ws_with_digest(tmp_path)
    ingest_candidates(ws.root, rid, [{"name": "天溯", "quote": "天溯系统"}])
    cid = open_candidates(ws.root)[0].id
    promote_candidate(ws.root, cid)
    assert load_review(ws.root).candidates[0].status == STATUS_PENDING_ROOT
    reject_root(ws.root, cid, "仅本课题")
    assert not (root / "glossary.yaml").exists()
    c = open_candidates(ws.root)[0]
    assert c.status == STATUS_ROOT_REJECTED
    assert c.reject_reason == "仅本课题"


def test_root_accept_writes_root_not_local(tmp_path):
    ws, rid, root = _ws_with_digest(tmp_path)
    ingest_candidates(ws.root, rid, [{"name": "天溯", "quote": "天溯系统"}])
    cid = open_candidates(ws.root)[0].id
    promote_candidate(ws.root, cid)
    accept_root(root, "ws", cid)
    assert load_glossary_file(root / "glossary.yaml")[0].name == "天溯"
    assert load_workspace_glossary(ws.root) == []


def test_todo_count_sums_open_extract_and_pending(tmp_path):
    ws, rid, _ = _ws_with_digest(tmp_path)
    assert todo_count(ws.root, pending=[]) == 0
    ingest_candidates(ws.root, rid, [{"name": "天溯", "quote": "天溯系统"}])
    assert todo_count(ws.root, pending=[]) == 1
    store = load_review(ws.root)
    store.extract_errors[rid] = "boom"
    from kairo.glossary_review import save_review

    save_review(ws.root, store)
    assert todo_count(ws.root, pending=["references/x/digest.md"]) == 3


def test_web_review_actions(tmp_path):
    ws, rid, root = _ws_with_digest(tmp_path)
    ingest_candidates(ws.root, rid, [{"name": "天溯", "quote": "天溯系统"}])
    cid = open_candidates(ws.root)[0].id
    c = TestClient(create_app(root))
    page = c.get("/glossary?workspace=ws")
    assert page.status_code == 200
    assert "天溯" in page.text
    r = c.post(f"/w/ws/glossary/candidates/{cid}/ignore")
    assert r.status_code == 200
    assert open_candidates(ws.root) == []


def test_web_promote_then_root_reject_on_console(tmp_path):
    """#174 S4: 提交公共出现在上半待提升；拒绝退回该区待审核。"""
    ws, rid, root = _ws_with_digest(tmp_path)
    ingest_candidates(ws.root, rid, [{"name": "天溯", "quote": "天溯系统"}])
    cid = open_candidates(ws.root)[0].id
    c = TestClient(create_app(root))
    r = c.post(f"/w/ws/glossary/candidates/{cid}/promote")
    assert r.status_code == 200
    bare = c.get("/glossary")
    assert f"/glossary/candidates/ws/{cid}/reject" in bare.text
    assert "天溯" in bare.text
    c.post(f"/glossary/candidates/ws/{cid}/reject", data={"reason": "本课题专用"})
    selected = c.get("/glossary?workspace=ws")
    assert "returned from root review" in selected.text
    assert "本课题专用" in selected.text
    assert load_review(ws.root).candidates[0].status == STATUS_ROOT_REJECTED


def test_workspace_hides_actions_after_candidate_is_submitted_to_root(tmp_path):
    ws, rid, root = _ws_with_digest(tmp_path)
    ingest_candidates(ws.root, rid, [{"name": "天溯", "quote": "天溯系统"}])
    cid = open_candidates(ws.root)[0].id
    promote_candidate(ws.root, cid)

    page = TestClient(create_app(root)).get("/glossary?workspace=ws")

    assert page.status_code == 200
    assert "awaiting root review" in page.text
    assert f"/candidates/{cid}/accept" not in page.text
    assert f"/candidates/{cid}/ignore" not in page.text
    assert f"/candidates/{cid}/promote" not in page.text
