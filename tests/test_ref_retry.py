"""#73: reference blocked 可见 + retry 清终态后可重跑。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from kairo.engine import clear_reference_products, ref_product_blocks, retry_reference, step
from kairo.models import ProductState, State
from kairo.provider import StubProvider
from kairo.web.server import create_app
from kairo.workspace import Workspace


def _ws_with_audio(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = Workspace.init(tmp_path / "ws")
    a = tmp_path / "rec.m4a"
    a.write_bytes(b"fake")
    rid = ws.add([a])
    return ws, rid


def test_ref_product_blocks_lists_asr_failed(tmp_path, monkeypatch):
    ws, rid = _ws_with_audio(tmp_path, monkeypatch)
    key = f"references/{rid}/transcript.md"
    state = ws.read_state()
    state.products[key] = ProductState(
        input_hash="h", status="blocked", reason="asr-failed"
    )
    ws.write_state(state)
    blocks = ref_product_blocks(ws, rid)
    assert len(blocks) == 1
    assert blocks[0]["name"] == "transcript.md"
    assert blocks[0]["reason"] == "asr-failed"


def test_clear_and_retry_reference_recovers_from_asr_failed(tmp_path, monkeypatch):
    ws, rid = _ws_with_audio(tmp_path, monkeypatch)
    key = f"references/{rid}/transcript.md"
    # input_hash 与源 form 一致 → asr-failed 才是真正终态(hash 变会自动重试)
    src_hash = ws.read_manifest(rid).forms[0].hash
    state = ws.read_state()
    state.products[key] = ProductState(
        input_hash=src_hash, status="blocked", reason="asr-failed"
    )
    ws.write_state(state)
    # 普通 step 不会清掉 asr-failed 终态
    step(ws, StubProvider())
    assert ws.read_state().products[key].status == "blocked"
    assert ws.read_state().products[key].reason == "asr-failed"

    clear_reference_products(ws, rid)
    assert key not in ws.read_state().products
    progressed = retry_reference(ws, StubProvider(), rid)
    assert progressed
    assert (ws.root / f"references/{rid}/transcript.md").is_file()
    assert (ws.root / f"references/{rid}/digest.md").is_file()
    ps = ws.read_state().products.get(key)
    assert ps is not None and ps.status != "blocked"


def test_web_ref_shows_blocks_and_retry_button(tmp_path, monkeypatch):
    ws, rid = _ws_with_audio(tmp_path, monkeypatch)
    key = f"references/{rid}/transcript.md"
    state = ws.read_state()
    state.products[key] = ProductState(
        input_hash="h", status="blocked", reason="asr-failed"
    )
    ws.write_state(state)
    r = TestClient(create_app(tmp_path)).get(f"/w/ws/ref/{rid}")
    assert r.status_code == 200
    assert "asr-failed" in r.text
    assert "transcript.md" in r.text
    assert f'hx-post="/w/ws/ref/{rid}/retry"' in r.text
    assert "重新处理" in r.text or "Reprocess" in r.text


def test_web_retry_starts_task(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    import os

    os.environ["KAIRO_STUB"] = "1"
    ws, rid = _ws_with_audio(tmp_path, monkeypatch)
    key = f"references/{rid}/transcript.md"
    state = ws.read_state()
    state.products[key] = ProductState(
        input_hash="h", status="blocked", reason="asr-failed"
    )
    ws.write_state(state)
    r = TestClient(create_app(tmp_path)).post(f"/w/ws/ref/{rid}/retry")
    assert r.status_code == 200
    assert "stream" in r.text
