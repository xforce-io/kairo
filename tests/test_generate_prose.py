"""#60: 单 ref 按需生成 prose(不改 constitution、不进 digest)。"""

from __future__ import annotations

import yaml

from kairo.engine import can_generate_prose, generate_prose, step
from kairo.models import Form, Manifest, State
from kairo.provider import StubProvider
from kairo.rules import DigestRule, NormalizeRule, TransformRule
from kairo.workspace import Workspace


def _add_audio(ws, tmp_path, name="rec.m4a"):
    a = tmp_path / name
    a.write_bytes(b"fake audio bytes")
    return ws.add([a])


def _machine_transcript(ws, tmp_path, monkeypatch):
    """ASR 派生 transcript(origin≠added);normalize 默认关。"""
    monkeypatch.setenv("KAIRO_STUB", "1")
    rid = _add_audio(ws, tmp_path)
    TransformRule(ws).discover()[0].run(State())
    return rid


def test_can_generate_prose_true_for_stream_machine_transcript(tmp_path, monkeypatch):
    ws = Workspace.init(tmp_path)
    rid = _machine_transcript(ws, tmp_path, monkeypatch)
    assert can_generate_prose(ws, rid) is True


def test_can_generate_prose_false_when_normalize_would_also_skip(tmp_path, monkeypatch):
    """人给文本 / 已有 prose / corpus → 不可生成。"""
    ws = Workspace.init(tmp_path)
    t = tmp_path / "note.txt"
    t.write_text("人给原文")
    human = ws.add([t])
    assert can_generate_prose(ws, human) is False

    rid = _machine_transcript(ws, tmp_path, monkeypatch)
    (ws.root / f"references/{rid}/prose.md").write_text("已有")
    m = ws.read_manifest(rid)
    m.forms.append(
        Form(
            role="prose",
            location=f"references/{rid}/prose.md",
            hash="x",
            origin="normalize-from:x",
        )
    )
    ws.write_manifest(rid, m)
    assert can_generate_prose(ws, rid) is False


def test_can_generate_prose_false_for_corpus(tmp_path):
    ws = Workspace.init(tmp_path)
    rid = "c1"
    (ws.references_dir() / rid).mkdir(parents=True)
    (ws.root / f"references/{rid}/transcript.md").write_text("派生")
    ws.write_manifest(
        rid,
        Manifest(
            id=rid,
            source_class="corpus",
            forms=[
                Form(
                    role="transcript",
                    location=f"references/{rid}/transcript.md",
                    hash="h",
                    origin="asr-stub-from:x",
                )
            ],
        ),
    )
    assert can_generate_prose(ws, rid) is False


def test_generate_prose_writes_file_form_and_products(tmp_path, monkeypatch):
    ws = Workspace.init(tmp_path)
    rid = _machine_transcript(ws, tmp_path, monkeypatch)
    assert not (ws.root / f"references/{rid}/prose.md").exists()
    # constitution 仍默认关
    con = yaml.safe_load((ws.root / "constitution.yaml").read_text())
    assert not (con.get("pipeline") or {}).get("normalize", {}).get("enabled", False)

    key = generate_prose(ws, StubProvider(), rid)
    assert key == f"references/{rid}/prose.md"
    prose = ws.root / key
    assert prose.is_file()
    assert "STUB TRANSCRIPT" in prose.read_text()
    forms = {f.role: f for f in ws.read_manifest(rid).forms}
    assert "prose" in forms
    assert forms["prose"].origin.startswith("normalize-from:")
    state = ws.read_state()
    assert key in state.products
    # 仍不写回 constitution
    con2 = yaml.safe_load((ws.root / "constitution.yaml").read_text())
    assert not (con2.get("pipeline") or {}).get("normalize", {}).get("enabled", False)
    assert can_generate_prose(ws, rid) is False


def test_generate_prose_errors(tmp_path, monkeypatch):
    from kairo.engine import ProseError

    ws = Workspace.init(tmp_path)
    try:
        generate_prose(ws, StubProvider(), "no-such")
        raise AssertionError("expected ProseError")
    except ProseError as e:
        assert e.code == "unknown-ref"

    t = tmp_path / "note.txt"
    t.write_text("人给")
    human = ws.add([t])
    try:
        generate_prose(ws, StubProvider(), human)
        raise AssertionError("expected ProseError")
    except ProseError as e:
        assert e.code == "no-machine-transcript"

    rid = _machine_transcript(ws, tmp_path, monkeypatch)
    generate_prose(ws, StubProvider(), rid)
    try:
        generate_prose(ws, StubProvider(), rid)
        raise AssertionError("expected ProseError")
    except ProseError as e:
        assert e.code == "prose-exists"


def test_generate_prose_rejects_corpus(tmp_path):
    from kairo.engine import ProseError

    ws = Workspace.init(tmp_path)
    rid = "c1"
    (ws.references_dir() / rid).mkdir(parents=True)
    (ws.root / f"references/{rid}/transcript.md").write_text("派生")
    ws.write_manifest(
        rid,
        Manifest(
            id=rid,
            source_class="corpus",
            forms=[
                Form(
                    role="transcript",
                    location=f"references/{rid}/transcript.md",
                    hash="h",
                    origin="asr-stub-from:x",
                )
            ],
        ),
    )
    try:
        generate_prose(ws, StubProvider(), rid)
        raise AssertionError("expected ProseError")
    except ProseError as e:
        assert e.code == "not-stream"


def test_generate_prose_does_not_affect_digest_product_or_body(tmp_path, monkeypatch):
    """#33: 生成 prose 后 digest 不 stale、digest 正文仍不含 prose。"""
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = Workspace.init(tmp_path)
    rid = _add_audio(ws, tmp_path)
    step(ws, StubProvider())
    digest_key = f"references/{rid}/digest.md"
    before = ws.read_state().products[digest_key]
    digest_before = (ws.root / digest_key).read_text()

    generate_prose(ws, StubProvider(), rid)
    assert (ws.root / f"references/{rid}/prose.md").is_file()

    state = ws.read_state()
    after = state.products[digest_key]
    assert after.input_hash == before.input_hash
    assert (ws.root / digest_key).read_text() == digest_before
    # DigestRule 读 body 仍不包含 prose 文件内容
    body = DigestRule(ws, StubProvider())._read_body(ws.read_manifest(rid))
    assert body is not None
    assert "prose" not in body.lower() or "STUB TRANSCRIPT" in body
    # normalize 默认关时 step discover 仍不产第二份
    assert NormalizeRule(ws, StubProvider()).discover() == []


def test_step_still_skips_prose_when_disabled_after_api_exists(tmp_path, monkeypatch):
    """回归:normalize 默认关 → step 不产 prose(即便 generate_prose API 存在)。"""
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = Workspace.init(tmp_path)
    rid = _add_audio(ws, tmp_path)
    step(ws, StubProvider())
    assert not (ws.root / f"references/{rid}/prose.md").exists()
