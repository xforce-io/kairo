"""#165 Digest 候选与两级审核。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from kairo.engine import step
from kairo.glossary import load_glossary_file, load_workspace_glossary
from kairo.glossary_review import (
    STATUS_IGNORED,
    STATUS_PENDING,
    STATUS_PENDING_ROOT,
    STATUS_ROOT_REJECTED,
    accept_root,
    accept_workspace,
    extract_after_digest,
    ignore_candidate,
    ingest_candidates,
    invalidate_stale,
    load_review,
    mark_extract_error,
    open_candidates,
    promote_candidate,
    reject_root,
)
from kairo.provider import StubProvider
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


def test_web_review_actions(tmp_path):
    ws, rid, root = _ws_with_digest(tmp_path)
    ingest_candidates(ws.root, rid, [{"name": "天溯", "quote": "天溯系统"}])
    cid = open_candidates(ws.root)[0].id
    c = TestClient(create_app(root))
    page = c.get("/w/ws/glossary")
    assert page.status_code == 200
    assert "天溯" in page.text
    r = c.post(f"/w/ws/glossary/candidates/{cid}/ignore")
    assert r.status_code == 200
    assert open_candidates(ws.root) == []
