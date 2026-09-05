"""#259: Timeline 默认 Recent；Ref 页 digest markdown + 来源形态右侧预览。"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from fastapi.testclient import TestClient

from kairo.refs import add_global_ref, global_home
from kairo.timeline import resolve_timeline_query
from kairo.web.server import create_app


def _ref_head(html: str) -> str:
    match = re.search(r'<header class="ref-head">([\s\S]*?)</header>', html)
    assert match is not None, "Ref page must have header.ref-head"
    return match.group(1)


def _occurred_line(html: str) -> str:
    head = _ref_head(html)
    match = re.search(r'<p class="ref-occurred">([^<]*)</p>', head)
    assert match is not None, "occurred metadata must be a p.ref-occurred under the title"
    return match.group(1).strip()


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


def test_global_ref_shows_occurred_day_not_raw_id(tmp_path):
    serve = tmp_path / "root"
    serve.mkdir()
    src = tmp_path / "note.md"
    src.write_text("note", encoding="utf-8")
    rid = add_global_ref(
        serve,
        [src],
        ref_id="2026-09-02-20260902-164853",
        title="能源项目-260902",
        copy=True,
        occurred_at="2026-09-02",
    )
    html = _client(serve).get(f"/refs/{rid}").text
    line = _occurred_line(html)
    assert "2026-09-02" in line
    assert rid not in line
    head = _ref_head(html)
    assert re.search(rf'<p class="ref-id">\s*{re.escape(rid)}\s*</p>', head)


def test_global_ref_shows_unknown_occurred_copy(tmp_path):
    serve = tmp_path / "root"
    serve.mkdir()
    src = tmp_path / "note.md"
    src.write_text("note", encoding="utf-8")
    rid = add_global_ref(
        serve, [src], ref_id="notes-candidate", title="未标日期", copy=True
    )
    html = _client(serve).get(f"/refs/{rid}").text
    line = _occurred_line(html)
    assert line == "Unknown occurred date"
    assert rid not in line


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


def test_global_ref_multi_track_switcher_uses_form_preview_target(tmp_path):
    """#265: Global Ref multi-track switcher must target #form-preview with correct URLs."""
    from kairo.models import Form, Manifest
    from kairo.refs import global_home

    serve = tmp_path / "root"
    serve.mkdir()
    ws = global_home(serve)
    rid = "multi-audio"
    rdir = ws.references_dir() / rid
    rdir.mkdir(parents=True)
    a1, a2 = rdir / "a1.wav", rdir / "a2.wav"
    _write_wav(a1)
    _write_wav(a2)
    (rdir / "t1.md").write_text("[00:10] track one\n", encoding="utf-8")
    (rdir / "t2.md").write_text("[00:15] track two\n", encoding="utf-8")
    ws.write_manifest(
        rid,
        Manifest(
            id=rid,
            title="Multi Audio",
            forms=[
                Form(role="audio", location=str(a1), hash="h1"),
                Form(role="audio", location=str(a2), hash="h2"),
                Form(role="transcript", location=f"references/{rid}/t1.md", hash="t1", origin="asr-from:h1"),
                Form(role="transcript", location=f"references/{rid}/t2.md", hash="t2", origin="asr-from:h2"),
            ],
        ),
    )
    client = _client(serve)
    preview = client.get(f"/refs/{rid}/form/0")
    assert preview.status_code == 200
    assert 'class="lr-switch"' in preview.text, "switcher must render for multi-track"
    assert 'hx-target="#form-preview"' in preview.text, "switcher must target #form-preview"
    assert f"/refs/{rid}/form/1" in preview.text, "switcher must use global ref form URL"
    assert "/w/" not in preview.text, "global ref must not use /w/ workspace URLs"
    assert "track one" in preview.text


def test_global_ref_with_topic_home_switcher_urls_include_query(tmp_path):
    """#265: Topic-homed ref switcher URLs must have query AFTER key: /refs/{id}/form/{key}?home=slug."""
    from kairo.models import Form, Manifest
    from kairo.workspace import Workspace

    serve = tmp_path / "root"
    serve.mkdir()
    ws = Workspace.init(serve / "my-topic", topic="my-topic")
    rid = "topic-multi"
    rdir = ws.references_dir() / rid
    rdir.mkdir(parents=True)
    a1, a2 = rdir / "a1.wav", rdir / "a2.wav"
    _write_wav(a1)
    _write_wav(a2)
    (rdir / "t1.md").write_text("[00:10] topic track one\n", encoding="utf-8")
    (rdir / "t2.md").write_text("[00:15] topic track two\n", encoding="utf-8")
    ws.write_manifest(
        rid,
        Manifest(
            id=rid,
            title="Topic Multi",
            forms=[
                Form(role="audio", location=str(a1), hash="h1"),
                Form(role="audio", location=str(a2), hash="h2"),
                Form(role="transcript", location=f"references/{rid}/t1.md", hash="t1", origin="asr-from:h1"),
                Form(role="transcript", location=f"references/{rid}/t2.md", hash="t2", origin="asr-from:h2"),
            ],
        ),
    )
    client = _client(serve)
    preview = client.get(f"/refs/{rid}/form/0?home=my-topic")
    assert preview.status_code == 200
    assert 'class="lr-switch"' in preview.text
    expected_url = "/refs/topic-multi/form/1?home=my-topic"
    assert expected_url in preview.text, f"switcher URL must be {expected_url}"
    assert "?home=my-topic/1" not in preview.text, "query must come AFTER key, not before"
    assert 'hx-target="#form-preview"' in preview.text


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


