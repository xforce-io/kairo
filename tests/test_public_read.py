"""#118 anonymous public-read surface — S1/S2 contract tests.

Exercises the real CLI → create_app(mode=public-read) → reader → safe
descriptor path. No test-only bypasses.

Publication state is schema version 3: every allowed physical member is a pure
metadata descriptor frozen in ``public-read.json`` (key/sha256/path/category/
label/media_type/download_name/text_capable). State load never reads workspace
manifests or member bytes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from kairo.cli import app as cli_app
from kairo.models import Form, Manifest
from kairo.web.public import (
    PUBLIC_STATE_FILENAME,
    PUBLIC_STATE_VERSION,
    AnonymousPublicReader,
    PublicNotFound,
    load_public_read_state,
)
from kairo.web.server import UnknownAppMode, create_app
from kairo.workspace import Workspace

runner = CliRunner()

# Fixed locators used across fixtures (p- + 22 url-safe chars)
LOC_TARGET = "p-" + ("A" * 22)
LOC_REF = "p-" + ("B" * 22)
LOC_OTHER = "p-" + ("C" * 22)
LOC_PRIVATE = "p-" + ("D" * 22)

_MD = "text/markdown; charset=utf-8"
_PNG = "image/png"
_OCTET = "application/octet-stream"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _member(
    key: str,
    *,
    path: str,
    file: Path,
    category: str,
    label: str,
    media_type: str,
    download_name: str,
    text_capable: bool,
) -> dict:
    return {
        "key": key,
        "sha256": _sha256_file(file),
        "path": path,
        "category": category,
        "label": label,
        "media_type": media_type,
        "download_name": download_name,
        "text_capable": text_capable,
    }


def _body_member(file: Path, rel_path: str = "understanding.md") -> dict:
    return _member(
        "body",
        path=rel_path,
        file=file,
        category="body",
        label="body",
        media_type=_MD,
        download_name=Path(rel_path).name,
        text_capable=True,
    )


def _digest_member(file: Path, rel_path: str = "digest.md") -> dict:
    return _member(
        "digest",
        path=rel_path,
        file=file,
        category="digest",
        label="digest",
        media_type=_MD,
        download_name=Path(rel_path).name,
        text_capable=True,
    )


def _prose_member(file: Path, rel_path: str = "prose.md") -> dict:
    return _member(
        "prose",
        path=rel_path,
        file=file,
        category="prose",
        label="prose",
        media_type=_MD,
        download_name=Path(rel_path).name,
        text_capable=True,
    )


def _form_member(
    key: str,
    file: Path,
    *,
    rel_path: str,
    label: str,
    media_type: str = _MD,
    text_capable: bool = True,
) -> dict:
    return _member(
        key,
        path=rel_path,
        file=file,
        category="form",
        label=label,
        media_type=media_type,
        download_name=Path(rel_path).name,
        text_capable=text_capable,
    )


def _artifact_member(
    name: str,
    file: Path,
    *,
    rel_path: str | None = None,
    media_type: str = _OCTET,
    text_capable: bool = False,
    label: str | None = None,
) -> dict:
    rel = rel_path or name
    key = f"artifact-{name}"
    return _member(
        key,
        path=rel,
        file=file,
        category="artifact",
        label=label or key,
        media_type=media_type,
        download_name=Path(rel).name,
        text_capable=text_capable,
    )


def _write_state(root: Path, roots: list[dict], generation: int = 1) -> None:
    payload = {
        "version": PUBLIC_STATE_VERSION,
        "generation": generation,
        "roots": roots,
    }
    (root / PUBLIC_STATE_FILENAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _public_client(root: Path) -> TestClient:
    return TestClient(create_app(root, mode="public-read"))


def _console_client(root: Path) -> TestClient:
    return TestClient(create_app(root))


def _setup_workspace(root: Path, slug: str = "ws") -> Workspace:
    ws = Workspace.init(root / slug, topic=slug)
    (ws.root / "understanding.md").write_text(
        "# Public understanding\n\nlanding priority alpha-secret-token\n",
        encoding="utf-8",
    )
    (ws.root / "assessment.md").write_text(
        "# Private assessment\n\nshould-not-leak\n",
        encoding="utf-8",
    )
    return ws


def _add_public_reference(ws: Workspace, ref_id: str = "2026-01-01-pub") -> str:
    ref_dir = ws.references_dir() / ref_id
    ref_dir.mkdir(parents=True, exist_ok=True)
    (ref_dir / "transcript.md").write_text(
        "transcript body PUBLIC-REF-BODY\n", encoding="utf-8"
    )
    (ref_dir / "digest.md").write_text(
        "# Digest\n\ncore conclusion PUBLIC-DIGEST\n", encoding="utf-8"
    )
    (ref_dir / "prose.md").write_text(
        "# Prose\n\nreadable PUBLIC-PROSE\n", encoding="utf-8"
    )
    (ref_dir / "shot.png").write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 17 + b"IEND"
    )
    man = Manifest(
        id=ref_id,
        title="Public Meeting",
        source_class="stream",
        forms=[
            Form(
                role="transcript",
                location=f"references/{ref_id}/transcript.md",
                hash="aaa111",
            ),
            Form(
                role="attachment",
                location=f"references/{ref_id}/shot.png",
                hash="bbb222",
            ),
        ],
    )
    ws.write_manifest(ref_id, man)
    return ref_id


def _target_root_entry(
    ws: Workspace,
    locator: str = LOC_TARGET,
    target_path: str = "understanding.md",
    display_label: str | None = "Understanding Public",
) -> dict:
    body = ws.root / target_path
    entry: dict = {
        "locator": locator,
        "kind": "target",
        "workspace": ws.root.name,
        "target_path": target_path,
        "members": [_body_member(body, target_path)],
    }
    if display_label is not None:
        entry["display_label"] = display_label
    return entry


def _ref_root_entry(
    ws: Workspace,
    ref_id: str,
    locator: str = LOC_REF,
    member_keys: list[str] | None = None,
    display_label: str | None = "Meeting Public",
) -> dict:
    ref_dir = ws.references_dir() / ref_id
    keys = member_keys or ["form-0", "form-1", "digest", "prose"]
    members = []
    for key in keys:
        if key == "digest":
            members.append(_digest_member(ref_dir / "digest.md"))
        elif key == "prose":
            members.append(_prose_member(ref_dir / "prose.md"))
        elif key == "form-0":
            members.append(
                _form_member(
                    "form-0",
                    ref_dir / "transcript.md",
                    rel_path="transcript.md",
                    label="transcript",
                )
            )
        elif key == "form-1":
            members.append(
                _form_member(
                    "form-1",
                    ref_dir / "shot.png",
                    rel_path="shot.png",
                    label="attachment",
                    media_type=_PNG,
                    text_capable=False,
                )
            )
        elif key.startswith("form-"):
            # Generic form helper: require file name matching key for custom cases
            raise AssertionError(f"use explicit form helper for {key}")
        elif key.startswith("artifact-"):
            name = key[len("artifact-") :]
            members.append(_artifact_member(name, ref_dir / name, rel_path=name))
        else:
            raise AssertionError(f"unknown member key helper: {key}")
    entry: dict = {
        "locator": locator,
        "kind": "reference",
        "workspace": ws.root.name,
        "ref_id": ref_id,
        "members": members,
    }
    if display_label is not None:
        entry["display_label"] = display_label
    return entry


def _full_public_root(tmp_path: Path) -> tuple[Path, str, str]:
    """Serve root with one public target + one public reference + private docs."""
    ws = _setup_workspace(tmp_path, "ws")
    rid = _add_public_reference(ws)
    priv = ws.references_dir() / "2026-01-02-priv"
    priv.mkdir(parents=True)
    (priv / "digest.md").write_text("SECRET-PRIVATE-DIGEST\n", encoding="utf-8")
    ws.write_manifest(
        "2026-01-02-priv",
        Manifest(
            id="2026-01-02-priv",
            title="Private",
            forms=[
                Form(
                    role="transcript",
                    location="references/2026-01-02-priv/digest.md",
                    hash="ccc",
                )
            ],
        ),
    )
    _write_state(
        tmp_path,
        [
            _target_root_entry(ws),
            _ref_root_entry(ws, rid),
        ],
    )
    return tmp_path, rid, "ws"


# ---------------------------------------------------------------------------
# State loading fail-closed
# ---------------------------------------------------------------------------


def test_state_missing_is_invalid(tmp_path):
    snap = load_public_read_state(tmp_path)
    assert snap.valid is False
    assert snap.candidates == ()


def test_state_corrupt_json_fail_closed(tmp_path):
    (tmp_path / PUBLIC_STATE_FILENAME).write_text("{not-json", encoding="utf-8")
    snap = load_public_read_state(tmp_path)
    assert snap.valid is False


def test_state_wrong_version_fail_closed(tmp_path):
    (tmp_path / PUBLIC_STATE_FILENAME).write_text(
        json.dumps({"version": 99, "generation": 1, "roots": []}), encoding="utf-8"
    )
    assert load_public_read_state(tmp_path).valid is False


def test_state_version_1_and_2_schema_fail_closed(tmp_path):
    """Old schemas without full frozen descriptors must fail closed."""
    _setup_workspace(tmp_path)
    payload = {
        "version": 1,
        "generation": 1,
        "roots": [
            {
                "locator": LOC_TARGET,
                "kind": "target",
                "workspace": "ws",
                "target_path": "understanding.md",
                "members": ["body"],
            }
        ],
    }
    (tmp_path / PUBLIC_STATE_FILENAME).write_text(
        json.dumps(payload), encoding="utf-8"
    )
    assert load_public_read_state(tmp_path).valid is False
    # v2 key+sha256 only is also rejected
    payload = {
        "version": 2,
        "generation": 1,
        "roots": [
            {
                "locator": LOC_TARGET,
                "kind": "target",
                "workspace": "ws",
                "target_path": "understanding.md",
                "members": [{"key": "body", "sha256": "0" * 64}],
            }
        ],
    }
    (tmp_path / PUBLIC_STATE_FILENAME).write_text(
        json.dumps(payload), encoding="utf-8"
    )
    assert load_public_read_state(tmp_path).valid is False
    # even if version bumped but members remain bare strings
    payload = {
        "version": PUBLIC_STATE_VERSION,
        "generation": 1,
        "roots": [
            {
                "locator": LOC_TARGET,
                "kind": "target",
                "workspace": "ws",
                "target_path": "understanding.md",
                "members": ["body"],
            }
        ],
    }
    (tmp_path / PUBLIC_STATE_FILENAME).write_text(
        json.dumps(payload), encoding="utf-8"
    )
    assert load_public_read_state(tmp_path).valid is False


def test_state_duplicate_locator_fail_closed(tmp_path):
    ws = _setup_workspace(tmp_path)
    a = _target_root_entry(ws, locator=LOC_TARGET, target_path="understanding.md")
    b = _target_root_entry(
        ws,
        locator=LOC_TARGET,
        target_path="assessment.md",
        display_label=None,
    )
    _write_state(tmp_path, [a, b])
    assert load_public_read_state(tmp_path).valid is False


def test_state_duplicate_identity_fail_closed(tmp_path):
    ws = _setup_workspace(tmp_path)
    a = _target_root_entry(ws, locator=LOC_TARGET)
    b = _target_root_entry(ws, locator=LOC_OTHER, display_label=None)
    _write_state(tmp_path, [a, b])
    assert load_public_read_state(tmp_path).valid is False


def test_state_path_escape_fail_closed(tmp_path):
    _setup_workspace(tmp_path)
    _write_state(
        tmp_path,
        [
            {
                "locator": LOC_TARGET,
                "kind": "target",
                "workspace": "ws",
                "target_path": "../outside.md",
                "members": [
                    {
                        "key": "body",
                        "sha256": "0" * 64,
                        "path": "../outside.md",
                        "category": "body",
                        "label": "body",
                        "media_type": _MD,
                        "download_name": "outside.md",
                        "text_capable": True,
                    }
                ],
            }
        ],
    )
    assert load_public_read_state(tmp_path).valid is False


def test_state_exact_json_shape_roundtrip(tmp_path):
    """Document and pin the exact public-read.json v3 shape used by tests."""
    ws = _setup_workspace(tmp_path)
    rid = "2026-01-01-x"
    ref_dir = ws.references_dir() / rid
    ref_dir.mkdir(parents=True)
    (ref_dir / "digest.md").write_text("d\n", encoding="utf-8")
    (ref_dir / "t.md").write_text("t\n", encoding="utf-8")
    ws.write_manifest(
        rid,
        Manifest(
            id=rid,
            title="x",
            forms=[
                Form(
                    role="transcript",
                    location=f"references/{rid}/t.md",
                    hash="h",
                )
            ],
        ),
    )
    body_hash = _sha256_file(ws.root / "understanding.md")
    digest_hash = _sha256_file(ref_dir / "digest.md")
    form_hash = _sha256_file(ref_dir / "t.md")
    payload = {
        "version": 3,
        "generation": 3,
        "roots": [
            {
                "locator": LOC_TARGET,
                "kind": "target",
                "workspace": "ws",
                "target_path": "understanding.md",
                "display_label": "Label",
                "members": [
                    {
                        "key": "body",
                        "sha256": body_hash,
                        "path": "understanding.md",
                        "category": "body",
                        "label": "body",
                        "media_type": _MD,
                        "download_name": "understanding.md",
                        "text_capable": True,
                    }
                ],
            },
            {
                "locator": LOC_REF,
                "kind": "reference",
                "workspace": "ws",
                "ref_id": rid,
                "display_label": "Ref",
                "members": [
                    {
                        "key": "digest",
                        "sha256": digest_hash,
                        "path": "digest.md",
                        "category": "digest",
                        "label": "digest",
                        "media_type": _MD,
                        "download_name": "digest.md",
                        "text_capable": True,
                    },
                    {
                        "key": "form-0",
                        "sha256": form_hash,
                        "path": "t.md",
                        "category": "form",
                        "label": "transcript",
                        "media_type": _MD,
                        "download_name": "t.md",
                        "text_capable": True,
                    },
                ],
            },
        ],
    }
    (tmp_path / PUBLIC_STATE_FILENAME).write_text(
        json.dumps(payload), encoding="utf-8"
    )
    snap = load_public_read_state(tmp_path)
    assert snap.valid is True
    assert snap.generation == 3
    assert list(snap.candidates) == [LOC_TARGET, LOC_REF]
    assert snap.locator_to_root[LOC_TARGET].kind == "target"
    assert snap.locator_to_root[LOC_REF].ref_id == rid
    assert snap.locator_to_root[LOC_TARGET].members["body"].sha256 == body_hash
    assert snap.locator_to_root[LOC_REF].members["form-0"].label == "transcript"
    assert snap.locator_to_root[LOC_REF].members["form-0"].rel_path == "t.md"
    assert PUBLIC_STATE_VERSION == 3


def test_state_load_never_reads_member_bytes(tmp_path, monkeypatch):
    """load_public_read_state must not open member files or manifests."""
    root, rid, _ = _full_public_root(tmp_path)
    reads: list[str] = []
    real_read_bytes = Path.read_bytes
    real_read_text = Path.read_text

    def spy_read_bytes(self, *a, **k):
        reads.append(f"bytes:{self}")
        return real_read_bytes(self, *a, **k)

    def spy_read_text(self, *a, encoding=None, errors=None):
        reads.append(f"text:{self}")
        return real_read_text(self, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_bytes", spy_read_bytes)
    monkeypatch.setattr(Path, "read_text", spy_read_text)

    snap = load_public_read_state(root)
    assert snap.valid is True
    # Only the state file itself may be read.
    assert len(reads) == 1
    assert reads[0].endswith(PUBLIC_STATE_FILENAME) or PUBLIC_STATE_FILENAME in reads[0]
    assert not any("manifest" in r for r in reads)
    assert not any("digest.md" in r for r in reads)
    assert not any("transcript.md" in r for r in reads)
    assert not any("understanding.md" in r for r in reads)
    assert not any(rid in r and "manifest" in r for r in reads)


# ---------------------------------------------------------------------------
# S1 — explicit public roots via five entries
# ---------------------------------------------------------------------------


def test_healthz_public(tmp_path):
    c = _public_client(tmp_path)
    r = c.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert r.headers.get("cache-control") == "no-store"


def test_public_home_minimal(tmp_path):
    c = _public_client(tmp_path)
    r = c.get("/")
    assert r.status_code == 200
    assert "Public documents" in r.text
    assert "/w/" not in r.text


def test_s1_public_target_page_and_body(tmp_path):
    root, _, _ = _full_public_root(tmp_path)
    c = _public_client(root)
    page = c.get(f"/p/{LOC_TARGET}")
    assert page.status_code == 200
    assert "Understanding Public" in page.text
    body = c.get(f"/p/{LOC_TARGET}/content/body")
    assert body.status_code == 200
    assert "alpha-secret-token" in body.text
    assert "should-not-leak" not in body.text


def test_s1_public_reference_members(tmp_path):
    root, _, _ = _full_public_root(tmp_path)
    c = _public_client(root)
    page = c.get(f"/p/{LOC_REF}")
    assert page.status_code == 200
    assert "Meeting Public" in page.text
    assert c.get(f"/p/{LOC_REF}/content/digest").status_code == 200
    assert "PUBLIC-DIGEST" in c.get(f"/p/{LOC_REF}/content/digest").text
    form0 = c.get(f"/p/{LOC_REF}/content/form-0")
    assert form0.status_code == 200 and "PUBLIC-REF-BODY" in form0.text


def test_s1_file_get_and_head(tmp_path):
    root, _, _ = _full_public_root(tmp_path)
    c = _public_client(root)
    g = c.get(f"/p/{LOC_REF}/file/form-1")
    assert g.status_code == 200
    assert g.content[:4] == b"\x89PNG"
    assert "shot.png" in g.headers.get("content-disposition", "")
    h = c.head(f"/p/{LOC_REF}/file/form-1")
    assert h.status_code == 200
    assert int(h.headers.get("content-length", "0")) > 0


def test_s1_api_document_and_member(tmp_path):
    root, _, _ = _full_public_root(tmp_path)
    c = _public_client(root)
    doc = c.get(f"/api/public/v1/documents/{LOC_TARGET}")
    assert doc.status_code == 200
    body = doc.json()
    assert body["locator"] == LOC_TARGET
    assert body["kind"] == "target"
    assert body["display_label"] == "Understanding Public"
    assert any(m["key"] == "body" for m in body["members"])
    assert "path" not in body
    assert "sha256" not in json.dumps(body)
    mem = c.get(f"/api/public/v1/documents/{LOC_TARGET}/members/body")
    assert mem.status_code == 200
    assert "alpha-secret-token" in mem.json()["text"]


def test_s1_search_page_and_api(tmp_path):
    root, _, _ = _full_public_root(tmp_path)
    c = _public_client(root)
    page = c.get("/p/search", params={"q": "PUBLIC-DIGEST"})
    assert page.status_code == 200
    assert LOC_REF in page.text or "Meeting Public" in page.text
    assert "SECRET-PRIVATE" not in page.text
    api = c.get("/api/public/v1/search", params={"q": "PUBLIC-DIGEST"})
    assert api.status_code == 200
    results = api.json()["results"]
    assert any(r["locator"] == LOC_REF for r in results)
    for r in results:
        assert "ref_id" not in r


def test_s1_search_static_before_locator(tmp_path):
    """`/p/search` is a reserved static route, never treated as locator."""
    root, _, _ = _full_public_root(tmp_path)
    c = _public_client(root)
    r = c.get("/p/search")
    assert r.status_code == 200
    assert "Search" in r.text
    assert c.get(f"/p/{LOC_TARGET}").status_code == 200


def test_s1_references_entry(tmp_path):
    root, _, _ = _full_public_root(tmp_path)
    c = _public_client(root)
    r = c.get(f"/p/{LOC_REF}/references")
    assert r.status_code == 200
    assert "References" in r.text
    assert "SECRET-PRIVATE-DIGEST" not in r.text


def test_s1_five_entries_same_root(tmp_path):
    """Page, search, file, references, API all reach the same public root."""
    root, _, _ = _full_public_root(tmp_path)
    c = _public_client(root)
    assert c.get(f"/p/{LOC_REF}").status_code == 200
    assert c.get("/p/search", params={"q": "Meeting"}).status_code == 200
    assert c.get(f"/p/{LOC_REF}/file/form-1").status_code == 200
    assert c.get(f"/p/{LOC_REF}/references").status_code == 200
    assert c.get(f"/api/public/v1/documents/{LOC_REF}").json()["locator"] == LOC_REF


# ---------------------------------------------------------------------------
# S2 — fail-closed, isomorphic denial, no console leakage
# ---------------------------------------------------------------------------


def _deny_root_paths(locator: str):
    """Root-level public entries that authorize on locator only."""
    return [
        ("html", f"/p/{locator}"),
        ("html", f"/p/{locator}/references"),
        ("json", f"/api/public/v1/documents/{locator}"),
        ("html", f"/p/search?q=locator:{locator}"),
    ]


def _deny_member_paths(locator: str, member: str):
    """Member-carrying entries that authorize on locator + member."""
    return [
        ("html", f"/p/{locator}/content/{member}"),
        ("file", f"/p/{locator}/file/{member}"),
        ("json", f"/api/public/v1/documents/{locator}/members/{member}"),
    ]


def _deny_matrix_paths(locator: str, member: str = "body"):
    """All public-read entry points for an invalid locator (isomorphic denial)."""
    return _deny_root_paths(locator) + _deny_member_paths(locator, member)


def _assert_fixed_denial(resp, kind: str):
    assert resp.status_code == 404
    assert resp.headers.get("cache-control") == "no-store"
    if kind == "json":
        assert resp.json() == {"error": "not_found"}
    elif kind == "file":
        assert resp.content == b""
        assert "content-disposition" not in {k.lower() for k in resp.headers.keys()}
    else:
        assert "Not found" in resp.text
        assert "SECRET" not in resp.text


def _assert_isomorphic_denials(client, paths):
    samples = []
    for kind, path in paths:
        r = client.get(path)
        _assert_fixed_denial(r, kind)
        samples.append((kind, r.status_code, r.headers.get("cache-control")))
    by_kind: dict[str, list] = {}
    for kind, status, cc in samples:
        by_kind.setdefault(kind, []).append((status, cc))
    for rows in by_kind.values():
        assert len({(s, c_) for s, c_ in rows}) == 1


@pytest.mark.parametrize(
    "locator",
    [
        "not-a-locator",
        "p-short",
        "p-" + ("x" * 21),  # too short
        "p-" + ("/" * 22),
        "p-" + ("A" * 22) + "/../x",
        "p-" + ("Z" * 22),  # well-formed but unpublished
        "",
        "p-" + ("A" * 300),
    ],
)
def test_s2_isomorphic_denial_matrix_invalid_locator(tmp_path, locator):
    """Invalid locator denies every root and member entry with fixed 404 shape."""
    root, _, _ = _full_public_root(tmp_path)
    c = _public_client(root)
    _assert_isomorphic_denials(c, _deny_matrix_paths(locator, "body"))


def test_s2_invalid_member_denies_member_entries_only(tmp_path):
    """Valid locator + unknown member: only member-carrying URLs 404.

    Root presentation remains reachable.
    """
    root, _, _ = _full_public_root(tmp_path)
    c = _public_client(root)
    for kind, path in _deny_member_paths(LOC_TARGET, "nope-member"):
        _assert_fixed_denial(c.get(path), kind)
    for kind, path in _deny_member_paths(LOC_TARGET, "form-0"):
        _assert_fixed_denial(c.get(path), kind)
    page = c.get(f"/p/{LOC_TARGET}")
    assert page.status_code == 200
    doc = c.get(f"/api/public/v1/documents/{LOC_TARGET}")
    assert doc.status_code == 200
    assert doc.json()["locator"] == LOC_TARGET


def test_s2_unpublished_target_denied(tmp_path):
    root, _, _ = _full_public_root(tmp_path)
    c = _public_client(root)
    # assessment.md exists but is not published
    for kind, path in _deny_matrix_paths(LOC_PRIVATE, "body"):
        r = c.get(path)
        _assert_fixed_denial(r, kind)
        if kind != "file":
            assert "SECRET-PRIVATE" not in r.text


def test_s2_path_and_ref_id_bypass_denied(tmp_path):
    root, _, _ = _full_public_root(tmp_path)
    c = _public_client(root)
    for path in (
        "/w/ws",
        "/w/ws/understanding.md",
        "/api/public/v1/documents/understanding.md",
        f"/p/2026-01-01-pub",
        f"/p/{LOC_REF}/content/references/2026-01-01-pub/transcript.md",
    ):
        r = c.get(path)
        assert r.status_code == 404


def test_s2_console_routes_absent_on_public_app(tmp_path):
    root, _, _ = _full_public_root(tmp_path)
    pub = _public_client(root)
    con = _console_client(root)
    for path in ("/w/ws", "/w/ws/references", "/tasks"):
        r = pub.get(path)
        assert r.status_code == 404
        assert r.headers.get("cache-control") == "no-store"
    assert con.get("/w/ws").status_code == 200


def test_s2_missing_state_denies_all(tmp_path):
    _setup_workspace(tmp_path)
    c = _public_client(tmp_path)
    r = c.get(f"/p/{LOC_TARGET}")
    _assert_fixed_denial(r, "html")
    r2 = c.get(f"/api/public/v1/documents/{LOC_TARGET}")
    _assert_fixed_denial(r2, "json")
    assert r.headers.get("cache-control") == "no-store"


def test_s2_corrupt_state_denies_all(tmp_path):
    _setup_workspace(tmp_path)
    (tmp_path / PUBLIC_STATE_FILENAME).write_text("{bad", encoding="utf-8")
    c = _public_client(tmp_path)
    r = c.get(f"/p/{LOC_TARGET}")
    _assert_fixed_denial(r, "html")
    r2 = c.get(f"/api/public/v1/documents/{LOC_TARGET}")
    _assert_fixed_denial(r2, "json")


def test_s2_search_rejects_locator_selector(tmp_path):
    root, _, _ = _full_public_root(tmp_path)
    c = _public_client(root)
    r = c.get("/p/search", params={"q": f"locator:{LOC_TARGET}"})
    _assert_fixed_denial(r, "html")
    r2 = c.get("/api/public/v1/search", params={"q": f"locator:{LOC_TARGET}"})
    assert r2.json() == {"error": "not_found"}


def test_s2_search_rejects_member_selector_same_shape(tmp_path):
    """locator:/member: selectors reject uniformly via reader envelope."""
    root, _, _ = _full_public_root(tmp_path)
    c = _public_client(root)
    for q in (
        f"member:body",
        f"locator:{LOC_REF}",
        LOC_TARGET,
        f"{LOC_REF}/content/body",
    ):
        _assert_fixed_denial(c.get("/p/search", params={"q": q}), "html")
        _assert_fixed_denial(
            c.get("/api/public/v1/search", params={"q": q}), "json"
        )
    # ordinary free text still works
    ok = c.get("/api/public/v1/search", params={"q": "PUBLIC-DIGEST"})
    assert ok.status_code == 200
    assert any(r["locator"] == LOC_REF for r in ok.json()["results"])


def test_s2_private_content_not_in_search(tmp_path):
    root, _, _ = _full_public_root(tmp_path)
    c = _public_client(root)
    page = c.get("/p/search", params={"q": "SECRET-PRIVATE"})
    assert page.status_code == 200
    assert "SECRET-PRIVATE" not in page.text
    api = c.get("/api/public/v1/search", params={"q": "SECRET-PRIVATE"})
    assert api.status_code == 200
    assert api.json()["results"] == []


def test_s2_generation_change_next_request_denies(tmp_path):
    root, _, _ = _full_public_root(tmp_path)
    c = _public_client(root)
    assert c.get(f"/p/{LOC_TARGET}").status_code == 200
    # withdraw everything
    _write_state(root, [], generation=2)
    _assert_fixed_denial(c.get(f"/p/{LOC_TARGET}"), "html")
    _assert_fixed_denial(c.get(f"/api/public/v1/documents/{LOC_TARGET}"), "json")
    # search must not return old candidates
    r = c.get("/api/public/v1/search", params={"q": "Public"})
    # empty roots → valid empty snapshot → empty results (or deny if invalid)
    assert r.status_code in {200, 404}
    if r.status_code == 200:
        assert r.json()["results"] == []
    # republish only reference
    ws = Workspace.open(root / "ws")
    _write_state(root, [_ref_root_entry(ws, "2026-01-01-pub")], generation=3)
    assert c.get(f"/p/{LOC_TARGET}").status_code == 404
    assert c.get(f"/p/{LOC_REF}").status_code == 200


def test_s2_file_deny_has_no_object_headers(tmp_path):
    root, _, _ = _full_public_root(tmp_path)
    c = _public_client(root)
    f = c.get(f"/p/{LOC_PRIVATE}/file/form-1")
    _assert_fixed_denial(f, "file")
    keys = {k.lower() for k in f.headers.keys()}
    assert "content-disposition" not in keys
    ct = f.headers.get("content-type", "")
    assert "image" not in ct.lower()


def test_s2_reader_permit_only(tmp_path):
    root, _, _ = _full_public_root(tmp_path)
    rdr = AnonymousPublicReader(root)
    p = rdr.permit(LOC_TARGET, "presentation")
    assert p.presentation is not None
    assert p.locator == LOC_TARGET
    body = rdr.permit(LOC_TARGET, "body")
    assert body.member is not None
    assert "alpha-secret-token" in (body.member.text or "")
    with pytest.raises(PublicNotFound):
        rdr.permit(LOC_PRIVATE, "body")
    with pytest.raises(PublicNotFound):
        rdr.permit(LOC_TARGET, "form-0")


def test_s2_absolute_escape_form_not_in_closure(tmp_path):
    ws = _setup_workspace(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("ESCAPED\n", encoding="utf-8")
    rid = "2026-01-03-esc"
    (ws.references_dir() / rid).mkdir(parents=True)
    man = Manifest(
        id=rid,
        title="Esc",
        forms=[
            Form(role="transcript", location=str(outside.resolve()), hash="x"),
        ],
    )
    ws.write_manifest(rid, man)
    loc = LOC_OTHER
    # Absolute path in state is rejected at load (path grammar).
    _write_state(
        tmp_path,
        [
            {
                "locator": loc,
                "kind": "reference",
                "workspace": "ws",
                "ref_id": rid,
                "members": [
                    {
                        "key": "form-0",
                        "sha256": _sha256_file(outside),
                        "path": str(outside.resolve()),
                        "category": "form",
                        "label": "transcript",
                        "media_type": "text/plain; charset=utf-8",
                        "download_name": "outside.txt",
                        "text_capable": True,
                    }
                ],
            }
        ],
    )
    assert load_public_read_state(tmp_path).valid is False
    c = _public_client(tmp_path)
    page = c.get(f"/p/{loc}")
    assert page.status_code == 404
    body = c.get(f"/p/{loc}/content/form-0")
    assert body.status_code == 404
    assert "ESCAPED" not in body.text
    f = c.get(f"/p/{loc}/file/form-0")
    assert f.status_code == 404
    assert f.content == b""


# ---------------------------------------------------------------------------
# Closure freeze / publication snapshot security
# ---------------------------------------------------------------------------


def test_s2_added_form_not_readable_under_old_generation(tmp_path):
    """New form added after publication cannot be authorized on old generation."""
    root, rid, _ = _full_public_root(tmp_path)
    c = _public_client(root)
    assert c.get(f"/p/{LOC_REF}/content/form-0").status_code == 200
    assert c.get(f"/p/{LOC_REF}/content/form-2").status_code == 404

    ws = Workspace.open(root / "ws")
    ref_dir = ws.references_dir() / rid
    (ref_dir / "extra.md").write_text("NEW-FORM-SECRET\n", encoding="utf-8")
    man = ws.read_manifest(rid)
    man.forms.append(
        Form(
            role="note",
            location=f"references/{rid}/extra.md",
            hash="new",
        )
    )
    ws.write_manifest(rid, man)

    # Same generation / state file — new form must stay denied.
    r = c.get(f"/p/{LOC_REF}/content/form-2")
    _assert_fixed_denial(r, "html")
    assert "NEW-FORM-SECRET" not in r.text
    page = c.get(f"/p/{LOC_REF}")
    assert page.status_code == 200
    assert "form-2" not in page.text
    assert "NEW-FORM-SECRET" not in page.text


def test_s2_rebound_form_location_does_not_change_public_descriptor(tmp_path):
    """Manifest form rebind / role change cannot alter public descriptors.

    Descriptors come only from frozen state. Content still resolves via state
    path+hash; a rebind that leaves the frozen path intact keeps the same
    public label/media/download_name. Role strings never appear from manifest.
    """
    root, rid, _ = _full_public_root(tmp_path)
    c = _public_client(root)
    before = c.get(f"/api/public/v1/documents/{LOC_REF}")
    assert before.status_code == 200
    before_members = {
        m["key"]: (m["label"], m["media_type"], m["category"])
        for m in before.json()["members"]
    }
    assert before_members["form-0"][0] == "transcript"
    # Sensitive role must not leak as free text beyond the frozen public label.
    assert "source_class" not in before.text
    assert "stream" not in before.text

    ws = Workspace.open(root / "ws")
    ref_dir = ws.references_dir() / rid
    (ref_dir / "rebound.md").write_text("REBOUND-SECRET\n", encoding="utf-8")
    man = ws.read_manifest(rid)
    # Change role + location in manifest under same generation.
    man.forms[0] = Form(
        role="classified-internal-role",
        location=f"references/{rid}/rebound.md",
        hash="rb",
    )
    ws.write_manifest(rid, man)

    after = c.get(f"/api/public/v1/documents/{LOC_REF}")
    assert after.status_code == 200
    after_members = {
        m["key"]: (m["label"], m["media_type"], m["category"])
        for m in after.json()["members"]
    }
    # Public descriptors unchanged (state-only).
    assert after_members == before_members
    assert "classified-internal-role" not in after.text
    # Content still served from frozen path (transcript.md), not rebound.
    r = c.get(f"/p/{LOC_REF}/content/form-0")
    assert r.status_code == 200
    assert "PUBLIC-REF-BODY" in r.text
    assert "REBOUND-SECRET" not in r.text


def test_s2_same_hash_form_rebind_cannot_expand_path(tmp_path):
    """Publishing a path is authoritative; same-hash file elsewhere is not granted."""
    root, rid, _ = _full_public_root(tmp_path)
    c = _public_client(root)
    ws = Workspace.open(root / "ws")
    ref_dir = ws.references_dir() / rid
    # Copy identical bytes to a new name; freeze still points at transcript.md.
    data = (ref_dir / "transcript.md").read_bytes()
    (ref_dir / "clone.md").write_bytes(data)
    man = ws.read_manifest(rid)
    man.forms[0] = Form(
        role="transcript",
        location=f"references/{rid}/clone.md",
        hash="same",
    )
    ws.write_manifest(rid, man)
    # Undeclared clone path is not readable as a new member.
    assert c.get(f"/p/{LOC_REF}/content/form-2").status_code == 404
    # Original frozen path still works.
    assert "PUBLIC-REF-BODY" in c.get(f"/p/{LOC_REF}/content/form-0").text
    # Attempting to serve via artifact name not in state fails.
    assert c.get(f"/p/{LOC_REF}/file/artifact-clone.md").status_code == 404


def test_s2_replaced_member_content_denied_but_siblings_remain(tmp_path):
    """Content replacement changes hash → that member denies; root stays up."""
    root, rid, _ = _full_public_root(tmp_path)
    c = _public_client(root)
    assert "PUBLIC-DIGEST" in c.get(f"/p/{LOC_REF}/content/digest").text

    ws = Workspace.open(root / "ws")
    digest = ws.references_dir() / rid / "digest.md"
    digest.write_text("# Digest\n\nREPLACED-CONTENT\n", encoding="utf-8")

    r = c.get(f"/p/{LOC_REF}/content/digest")
    _assert_fixed_denial(r, "html")
    assert "REPLACED-CONTENT" not in r.text
    assert "PUBLIC-DIGEST" not in r.text
    # presentation listing keeps root + other valid members
    page = c.get(f"/p/{LOC_REF}")
    assert page.status_code == 200
    assert "Meeting Public" in page.text
    # drifted member omitted from listing links
    assert "/content/digest" not in page.text
    # sibling still works
    assert "PUBLIC-REF-BODY" in c.get(f"/p/{LOC_REF}/content/form-0").text
    assert "PUBLIC-PROSE" in c.get(f"/p/{LOC_REF}/content/prose").text



def test_s2_withdraw_and_rebind_next_request_denies(tmp_path):
    """Withdraw then rebind locator to another root: next request uses new gen."""
    root, _, _ = _full_public_root(tmp_path)
    c = _public_client(root)
    assert c.get(f"/p/{LOC_TARGET}/content/body").status_code == 200

    ws = Workspace.open(root / "ws")
    # withdraw target; rebind LOC_TARGET to assessment (still requires fresh hash)
    _write_state(
        root,
        [
            _target_root_entry(
                ws,
                locator=LOC_TARGET,
                target_path="assessment.md",
                display_label="Rebound",
            ),
            _ref_root_entry(ws, "2026-01-01-pub"),
        ],
        generation=9,
    )
    # New generation authorizes assessment only under the rebound
    r = c.get(f"/p/{LOC_TARGET}/content/body")
    assert r.status_code == 200
    assert "should-not-leak" in r.text
    assert "alpha-secret-token" not in r.text

    # withdraw again → empty roots
    _write_state(root, [], generation=10)
    _assert_fixed_denial(c.get(f"/p/{LOC_TARGET}"), "html")
    _assert_fixed_denial(c.get(f"/p/{LOC_REF}"), "html")


def test_s2_symlink_reference_directory_rejected(tmp_path):
    """Symlink reference directory must not become a public root."""
    ws = _setup_workspace(tmp_path)
    real = tmp_path / "outside-ref"
    real.mkdir()
    (real / "digest.md").write_text("LINKED-SECRET\n", encoding="utf-8")
    (real / "manifest.yaml").write_text(
        "id: linked\ntitle: L\nforms: []\n", encoding="utf-8"
    )
    # Create symlink at references/linked → outside
    refs = ws.references_dir()
    refs.mkdir(parents=True, exist_ok=True)
    link = refs / "linked"
    link.symlink_to(real, target_is_directory=True)

    # State itself may load (metadata only), but live root validity fails.
    _write_state(
        tmp_path,
        [
            {
                "locator": LOC_OTHER,
                "kind": "reference",
                "workspace": "ws",
                "ref_id": "linked",
                "members": [
                    _digest_member(real / "digest.md"),
                ],
            }
        ],
    )
    # Metadata snapshot can be structurally valid (no FS checks at load).
    snap = load_public_read_state(tmp_path)
    # Either invalid at load (if we choose) or valid-but-unreadable at Permit.
    c = _public_client(tmp_path)
    r = c.get(f"/p/{LOC_OTHER}/content/digest")
    _assert_fixed_denial(r, "html")
    assert "LINKED-SECRET" not in r.text
    assert c.get(f"/p/{LOC_OTHER}").status_code == 404
    _ = snap  # structural load path exercised either way


def test_s2_symlink_references_directory_rejected(tmp_path):
    """Symlink ``references/`` directory poisons live root validation."""
    ws = _setup_workspace(tmp_path)
    rid = _add_public_reference(ws)
    # Move real references aside and replace with symlink
    real_refs = ws.root / "references-real"
    ws.references_dir().rename(real_refs)
    (ws.root / "references").symlink_to(real_refs, target_is_directory=True)

    _write_state(
        tmp_path,
        [
            _target_root_entry(ws),
            {
                "locator": LOC_REF,
                "kind": "reference",
                "workspace": "ws",
                "ref_id": rid,
                "members": [
                    _digest_member(real_refs / rid / "digest.md"),
                ],
            },
        ],
    )
    # State metadata may load; live Permit must deny the reference root.
    c = _public_client(tmp_path)
    assert c.get(f"/p/{LOC_REF}").status_code == 404
    # Target still uses workspace body path (not under references/) — may work
    # if constitution/body resolve; references symlink must not grant ref root.
    # Whole-app still must not leak linked content.
    r = c.get(f"/p/{LOC_REF}/content/digest")
    _assert_fixed_denial(r, "html")


def test_s2_shared_form_between_two_public_refs_denied(tmp_path):
    """Two public refs claiming the same physical form → ownership conflict."""
    ws = _setup_workspace(tmp_path)
    a = "2026-01-10-a"
    b = "2026-01-10-b"
    for rid in (a, b):
        d = ws.references_dir() / rid
        d.mkdir(parents=True)
        (d / "digest.md").write_text(f"digest-{rid}\n", encoding="utf-8")
    shared = ws.references_dir() / a / "shared.md"
    shared.write_text("SHARED-BODY\n", encoding="utf-8")
    for rid in (a, b):
        ws.write_manifest(
            rid,
            Manifest(
                id=rid,
                title=rid,
                forms=[
                    Form(
                        role="transcript",
                        location=f"references/{a}/shared.md",
                        hash="s",
                    )
                ],
            ),
        )

    # b cannot declare path outside its own ref dir via relative escape;
    # declaring the same relative name under b would be a different path.
    # Conflict: both claim path that lexically resolves to a's shared.md by
    # using absolute-like or cross path — rejected by path grammar for b if
    # path escapes, or by path_owner if same canonical.
    _write_state(
        tmp_path,
        [
            {
                "locator": LOC_REF,
                "kind": "reference",
                "workspace": "ws",
                "ref_id": a,
                "members": [
                    _form_member(
                        "form-0",
                        shared,
                        rel_path="shared.md",
                        label="transcript",
                    ),
                    _digest_member(ws.references_dir() / a / "digest.md"),
                ],
            },
            {
                "locator": LOC_OTHER,
                "kind": "reference",
                "workspace": "ws",
                "ref_id": b,
                "members": [
                    # Attempt to claim a's file via nested relative path — rejected.
                    {
                        "key": "form-0",
                        "sha256": _sha256_file(shared),
                        "path": f"../{a}/shared.md",
                        "category": "form",
                        "label": "transcript",
                        "media_type": _MD,
                        "download_name": "shared.md",
                        "text_capable": True,
                    },
                    _digest_member(ws.references_dir() / b / "digest.md"),
                ],
            },
        ],
    )
    assert load_public_read_state(tmp_path).valid is False
    c = _public_client(tmp_path)
    assert c.get(f"/p/{LOC_REF}/content/form-0").status_code == 404
    assert c.get(f"/p/{LOC_OTHER}/content/form-0").status_code == 404


def test_s2_duplicate_declared_physical_path_fail_closed(tmp_path):
    """Two roots declaring the same lexical physical path fail closed."""
    ws = _setup_workspace(tmp_path)
    a = "2026-01-14-a"
    b = "2026-01-14-b"
    for rid in (a, b):
        d = ws.references_dir() / rid
        d.mkdir(parents=True)
        (d / "digest.md").write_text(f"digest-{rid}\n", encoding="utf-8")
        (d / "x.md").write_text("x\n", encoding="utf-8")
        ws.write_manifest(rid, Manifest(id=rid, title=rid, forms=[]))
    # Force same canonical by publishing target body and a reference form path
    # that collides only if someone reuses target path under ref — instead
    # publish two refs with identical workspace-relative collision via same
    # workspace+path through target + bogus: use two targets same path denied
    # by identity. Use two members in one root with same path.
    _write_state(
        tmp_path,
        [
            {
                "locator": LOC_REF,
                "kind": "reference",
                "workspace": "ws",
                "ref_id": a,
                "members": [
                    _digest_member(ws.references_dir() / a / "digest.md"),
                    _form_member(
                        "form-0",
                        ws.references_dir() / a / "x.md",
                        rel_path="x.md",
                        label="x",
                    ),
                    # second member same path
                    {
                        "key": "form-1",
                        "sha256": _sha256_file(ws.references_dir() / a / "x.md"),
                        "path": "x.md",
                        "category": "form",
                        "label": "y",
                        "media_type": _MD,
                        "download_name": "x.md",
                        "text_capable": True,
                    },
                ],
            }
        ],
    )
    assert load_public_read_state(tmp_path).valid is False


def test_s2_reference_form_pointing_at_target_body_denied(tmp_path):
    """Reference form must not claim a target body as a public member."""
    ws = _setup_workspace(tmp_path)
    rid = "2026-01-11-x"
    (ws.references_dir() / rid).mkdir(parents=True)
    (ws.references_dir() / rid / "digest.md").write_text("d\n", encoding="utf-8")
    body = ws.root / "understanding.md"
    ws.write_manifest(
        rid,
        Manifest(
            id=rid,
            title="x",
            forms=[
                Form(
                    role="transcript",
                    location="understanding.md",
                    hash="t",
                )
            ],
        ),
    )
    # path outside ref dir via absolute or parent — fail at state load
    _write_state(
        tmp_path,
        [
            _target_root_entry(ws),
            {
                "locator": LOC_REF,
                "kind": "reference",
                "workspace": "ws",
                "ref_id": rid,
                "members": [
                    {
                        "key": "form-0",
                        "sha256": _sha256_file(body),
                        "path": "../../understanding.md",
                        "category": "form",
                        "label": "transcript",
                        "media_type": _MD,
                        "download_name": "understanding.md",
                        "text_capable": True,
                    },
                    _digest_member(ws.references_dir() / rid / "digest.md"),
                ],
            },
        ],
    )
    assert load_public_read_state(tmp_path).valid is False
    c = _public_client(tmp_path)
    assert c.get(f"/p/{LOC_REF}/content/form-0").status_code == 404
    # target itself is also denied because whole snapshot invalid
    assert c.get(f"/p/{LOC_TARGET}/content/body").status_code == 404


def test_s2_public_private_root_path_collision_denied(tmp_path):
    """Public root cannot expose a physical file owned by a private root."""
    ws = _setup_workspace(tmp_path)
    pub = "2026-01-12-pub"
    priv = "2026-01-12-priv"
    for rid in (pub, priv):
        d = ws.references_dir() / rid
        d.mkdir(parents=True)
        (d / "digest.md").write_text(f"{rid}\n", encoding="utf-8")
        ws.write_manifest(
            rid,
            Manifest(id=rid, title=rid, forms=[]),
        )
    # Craft state that tries to publish private digest under public ref key via
    # path escape — rejected by path grammar.
    priv_digest = ws.references_dir() / priv / "digest.md"
    _write_state(
        tmp_path,
        [
            {
                "locator": LOC_REF,
                "kind": "reference",
                "workspace": "ws",
                "ref_id": pub,
                "members": [
                    {
                        "key": "artifact-digest.md",
                        "sha256": _sha256_file(priv_digest),
                        "path": f"../{priv}/digest.md",
                        "category": "artifact",
                        "label": "artifact-digest.md",
                        "media_type": _MD,
                        "download_name": "digest.md",
                        "text_capable": True,
                    },
                    _digest_member(ws.references_dir() / pub / "digest.md"),
                ],
            }
        ],
    )
    assert load_public_read_state(tmp_path).valid is False


def test_s2_hash_change_after_permit_download_denied(tmp_path):
    """Download revalidates hash; mid-flight content change → empty 404."""
    root, rid, _ = _full_public_root(tmp_path)
    rdr = AnonymousPublicReader(root)
    permit = rdr.permit(LOC_REF, "form-1", want_text=False)
    assert permit.member is not None
    # mutate bytes after permit
    path = Workspace.open(root / "ws").references_dir() / rid / "shot.png"
    path.write_bytes(b"MUTATED-BYTES-NOT-PNG")
    with pytest.raises(PublicNotFound):
        rdr.revalidate_permit_bytes(permit)
    c = _public_client(root)
    f = c.get(f"/p/{LOC_REF}/file/form-1")
    assert f.status_code == 404
    assert f.content == b""
    assert "content-disposition" not in {k.lower() for k in f.headers.keys()}


def test_s2_invalid_utf8_member_denied(tmp_path):
    """Text member with invalid UTF-8 fails closed (no partial decode)."""
    ws = _setup_workspace(tmp_path)
    rid = "2026-01-13-bin"
    d = ws.references_dir() / rid
    d.mkdir(parents=True)
    bad = d / "transcript.md"
    bad.write_bytes(b"ok\n\xff\xfe bad utf-8\n")
    (d / "digest.md").write_text("d\n", encoding="utf-8")
    ws.write_manifest(
        rid,
        Manifest(
            id=rid,
            title="bin",
            forms=[
                Form(
                    role="transcript",
                    location=f"references/{rid}/transcript.md",
                    hash="b",
                )
            ],
        ),
    )
    _write_state(
        tmp_path,
        [
            {
                "locator": LOC_REF,
                "kind": "reference",
                "workspace": "ws",
                "ref_id": rid,
                "members": [
                    _form_member(
                        "form-0",
                        bad,
                        rel_path="transcript.md",
                        label="transcript",
                    ),
                    _digest_member(d / "digest.md"),
                ],
            }
        ],
    )
    # Load succeeds (hash freezes bytes); text representation denies.
    assert load_public_read_state(tmp_path).valid is True
    c = _public_client(tmp_path)
    r = c.get(f"/p/{LOC_REF}/content/form-0")
    _assert_fixed_denial(r, "html")
    api = c.get(f"/api/public/v1/documents/{LOC_REF}/members/form-0")
    _assert_fixed_denial(api, "json")
    # binary/file entry can still serve raw bytes after revalidation
    f = c.get(f"/p/{LOC_REF}/file/form-0")
    assert f.status_code == 200
    assert b"\xff\xfe" in f.content


def test_s2_search_corrupt_candidate_blocks_all(tmp_path):
    """Any invalid/unowned candidate collapses the entire search."""
    root, rid, _ = _full_public_root(tmp_path)
    c = _public_client(root)
    assert c.get("/api/public/v1/search", params={"q": "Public"}).status_code == 200

    # Corrupt one published member → that candidate (and thus search) fails.
    ws = Workspace.open(root / "ws")
    (ws.references_dir() / rid / "digest.md").write_text(
        "CORRUPT-NOW\n", encoding="utf-8"
    )
    r = c.get("/api/public/v1/search", params={"q": "Public"})
    _assert_fixed_denial(r, "json")
    r2 = c.get("/p/search", params={"q": "Public"})
    _assert_fixed_denial(r2, "html")
    # Even empty query must not return partial results
    r3 = c.get("/api/public/v1/search")
    _assert_fixed_denial(r3, "json")
    # Page presentation still works (member-level tolerance)
    page = c.get(f"/p/{LOC_REF}")
    assert page.status_code == 200
    assert c.get(f"/p/{LOC_TARGET}").status_code == 200


def test_s2_denial_before_member_content_io(tmp_path, monkeypatch):
    """Invalid/undeclared locator/member rejects with zero member content reads."""
    root, rid, _ = _full_public_root(tmp_path)
    rdr = AnonymousPublicReader(root)

    member_reads: list[str] = []
    real_read_bytes = Path.read_bytes

    def spy_read_bytes(self, *a, **k):
        name = str(self)
        # Ignore state file reads; track content-ish paths.
        if PUBLIC_STATE_FILENAME not in name:
            member_reads.append(name)
        return real_read_bytes(self, *a, **k)

    monkeypatch.setattr(Path, "read_bytes", spy_read_bytes)

    # Invalid locator
    member_reads.clear()
    with pytest.raises(PublicNotFound):
        rdr.permit("not-a-locator", "body")
    assert member_reads == []

    # Unpublished well-formed locator
    member_reads.clear()
    with pytest.raises(PublicNotFound):
        rdr.permit(LOC_PRIVATE, "body")
    assert member_reads == []

    # Valid locator, undeclared member
    member_reads.clear()
    with pytest.raises(PublicNotFound):
        rdr.permit(LOC_TARGET, "form-0")
    assert member_reads == []

    # Valid locator, unknown member key
    member_reads.clear()
    with pytest.raises(PublicNotFound):
        rdr.permit(LOC_REF, "form-99")
    assert member_reads == []

    # Selector-style search completes a metadata-only envelope: pure state
    # snapshot + fixed candidate/descriptor walk + generation recheck, then
    # PublicNotFound. No member content I/O, workspace/manifest reads, or
    # permit()/materialization of public candidates.
    member_reads.clear()
    with pytest.raises(PublicNotFound):
        rdr.search(f"locator:{LOC_TARGET}")
    assert member_reads == []

    member_reads.clear()
    with pytest.raises(PublicNotFound):
        rdr.search("member:body")
    assert member_reads == []

    member_reads.clear()
    with pytest.raises(PublicNotFound):
        rdr.search(LOC_TARGET)
    assert member_reads == []

def test_cli_serve_mode_public_read_wires_app(tmp_path, monkeypatch):
    """CLI --mode public-read reaches create_app(..., mode='public-read')."""
    root, _, _ = _full_public_root(tmp_path)
    seen: dict = {}

    def fake_run(serve_root, port=8787, *, mode="console"):
        seen["root"] = Path(serve_root)
        seen["port"] = port
        seen["mode"] = mode

    import kairo.web.server as srv

    monkeypatch.setattr(srv, "run", fake_run)

    result = runner.invoke(
        cli_app,
        ["serve", str(root), "--port", "9999", "--mode", "public-read"],
    )
    assert result.exit_code == 0, result.output
    assert seen.get("mode") == "public-read"
    assert seen.get("port") == 9999
    assert Path(seen["root"]) == root
    assert "public-read" in result.output


def test_cli_serve_default_remains_console(tmp_path, monkeypatch):
    seen: dict = {}

    def fake_run(serve_root, port=8787, *, mode="console"):
        seen["mode"] = mode

    import kairo.web.server as srv

    monkeypatch.setattr(srv, "run", fake_run)
    result = runner.invoke(cli_app, ["serve", str(tmp_path), "--port", "1"])
    assert result.exit_code == 0, result.output
    assert seen.get("mode") == "console"
    assert "console" in result.output


def test_cli_serve_rejects_unknown_mode(tmp_path):
    result = runner.invoke(cli_app, ["serve", str(tmp_path), "--mode", "admin"])
    assert result.exit_code != 0
    assert "public-read" in result.output or "mode" in result.output.lower()


def test_create_app_mode_public_read_factory(tmp_path):
    app = create_app(tmp_path, mode="public-read")
    assert app.title == "kairo public-read"
    routes = {getattr(r, "path", None) for r in app.routes}
    assert "/p/search" in routes
    assert "/p/{locator}" in routes
    assert "/w/{slug}" not in routes
    assert "/healthz" in routes
    # Closed public surface: no framework OpenAPI/docs/redoc routes (L2 §8.2).
    assert "/openapi.json" not in routes
    assert "/docs" not in routes
    assert "/docs/oauth2-redirect" not in routes
    assert "/redoc" not in routes


def test_create_app_and_run_unknown_mode_fail_closed(tmp_path, monkeypatch):
    """Factory/runtime must not silently fall back to Console on unknown mode."""
    with pytest.raises(UnknownAppMode):
        create_app(tmp_path, mode="admin")
    with pytest.raises(UnknownAppMode):
        create_app(tmp_path, mode="")
    with pytest.raises(UnknownAppMode):
        create_app(tmp_path, mode="PUBLIC-READ")

    import kairo.web.server as srv

    monkeypatch.setattr(
        "uvicorn.run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("uvicorn must not start")),
    )
    with pytest.raises(UnknownAppMode):
        srv.run(tmp_path, port=1, mode="admin")


def test_success_responses_no_store(tmp_path):
    root, _, _ = _full_public_root(tmp_path)
    c = _public_client(root)
    for path in (
        f"/p/{LOC_TARGET}",
        f"/p/{LOC_TARGET}/content/body",
        f"/api/public/v1/documents/{LOC_TARGET}",
        "/p/search?q=Public",
        "/api/public/v1/search?q=Public",
        f"/p/{LOC_REF}/file/form-1",
    ):
        r = c.get(path)
        assert r.status_code == 200, path
        assert r.headers.get("cache-control") == "no-store", path


# ---------------------------------------------------------------------------
# Round-2 remediation: unmatched public namespace + selector envelope
# ---------------------------------------------------------------------------


def test_s2_public_app_disables_framework_docs_routes(tmp_path):
    """public-read must not expose FastAPI openapi/docs/redoc surfaces."""
    root, _, _ = _full_public_root(tmp_path)
    app = create_app(root, mode="public-read")
    paths = {getattr(r, "path", None) for r in app.routes}
    for banned in ("/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"):
        assert banned not in paths
    c = _public_client(root)
    for path in ("/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"):
        r = c.get(path)
        _assert_fixed_denial(r, "json")
        assert "swagger" not in r.text.lower()
        assert "openapi" not in r.text.lower()


def test_s2_encoded_and_unmatched_public_paths_fixed_denial(tmp_path):
    """Encoded separators / unreachable members fold into fixed PublicNotFound.

    Starlette decodes %2F/%252F into multi-segment paths that exact
    {locator}/{member} converters do not match. The public app must still
    emit representation-correct 404 + no-store instead of framework defaults.
    """
    root, _, _ = _full_public_root(tmp_path)
    c = _public_client(root)

    html_paths = [
        f"/p/{LOC_TARGET}%2Fextra",
        f"/p/{LOC_TARGET}%252Fextra",
        f"/p/{LOC_TARGET}/content/body%2Fx",
        f"/p/{LOC_TARGET}/content/body%252Fx",
        f"/p/{LOC_TARGET}/extra",
        f"/p/{LOC_TARGET}/content/%2e%2e",
        "/p/foo/bar",
        "/p/",
    ]
    json_paths = [
        f"/api/public/v1/documents/{LOC_TARGET}%2Fx",
        f"/api/public/v1/documents/{LOC_TARGET}%252Fx",
        f"/api/public/v1/documents/{LOC_TARGET}/members/body%2Fx",
        f"/api/public/v1/documents/{LOC_TARGET}/members/body%252Fx",
        "/api/public/v1/nope",
        "/api/public/v1/documents/foo/bar",
    ]
    file_paths = [
        f"/p/{LOC_TARGET}/file/body%2Fx",
        f"/p/{LOC_TARGET}/file/body%252Fx",
        f"/p/{LOC_REF}/file/form-1%2Fextra",
        f"/p/{LOC_REF}/file/form-1%252Fextra",
    ]

    for path in html_paths:
        _assert_fixed_denial(c.get(path), "html")
        # HEAD keeps status/headers; body is empty by HTTP semantics.
        h = c.head(path)
        assert h.status_code == 404
        assert h.headers.get("cache-control") == "no-store"
        assert h.content == b""

    for path in json_paths:
        _assert_fixed_denial(c.get(path), "json")
        h = c.head(path)
        assert h.status_code == 404
        assert h.headers.get("cache-control") == "no-store"
        assert h.headers.get("content-type", "").startswith("application/json")

    for path in file_paths:
        g = c.get(path)
        h = c.head(path)
        _assert_fixed_denial(g, "file")
        _assert_fixed_denial(h, "file")
        assert "content-disposition" not in {k.lower() for k in g.headers.keys()}
        assert "content-disposition" not in {k.lower() for k in h.headers.keys()}

    # Outside public namespaces: still fixed JSON 404 (no console/docs leak).
    for path in ("/w/ws", "/something-else", "/glossary"):
        _assert_fixed_denial(c.get(path), "json")

    # Legitimate routes must not regress through the catch-all sinks.
    assert c.get(f"/p/{LOC_TARGET}").status_code == 200
    assert c.get(f"/p/{LOC_TARGET}/content/body").status_code == 200
    assert c.get(f"/p/{LOC_REF}/file/form-1").status_code == 200
    assert c.head(f"/p/{LOC_REF}/file/form-1").status_code == 200
    assert c.get(f"/api/public/v1/documents/{LOC_TARGET}").status_code == 200
    assert c.get("/p/search").status_code == 200
    assert c.get("/healthz").status_code == 200
    assert c.get("/").status_code == 200


def test_s2_search_selector_metadata_only_zero_content_io(tmp_path, monkeypatch):
    """Selector rejection is metadata-only: fixed 404, zero non-state I/O.

    Page and API search both enter the reader envelope. Selector queries must
    not call permit()/member materialization or read workspace/member/manifest
    bytes. Ordinary no-hit text search still materializes members (fail-closed).
    """
    root, _, _ = _full_public_root(tmp_path)

    reads: list[str] = []
    real_read_bytes = Path.read_bytes
    real_read_text = Path.read_text

    def spy_read_bytes(self, *a, **k):
        reads.append(f"bytes:{self}")
        return real_read_bytes(self, *a, **k)

    def spy_read_text(self, *a, encoding=None, errors=None):
        reads.append(f"text:{self}")
        return real_read_text(self, *a, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_bytes", spy_read_bytes)
    monkeypatch.setattr(Path, "read_text", spy_read_text)

    class TracingReader(AnonymousPublicReader):
        def __init__(self, serve_root: Path) -> None:
            super().__init__(serve_root)
            self.events: list[tuple] = []

        def current_snapshot(self):  # type: ignore[override]
            self.events.append(("snapshot",))
            return super().current_snapshot()

        def permit(  # type: ignore[override]
            self,
            locator_raw: str | None,
            member_raw: str | None = "presentation",
            *,
            want_text: bool = True,
        ):
            self.events.append(
                (
                    "permit",
                    locator_raw,
                    member_raw if member_raw is not None else "presentation",
                )
            )
            return super().permit(locator_raw, member_raw, want_text=want_text)

    def non_state_reads(entries: list[str]) -> list[str]:
        return [r for r in entries if PUBLIC_STATE_FILENAME not in r]

    selectors = (
        f"locator:{LOC_TARGET}",
        "member:body",
        LOC_TARGET,
        f"{LOC_REF}/content/body",
    )

    # Direct reader: selector path is snapshot + metadata walk only.
    for q in selectors:
        rdr = TracingReader(root)
        reads.clear()
        with pytest.raises(PublicNotFound):
            rdr.search(q)
        assert non_state_reads(reads) == [], (q, reads)
        assert all(ev[0] == "snapshot" for ev in rdr.events), rdr.events
        assert len(rdr.events) >= 2  # initial + generation recheck
        assert not any(ev[0] == "permit" for ev in rdr.events)

    # Ordinary no-hit free-text search still materializes members.
    miss = TracingReader(root)
    reads.clear()
    miss_hits = miss.search("zzznomatch_qqq_unique_token")
    assert miss_hits == []
    assert any(ev[0] == "permit" for ev in miss.events)
    assert any(ev[0] == "permit" and ev[2] == "presentation" for ev in miss.events)
    assert any(ev[0] == "permit" and ev[2] != "presentation" for ev in miss.events)
    assert non_state_reads(reads), "ordinary search must materialize member bytes"

    # Ordinary successful text search still works and is not a selector.
    ok = TracingReader(root)
    hits = ok.search("PUBLIC-DIGEST")
    assert any(p.locator == LOC_REF for p in hits)
    assert any(ev[0] == "permit" for ev in ok.events)

    # HTTP adapters: page + API selector → fixed denial; only state file I/O.
    c = _public_client(root)
    for q in selectors:
        reads.clear()
        _assert_fixed_denial(c.get("/p/search", params={"q": q}), "html")
        assert non_state_reads(reads) == [], ("html", q, reads)

        reads.clear()
        _assert_fixed_denial(
            c.get("/api/public/v1/search", params={"q": q}), "json"
        )
        assert non_state_reads(reads) == [], ("json", q, reads)
