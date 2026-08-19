"""#122 Web 听读面:选 audio、配对切换、媒体 URL。"""

from pathlib import Path

from fastapi.testclient import TestClient

from kairo.models import Form, Manifest
from kairo.web.server import create_app
from kairo.workspace import Workspace


def _client(root):
    return TestClient(create_app(root))


def _write_wav(path: Path) -> None:
    path.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")


def _paired_ref(root: Path) -> str:
    ws = Workspace.init(root / "ws", topic="listen")
    rdir = ws.references_dir() / "talk"
    rdir.mkdir(parents=True)
    a1, a2 = rdir / "a1.wav", rdir / "a2.wav"
    _write_wav(a1)
    _write_wav(a2)
    (rdir / "t1.md").write_text("[00:10] alpha one\n[00:20] shared\n")
    (rdir / "t2.md").write_text("[00:15] alpha two\n[00:25] shared\n")
    ws.write_manifest(
        "talk",
        Manifest(
            id="talk",
            title="talk",
            forms=[
                Form(role="audio", location=str(a1), hash="h1"),
                Form(role="audio", location=str(a2), hash="h2"),
                Form(
                    role="transcript",
                    location="references/talk/t1.md",
                    hash="t1",
                    origin="asr-from:h1",
                ),
                Form(
                    role="transcript",
                    location="references/talk/t2.md",
                    hash="t2",
                    origin="asr-from:h2",
                ),
            ],
        ),
    )
    return "talk"


def test_select_audio_shows_listen_read_zones_and_own_media(tmp_path):
    rid = _paired_ref(tmp_path)
    r = _client(tmp_path).get(f"/w/ws/ref/{rid}/form/0")
    assert r.status_code == 200
    assert 'class="listen-read"' in r.text
    assert 'class="lr-time"' in r.text
    assert 'class="lr-seek"' in r.text
    assert 'class="lr-units"' in r.text
    assert 'class="lr-dock"' in r.text
    assert f'/w/ws/ref/{rid}/file/0' in r.text
    assert "alpha one" in r.text
    assert "alpha two" not in r.text
    fb = _client(tmp_path).get(f"/w/ws/ref/{rid}/file/0")
    assert fb.status_code == 200
    assert fb.content.startswith(b"RIFF")


def test_switch_audio_swaps_transcript_and_src(tmp_path):
    rid = _paired_ref(tmp_path)
    r = _client(tmp_path).get(f"/w/ws/ref/{rid}/form/1")
    assert r.status_code == 200
    assert "alpha two" in r.text
    assert "alpha one" not in r.text
    assert f'/w/ws/ref/{rid}/file/1' in r.text
    assert f'/w/ws/ref/{rid}/file/0"' not in r.text


def test_unpaired_audio_plays_without_fake_transcript(tmp_path):
    ws = Workspace.init(tmp_path / "ws", topic="t")
    rdir = ws.references_dir() / "solo"
    rdir.mkdir(parents=True)
    wav = rdir / "only.wav"
    _write_wav(wav)
    ws.write_manifest(
        "solo",
        Manifest(
            id="solo",
            title="solo",
            forms=[Form(role="audio", location=str(wav), hash="x")],
        ),
    )
    r = _client(tmp_path).get("/w/ws/ref/solo/form/0")
    assert r.status_code == 200
    assert 'class="listen-read"' in r.text
    assert 'class="lr-unit"' not in r.text
    assert "/w/ws/ref/solo/file/0" in r.text


def test_ambiguous_pair_not_guessed(tmp_path):
    ws = Workspace.init(tmp_path / "ws", topic="t")
    rdir = ws.references_dir() / "amb"
    rdir.mkdir(parents=True)
    a1, a2 = rdir / "a1.wav", rdir / "a2.wav"
    _write_wav(a1)
    _write_wav(a2)
    (rdir / "t.md").write_text("[00:10] should not attach\n")
    ws.write_manifest(
        "amb",
        Manifest(
            id="amb",
            title="amb",
            forms=[
                Form(role="audio", location=str(a1), hash="h1"),
                Form(role="audio", location=str(a2), hash="h2"),
                Form(role="transcript", location="references/amb/t.md", hash="t"),
            ],
        ),
    )
    r = _client(tmp_path).get("/w/ws/ref/amb/form/0")
    assert r.status_code == 200
    assert "should not attach" not in r.text


