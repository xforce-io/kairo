"""#354 S1–S3: Topic context is independent of the Ref's home."""
from html import unescape
import re
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from kairo.refs import add_global_ref, add_tag, create_tag, global_home, remove_tag, set_include_tags
from kairo.web.public import set_reference_public
from kairo.web.server import create_app
from kairo.workspace import Workspace


@pytest.fixture
def members(tmp_path):
    topic = Workspace.init(tmp_path / "topic", topic="阅读 Topic")
    other = Workspace.init(tmp_path / "other", topic="来源 Topic")
    create_tag(tmp_path, "reading")
    set_include_tags(tmp_path, "topic", ["reading"])
    owners = {"topic": topic, "other": other, "global": None}
    for home, ws in owners.items():
        src = tmp_path / f"{home}.txt"
        src.write_text(f"{home} ORIGINAL BODY")
        if ws is None:
            add_global_ref(tmp_path, [src], ref_id="shared", copy=True)
            ws = global_home(tmp_path)
            owners[home] = ws
        else:
            ws.add([src], ref_id="shared", copy=True)
        ws.set_title("shared", f"{home} material")
        (ws.references_dir() / "shared" / "digest.md").write_text(f"# {home} DIGEST BODY")
        add_tag(tmp_path, home="" if home == "global" else home, ref_id="shared", tag="reading")
    return tmp_path, owners, TestClient(create_app(tmp_path))