def _css_rules(css: str) -> list[tuple[str, str]]:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out = []
    for m in re.finditer(r"([^{}]+)\{([^{}]+)\}", css):
        out.append((re.sub(r"\s+", " ", m.group(1)).strip(), m.group(2)))
    return out


def _decl_map(body: str) -> dict[str, str]:
    decls: dict[str, str] = {}
    for part in body.split(";"):
        part = part.strip()
        if ":" not in part:
            continue
        name, value = part.split(":", 1)
        decls[name.strip()] = re.sub(r"\s+", " ", value).strip()
    return decls


def _merged_decls(css: str, pred) -> dict[str, str]:
    merged: dict[str, str] = {}
    for sel, body in _css_rules(css):
        if pred(sel):
            merged.update(_decl_map(body))
    return merged


def _selector_token(sel: str, token: str) -> bool:
    for piece in sel.split(","):
        piece = piece.strip()
        if piece == token or piece.startswith(token + " ") or piece.startswith(token + ":") or piece.startswith(token + "["):
            return True
    return False


def test_global_ref_audio_preview_exposes_listen_read_controls(tmp_path):
    """#285: Preview 链必须带 Play/seek/搜索，音频 URL 仍走全局 Ref 文件。"""
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
    assert 'id="form-drawer"' in page.text
    match = re.search(rf'hx-get="(/refs/{rid}/form/0(?:\?[^"]*)?)"', page.text)
    assert match is not None, "audio form preview link must exist"
    preview = client.get(match.group(1))
    assert preview.status_code == 200
    assert 'class="listen-read"' in preview.text
    assert 'class="lr-play"' in preview.text
    assert "data-lr-play" in preview.text
    assert 'class="lr-seek"' in preview.text
    assert "data-lr-seek" in preview.text
    assert 'class="lr-search"' in preview.text
    assert "data-lr-q" in preview.text
    assert 'class="lr-units"' in preview.text
    assert "test content" in preview.text
    src = re.search(r'<audio[^>]*\ssrc="([^"]+)"', preview.text)
    assert src is not None, "listen-read audio element must have src"
    assert src.group(1).startswith(f"/refs/{rid}/file/"), src.group(1)
    assert "/w/" not in preview.text


def test_form_drawer_listen_read_host_css_matches_reader(tmp_path):
    """#285: 抽屉含听读时必须与 #reader:has(.listen-read) 同类钉住头底栏。"""
    shipped = Path("src/kairo/web/static/app.css").read_text(encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    served = _client(root).get("/static/app.css")
    assert served.status_code == 200
    assert served.text == shipped

    host = _merged_decls(
        shipped,
        lambda s: "form-drawer" in s and ":has(.listen-read)" in s,
    )
    assert host, "form drawer must declare :has(.listen-read) host rules"
    assert host.get("overflow") == "hidden"
    assert host.get("min-height") == "0"
    assert host.get("flex-direction") == "column" or host.get("display") == "flex"

    doc = _merged_decls(
        shipped,
        lambda s: "form-drawer" in s and ":has(.listen-read)" in s and ".doc" in s,
    )
    assert doc.get("display") == "flex"
    assert doc.get("flex-direction") == "column"
    assert doc.get("min-height") == "0"
    assert doc.get("overflow") == "hidden"

    units = _merged_decls(shipped, lambda s: _selector_token(s, ".lr-units"))
    assert units.get("overflow") == "auto"
    assert units.get("min-height") == "0"

    head = _merged_decls(shipped, lambda s: _selector_token(s, ".lr-head"))
    assert head.get("flex") == "none"

    dock = _merged_decls(shipped, lambda s: _selector_token(s, ".lr-dock"))
    assert dock.get("flex") == "none"

    default_body = _merged_decls(
        shipped,
        lambda s: ":has(" not in s and ".doc" not in s and _selector_token(s, ".form-drawer-body"),
    )
    assert default_body.get("overflow") == "auto"


def test_global_ref_non_audio_preview_stays_document_not_listen_read(tmp_path):
    """#285: 文本/图片 Preview 不套听读三区。"""
    serve, rid = _serve_with_digest(tmp_path)
    client = _client(serve)
    page = client.get(f"/refs/{rid}")
    assert page.status_code == 200
    match = re.search(rf'hx-get="(/refs/{rid}/form/\d+(?:\?[^"]*)?)"', page.text)
    assert match is not None
    preview = client.get(match.group(1))
    assert preview.status_code == 200
    assert 'class="listen-read"' not in preview.text
    assert 'class="lr-play"' not in preview.text
    assert "<h1>" in preview.text
    assert "原料标题" in preview.text

    img = tmp_path / "pic.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    img_id = add_global_ref(serve, [img], ref_id="img-ref", title="Image Reference", copy=True)
    image_preview = client.get(f"/refs/{img_id}/form/0")
    assert image_preview.status_code == 200
    assert 'class="doc-img"' in image_preview.text
    assert 'class="listen-read"' not in image_preview.text
    assert 'class="lr-play"' not in image_preview.text