def test_listen_read_search_lists_hits_for_each_unit(tmp_path):
    rid = _paired_ref(tmp_path)
    r = _client(tmp_path).get(f"/w/ws/ref/{rid}/form/0")
    assert 'class="lr-search"' in r.text
    assert r.text.count("shared") >= 1
    assert 'data-start="10"' in r.text
    assert 'data-start="20"' in r.text


def test_audio_only_ref_view_does_not_use_empty_primary(tmp_path):
    ws = Workspace.init(tmp_path / "ws", topic="t")
    rdir = ws.references_dir() / "solo"
    rdir.mkdir(parents=True)
    wav = rdir / "only.wav"
    _write_wav(wav)
    ws.write_manifest(
        "solo",
        Manifest(
            id="solo",
            title="solo",
            forms=[Form(role="audio", location=str(wav), hash="x")],
        ),
    )
    r = _client(tmp_path).get("/w/ws/ref/solo")
    assert r.status_code == 200
    assert 'class="listen-read"' not in r.text



def test_untimed_transcript_still_readable(tmp_path):
    ws = Workspace.init(tmp_path / "ws", topic="t")
    rdir = ws.references_dir() / "plain"
    rdir.mkdir(parents=True)
    wav = rdir / "a.wav"
    _write_wav(wav)
    (rdir / "t.md").write_text("no clocks here\njust prose\n")
    ws.write_manifest(
        "plain",
        Manifest(
            id="plain",
            title="plain",
            forms=[
                Form(role="audio", location=str(wav), hash="h"),
                Form(role="transcript", location="references/plain/t.md", hash="t"),
            ],
        ),
    )
    r = _client(tmp_path).get("/w/ws/ref/plain/form/0")
    assert r.status_code == 200
    assert "no clocks here" in r.text
    assert "just prose" in r.text
    assert "is-untimed" in r.text


def test_duplicate_origin_does_not_link(tmp_path):
    ws = Workspace.init(tmp_path / "ws", topic="t")
    rdir = ws.references_dir() / "dup"
    rdir.mkdir(parents=True)
    wav = rdir / "a.wav"
    _write_wav(wav)
    (rdir / "t1.md").write_text("[00:10] first claim\n")
    (rdir / "t2.md").write_text("[00:10] second claim\n")
    ws.write_manifest(
        "dup",
        Manifest(
            id="dup",
            title="dup",
            forms=[
                Form(role="audio", location=str(wav), hash="a"),
                Form(role="transcript", location="references/dup/t1.md", hash="t1", origin="asr-from:a"),
                Form(role="transcript", location="references/dup/t2.md", hash="t2", origin="asr-from:a"),
            ],
        ),
    )
    r = _client(tmp_path).get("/w/ws/ref/dup/form/0")
    assert "first claim" not in r.text
    assert "second claim" not in r.text


def test_js_helpers_filter_duration_and_stop_audio():
    import subprocess
    from pathlib import Path

    js = Path("src/kairo/web/static/listen_read.js").read_text()
    script = (
        js
        + """
const filtered = kairoFilterUnits(
  [{start:10,end:600,text:'valid'},{start:600,end:null,text:'past'}],
  30
);
if (filtered.length !== 1 || filtered[0].text.indexOf('past') < 0 || filtered[0].start !== 10) {
  throw new Error('filter ' + JSON.stringify(filtered));
}
const audio = { paused: false, src: 'x', pause(){ this.paused = true; }, removeAttribute(n){ if(n==='src') this.src=''; }, load(){} };
const scope = { querySelectorAll(){ return [audio]; } };
kairoStopListenRead(scope);
if (!audio.paused || audio.src !== '') throw new Error('not stopped');
console.log('ok');
"""
    )
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr + r.stdout