@pytest.mark.parametrize("home", ["topic", "global", "other"])
def test_s1_s2_member_preview_and_restore(members, home):
    root, owners, client = members
    query = "" if home == "topic" else f"&home={home}"
    address = f"/w/topic?ref=shared{query}"
    detail = f"/w/topic/ref/shared" + (f"?home={home}" if query else "")
    for url in [address, address.replace("/w/", "/topics/")]:
        page = client.get(url)
        active = re.findall(r'<a class="nav-doc is-ref is-active"[^>]+>', page.text)
        assert len(active) == 1
        assert f'href="{address}"' in unescape(active[0])
        assert f'hx-get="{detail}"' in unescape(page.text)
    response = client.get(detail, headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert f"<h1>{home} DIGEST BODY</h1>" in response.text
    assert 'id="reader" class="pane-read" hx-swap-oob="true"' in response.text
    assert f'data-share-path="{address}"' in unescape(response.text)
    for different in owners.keys() - {home}:
        assert f"{different} DIGEST BODY" not in response.text
    if home != "topic":
        assert 'hx-post="/w/topic/ref/shared' not in response.text
        assert f'href="/refs/shared?home={home}"' in response.text
    else:
        assert 'hx-post="/w/topic/ref/shared/title"' in response.text
    form_url = re.search(r'hx-get="([^"]+/form/digest[^"]*)"', response.text).group(1)
    assert f"{home} DIGEST BODY" in client.get(unescape(form_url)).text
    assert f"{home} ORIGINAL BODY" in client.get(f"/w/topic/ref/shared/file/0", params={"home": home}).text


@pytest.mark.parametrize("home", ["global", "other", "topic"])
def test_s3_removed_member_clears_reader_and_denies_forms(members, home):
    root, owners, client = members
    remove_tag(root, home="" if home == "global" else home, ref_id="shared", tag="reading")
    params = {"home": home}
    response = client.get('/w/topic/ref/shared', params=params)
    assert 'This reference is unavailable' in response.text
    assert 'hx-swap-oob="true"' in response.text
    assert 'DIGEST BODY' not in response.text
    page = client.get('/w/topic', params={"ref": "shared", **params})
    assert 'This reference is unavailable' in page.text
    assert 'nav-doc is-ref is-active' not in page.text
    for suffix in ['form/digest', 'file/0']:
        assert client.get(f'/w/topic/ref/shared/{suffix}', params=params).status_code == 404
    assert client.get('/w/topic').status_code == 200


@pytest.mark.parametrize("home", ["missing", "../other", "", "/tmp"])
def test_s3_invalid_home_does_not_guess_same_id(members, home):
    _, _, client = members
    for path in ['/w/topic', '/w/topic/ref/shared']:
        response = client.get(path, params={"home": home, "ref": "shared"})
        assert 'This reference is unavailable' in response.text
        assert 'DIGEST BODY' not in response.text


def test_s3_empty_material_and_recovery(members):
    root, _, client = members
    src = root / 'empty.wav'
    src.write_bytes(b'RIFF')
    add_global_ref(root, [src], ref_id='empty-audio', copy=True)
    add_tag(root, home='', ref_id='empty-audio', tag='reading')
    empty = client.get('/w/topic/ref/empty-audio?home=global')
    assert empty.status_code == 200
    assert 'no inline-previewable text form' in empty.text
    assert 'hx-swap-oob="true"' in empty.text
    assert 'DIGEST BODY' not in empty.text
    assert 'global DIGEST BODY' in client.get('/w/topic/ref/shared?home=global').text
    assert 'This reference is unavailable' in client.get('/w/topic/ref/missing?home=global').text


def test_s1_audio_image_and_corpus_use_member_routes(members):
    root, _, client = members
    import base64
    image = root / 'image.png'
    image.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aZ1cAAAAASUVORK5CYII='))
    audio = root / 'audio.wav'
    audio.write_bytes(b'RIFF')
    for name, file, source_class in [('image', image, 'corpus'), ('audio', audio, 'stream')]:
        add_global_ref(root, [file], ref_id=name, source_class=source_class, copy=True)
        add_tag(root, home='', ref_id=name, tag='reading')
    page = client.get('/w/topic?ref=image&home=global')
    assert 'href="/w/topic?ref=image&amp;home=global"' in page.text
    meta = client.get('/w/topic/ref/image?home=global')
    assert 'src="/w/topic/ref/image/file/0?home=global"' in meta.text
    assert client.get('/w/topic/ref/image/file/0?home=global').content == image.read_bytes()
    audio_page = client.get('/w/topic/ref/audio/form/0?home=global')
    assert audio_page.status_code == 200
    assert '/w/topic/ref/audio/file/0?home=global' in audio_page.text
    assert '/w/topic/ref/audio/form/0?home=global' in client.get('/w/topic/ref/audio?home=global').text


def test_public_read_checks_actual_home_not_same_id(members):
    root, owners, _ = members
    set_reference_public(root, owners['topic'], 'shared', public=True)
    client = TestClient(create_app(root, mode='public-read'))
    page = client.get('/w/topic')
    assert page.status_code == 200
    assert 'global material' not in page.text
    assert 'other material' not in page.text
    for home in ['global', 'other']:
        for suffix in ['', '/form/digest', '/file/0']:
            response = client.get(f'/w/topic/ref/shared{suffix}', params={'home': home})
            assert response.status_code == 404
            assert 'BODY' not in response.text
    assert 'topic DIGEST BODY' in client.get('/w/topic/ref/shared').text
    set_reference_public(root, owners['other'], 'shared', public=True)
    assert 'other material' in client.get('/w/topic').text
    assert 'other DIGEST BODY' in client.get('/w/topic/ref/shared?home=other').text


def test_s2_encoded_source_and_id(tmp_path):
    topic = Workspace.init(tmp_path / '当前 Topic', topic='当前')
    source = Workspace.init(tmp_path / '来源 Topic', topic='来源')
    src = tmp_path / 'note.txt'
    src.write_text('ENCODED CONTENT')
    source.add([src], ref_id='中文-id', copy=True)
    create_tag(tmp_path, 'read')
    add_tag(tmp_path, home=source.root.name, ref_id='中文-id', tag='read')
    set_include_tags(tmp_path, topic.root.name, ['read'])
    client = TestClient(create_app(tmp_path))
    page = client.get('/w/' + quote(topic.root.name, safe=''))
    link = re.search(r'<a class="nav-doc is-ref [^"]*" href="([^"]+)" hx-get="([^"]+)"', page.text)
    assert link
    assert '%20' in link[1] and '%20' in link[2]
    assert 'nav-doc is-ref is-active' in client.get(unescape(link[1])).text
    assert 'ENCODED CONTENT' in client.get(unescape(link[2])).text
