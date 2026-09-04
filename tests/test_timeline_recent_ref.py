"""#259: Timeline 默认 Recent；Ref 页 digest markdown + 来源形态右侧预览。"""

from __future__ import annotations

import datetime as dt
import re

from fastapi.testclient import TestClient

from kairo.refs import add_global_ref, global_home
from kairo.timeline import resolve_timeline_query
from kairo.web.server import create_app


def _client(root):
    return TestClient(create_app(root))


def _serve_with_digest(tmp_path):
    serve = tmp_path / "root"
    serve.mkdir()
    src = tmp_path / "note.md"
    src.write_text("# 原料标题\n\n源形态**正文**。\n", encoding="utf-8")
    rid = add_global_ref(serve, [src], ref_id="loose-note", title="未归档笔记", copy=True)
    digest = global_home(serve).references_dir() / rid / "digest.md"
    digest.write_text("# 结论标题\n\n- 要点一\n", encoding="utf-8")
    return serve, rid


def test_resolve_timeline_query_defaults_to_recent():
    today = dt.date(2026, 8, 25)
    q = resolve_timeline_query(today=today)
    assert q.view == "recent"
    assert q.day == today
    day = resolve_timeline_query(day="2026-08-24", today=today)
    assert day.view == "calendar" and day.day == dt.date(2026, 8, 24)


def test_timeline_root_selects_recent_tab(tmp_path):
    serve, rid = _serve_with_digest(tmp_path)
    r = _client(serve).get("/timeline")
    assert r.status_code == 200
    assert re.search(r'<a class="on" href="/timeline\?mode=recent">', r.text)
    assert rid in r.text
    assert "/timeline?day=" in r.text
    cal = _client(serve).get("/timeline", params={"day": "2026-08-24"})
    assert cal.status_code == 200
    assert re.search(r'<a class="on" href="/timeline\?day=', cal.text)


def test_global_ref_digest_renders_markdown(tmp_path):
    serve, rid = _serve_with_digest(tmp_path)
    r = _client(serve).get(f"/refs/{rid}")
    assert r.status_code == 200
    digest = re.search(
        r'<section class="ref-section">\s*<h2>[^<]*</h2>\s*(<article class="doc">[\s\S]*?</article>)',
        r.text,
    )
    assert digest is not None, "digest section must render an article.doc"
    html = digest.group(1)
    assert "<h1>结论标题</h1>" in html
    assert "<li>" in html
    assert "# 结论标题" not in html
    assert re.search(r'<article class="ref-digest">', r.text) is None


def test_global_ref_source_form_preview_drawer_reuses_form_render(tmp_path):
    serve, rid = _serve_with_digest(tmp_path)
    client = _client(serve)
    page = client.get(f"/refs/{rid}")
    assert page.status_code == 200
    assert 'id="form-drawer"' in page.text
    assert 'hx-target="#form-preview"' in page.text
    match = re.search(rf'hx-get="(/refs/{rid}/form/\d+(?:\?[^"]*)?)"', page.text)
    assert match is not None
    preview = client.get(match.group(1))
    assert preview.status_code == 200
    assert "<h1>" in preview.text
    assert "原料标题" in preview.text
    assert "源形态" in preview.text
    assert "<strong>" in preview.text or "<b>" in preview.text


def _write_wav(path):
    path.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")


def test_global_ref_audio_form_preview_returns_listen_read(tmp_path):
    """#265: Global Ref audio preview must return listen-read UI, not 404 'not previewable'."""
    serve = tmp_path / "root"
    serve.mkdir()
    audio = tmp_path / "talk.wav"
    _write_wav(audio)
    transcript = tmp_path / "talk.md"
    transcript.write_text("[00:10] test content\n[00:20] more content\n", encoding="utf-8")
    rid = add_global_ref(
        serve, [audio, transcript], ref_id="audio-ref", title="Audio Reference", copy=True
    )
    client = _client(serve)
    page = client.get(f"/refs/{rid}")
    assert page.status_code == 200
    match = re.search(rf'hx-get="(/refs/{rid}/form/0(?:\?[^"]*)?)"', page.text)
    assert match is not None, "audio form preview link must exist"
    preview = client.get(match.group(1))
    assert preview.status_code == 200, "audio preview must return 200, not 404"
    assert 'class="listen-read"' in preview.text, "audio preview must render listen-read UI"
    assert 'class="lr-audio"' in preview.text
    assert 'class="lr-units"' in preview.text
    assert "test content" in preview.text
    assert f"/refs/{rid}/file/0" in preview.text, "audio src must use global ref file URL"


def test_global_ref_audio_with_transcript_shows_timed_units(tmp_path):
    """#265: Global Ref audio with transcript should show timed units and use correct URLs/targets."""
    serve = tmp_path / "root"
    serve.mkdir()
    audio = tmp_path / "talk.wav"
    _write_wav(audio)
    transcript = tmp_path / "talk.md"
    transcript.write_text("[00:10] first unit\n[00:20] second unit\n", encoding="utf-8")
    from kairo.refs import global_home

    rid = "paired-audio"
    add_global_ref(serve, [audio, transcript], ref_id=rid, title="Paired Audio", copy=True)
    ws = global_home(serve)
    man = ws.read_manifest(rid)
    audio_idx = next(i for i, f in enumerate(man.forms) if f.role == "audio")
    transcript_idx = next(i for i, f in enumerate(man.forms) if f.role == "transcript")
    man.forms[transcript_idx].origin = "asr-from:" + man.forms[audio_idx].hash
    ws.write_manifest(rid, man)
    client = _client(serve)
    preview = client.get(f"/refs/{rid}/form/{audio_idx}")
    assert preview.status_code == 200
    assert 'class="listen-read"' in preview.text
    assert "first unit" in preview.text, "transcript units must be rendered"
    assert "second unit" in preview.text
    assert f"/refs/{rid}/file/{audio_idx}" in preview.text, "audio src must use global ref file URL"
    assert "/w/" not in preview.text, "global ref must not use /w/ workspace URLs"


def test_global_ref_image_form_preview_still_works(tmp_path):
    """Ensure image preview not regressed by audio fix."""
    serve = tmp_path / "root"
    serve.mkdir()
    img = tmp_path / "pic.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    rid = add_global_ref(serve, [img], ref_id="img-ref", title="Image Reference", copy=True)
    client = _client(serve)
    preview = client.get(f"/refs/{rid}/form/0")
    assert preview.status_code == 200
    assert 'class="doc-img"' in preview.text
    assert f"/refs/{rid}/file/0" in preview.text
