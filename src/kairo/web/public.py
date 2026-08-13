"""Anonymous public-read surface (#118).

Explicit public roots are supplied only by a versioned static state file at the
serve root (``public-read.json``). All page / search / file / reference / API
entries share one reader; missing, corrupt, duplicate, out-of-bound, or
unauthorized inputs fail closed as a fixed 404 with ``Cache-Control: no-store``.

Publication snapshot schema (version 3)
---------------------------------------
Exact shape::

    {
      "version": 3,
      "generation": <positive int>,
      "roots": [
        {
          "locator": "p-<url-safe ≥22 chars>",
          "kind": "target",
          "workspace": "<serve-root child dir name>",
          "target_path": "<md relative to workspace>",
          "display_label": "<optional public label>",
          "members": [
            {
              "key": "body",
              "sha256": "<64 lowercase hex>",
              "path": "<same as target_path>",
              "category": "body",
              "label": "body",
              "media_type": "text/markdown; charset=utf-8",
              "download_name": "understanding.md",
              "text_capable": true
            }
          ]
        },
        {
          "locator": "p-...",
          "kind": "reference",
          "workspace": "<slug>",
          "ref_id": "<reference id>",
          "display_label": "<optional>",
          "members": [
            {
              "key": "form-0",
              "sha256": "<64 hex>",
              "path": "transcript.md",
              "category": "form",
              "label": "transcript",
              "media_type": "text/markdown; charset=utf-8",
              "download_name": "transcript.md",
              "text_capable": true
            },
            {
              "key": "digest",
              "sha256": "<64 hex>",
              "path": "digest.md",
              "category": "digest",
              "label": "digest",
              "media_type": "text/markdown; charset=utf-8",
              "download_name": "digest.md",
              "text_capable": true
            }
          ]
        }
      ]
    }

Rules (fail-closed):
- ``version`` must equal ``PUBLIC_STATE_VERSION`` (3). Version 1/2 and any
  incomplete member descriptor are rejected.
- ``load_public_read_state`` reads **only** the state file. It validates
  schema, string bounds, relative path grammar, and declared path uniqueness.
  It never opens workspaces, never parses manifests, and never reads member
  body/attachment bytes. Invalid/undeclared locator or member denials complete
  against this metadata snapshot alone (plus fixed decoy tokens and generation
  recheck).
- Every physical member freezes ``key``, ``sha256``, controlled relative
  ``path``, ``category``, public ``label``, ``media_type``, ``download_name``,
  and ``text_capable``. Descriptors are an allow-list only — never derived from
  live manifest role/location or directory scans.
- Target members: only ``body``; ``path`` must equal ``target_path``.
- Reference members: ``path`` is relative to that reference directory only
  (no absolute, no ``..``, no nested symlink escape at materialization).
- Physical path / ordinary-file / symlink / SHA-256 / UTF-8 / TOCTOU checks run
  only during Permit/materialization of a declared member. A single member
  failure never invalidates the root presentation or sibling members.
- Presentation returns fixed root fields plus independently permitted member
  descriptors from state. Search still fail-closes if any declared member of
  any candidate fails Permit (prior search contract).
- Permit / download re-verify the same snapshot generation and frozen content
  hash before object headers. I/O errors, invalid UTF-8 (text reps), hash
  drift, or generation change collapse to ``PublicNotFound``.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, FastAPI, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from kairo.web.render import render_markdown
from kairo.workspace import Workspace, WorkspaceNotFound

# ---------------------------------------------------------------------------
# Constants / fixed rejection surface
# ---------------------------------------------------------------------------

PUBLIC_STATE_FILENAME = "public-read.json"
PUBLIC_STATE_VERSION = 3

_NO_STORE = {"Cache-Control": "no-store"}
_JSON_NOT_FOUND = {"error": "not_found"}
_HTML_NOT_FOUND = (
    "<!doctype html><html><head><meta charset=\"utf-8\">"
    "<title>Not found</title></head>"
    "<body><h1>Not found</h1></body></html>"
)

# locator: p- + URL-safe token with ≥128 bits entropy (token_urlsafe(16) → 22 chars)
_LOCATOR_RE = re.compile(r"^p-[A-Za-z0-9_-]{22,}$")
_MEMBER_RE = re.compile(r"^(presentation|body|digest|prose|form-\d+|artifact-.+)$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CATEGORY_RE = re.compile(r"^(body|form|digest|prose|artifact)$")
_MEDIA_TYPE_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9!#$&\-^_+.]*\/[A-Za-z0-9][A-Za-z0-9!#$&\-^_+.]*"
    r"(?:;[ \t]*[A-Za-z0-9!#$&\-^_+/]+=[A-Za-z0-9!#$&\-^_+/.=-]+)*$"
)
_DECOY_LOCATOR = "p-" + ("0" * 22)
_DECOY_MEMBER = "body"
_MAX_TOKEN_LEN = 256
_MAX_QUERY_LEN = 512
_MAX_LABEL_LEN = 200
_MAX_MEDIA_TYPE_LEN = 128
_MAX_DOWNLOAD_NAME_LEN = 200
_MAX_REL_PATH_LEN = 512
_MAX_GENERATION_RETRIES = 3

MemberCategory = Literal[
    "presentation", "body", "form", "digest", "prose", "artifact", "file"
]


# ---------------------------------------------------------------------------
# Safe descriptors (only these leave the reader)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SafeMember:
    key: str
    category: str
    label: str
    media_type: str | None = None
    download_name: str | None = None
    text: str | None = None
    path: Path | None = None
    bytes_len: int | None = None
    sha256: str | None = None


@dataclass(frozen=True, slots=True)
class SafePresentation:
    kind: Literal["target", "reference"]
    locator: str
    display_label: str | None
    members: tuple[SafeMember, ...]


@dataclass(frozen=True, slots=True)
class Permit:
    generation: int
    locator: str
    member_key: str
    kind: Literal["target", "reference"]
    display_label: str | None
    presentation: SafePresentation | None = None
    member: SafeMember | None = None


class PublicNotFound(Exception):
    """Collapsed denial — never carries object-specific reason."""


class _GenerationChanged(Exception):
    """Internal: snapshot generation moved mid-envelope; bounded retry."""


# ---------------------------------------------------------------------------
# Static state schema (serve-root public-read.json) — version 3
# ---------------------------------------------------------------------------


class _StateMember(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    sha256: str
    path: str
    category: str
    label: str
    media_type: str
    download_name: str
    text_capable: bool


class _StateRoot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locator: str
    kind: Literal["target", "reference"]
    workspace: str
    target_path: str | None = None
    ref_id: str | None = None
    display_label: str | None = None
    members: list[_StateMember] = Field(default_factory=list)


class PublicReadStateFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    generation: int = Field(ge=1)
    roots: list[_StateRoot] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class FrozenMember:
    """Publication-frozen member descriptor — pure state metadata."""

    key: str
    sha256: str
    category: str
    label: str
    media_type: str
    download_name: str
    rel_path: str
    text_capable: bool
    # Lexically canonical absolute path string for ownership uniqueness.
    # Computed from state only (no filesystem I/O / symlink follow).
    canonical_path: str


@dataclass(frozen=True, slots=True)
class BoundRoot:
    locator: str
    kind: Literal["target", "reference"]
    workspace: str
    target_path: str | None
    ref_id: str | None
    display_label: str | None
    members: dict[str, FrozenMember]  # key → frozen descriptor
    # Lexical base for this root (workspace dir or reference dir).
    canonical_base: str


@dataclass
class GenerationSnapshot:
    generation: int
    locator_to_root: dict[str, BoundRoot] = field(default_factory=dict)
    # (kind, workspace, identity) → locator for uniqueness
    identity_to_locator: dict[tuple[str, str, str], str] = field(default_factory=dict)
    # lexical canonical path → owning identity (exclusive within the snapshot)
    path_owner: dict[str, tuple[str, str, str]] = field(default_factory=dict)
    candidates: tuple[str, ...] = ()
    valid: bool = True
    error: str | None = None


def _identity_key(
    kind: str, workspace: str, target_path: str | None, ref_id: str | None
) -> tuple[str, str, str]:
    if kind == "target":
        return ("target", workspace, target_path or "")
    return ("reference", workspace, ref_id or "")


def _clean_label(label: str | None) -> str | None:
    if label is None:
        return None
    s = str(label).strip()
    if not s or len(s) > _MAX_LABEL_LEN:
        return None
    if any(ch in s for ch in ("\0", "\r", "\n")):
        return None
    return s


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str | None:
    try:
        return _sha256_bytes(path.read_bytes())
    except OSError:
        return None


def _is_ordinary_file(path: Path) -> bool:
    try:
        return path.is_file() and not path.is_symlink()
    except OSError:
        return False


def _lexical_join(*parts: str) -> str:
    """Absolute lexical path join + normpath (no symlink resolution / no I/O)."""
    joined = os.path.join(*parts)
    return os.path.normpath(joined)


def _validate_rel_path(raw: str, *, allow_nested: bool) -> str | None:
    """Return normalized relative path or None if unsafe."""
    if not isinstance(raw, str):
        return None
    s = raw.strip().replace("\\", "/")
    if not s or len(s) > _MAX_REL_PATH_LEN:
        return None
    if s.startswith("/") or s.startswith("~"):
        return None
    if any(ch in s for ch in ("\0", "\r", "\n")):
        return None
    parts = [p for p in s.split("/") if p not in ("",)]
    if not parts:
        return None
    if any(p in {".", ".."} for p in parts):
        return None
    if not allow_nested and len(parts) != 1:
        return None
    return "/".join(parts)


def _validate_download_name(name: str) -> str | None:
    if not isinstance(name, str):
        return None
    s = name.strip()
    if not s or len(s) > _MAX_DOWNLOAD_NAME_LEN:
        return None
    if any(ch in s for ch in ("/", "\\", "\0", "\r", "\n", "\t")):
        return None
    if s in {".", ".."}:
        return None
    return s


def _validate_media_type(mt: str) -> str | None:
    if not isinstance(mt, str):
        return None
    s = mt.strip()
    if not s or len(s) > _MAX_MEDIA_TYPE_LEN:
        return None
    if any(ch in s for ch in ("\0", "\r", "\n")):
        return None
    if not _MEDIA_TYPE_RE.fullmatch(s):
        return None
    return s


def _validate_member_label(label: str) -> str | None:
    if not isinstance(label, str):
        return None
    s = label.strip()
    if not s or len(s) > _MAX_LABEL_LEN:
        return None
    if any(ch in s for ch in ("\0", "\r", "\n")):
        return None
    return s


def _category_for_key(key: str) -> str | None:
    if key == "body":
        return "body"
    if key == "digest":
        return "digest"
    if key == "prose":
        return "prose"
    if key.startswith("form-"):
        return "form"
    if key.startswith("artifact-"):
        return "artifact"
    return None


# ---------------------------------------------------------------------------
# Input normalization (fixed envelope; invalid → decoy)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NormToken:
    token: str
    ok: bool


def normalize_locator(raw: str | None) -> NormToken:
    if raw is None:
        return NormToken(_DECOY_LOCATOR, False)
    if not isinstance(raw, str):
        return NormToken(_DECOY_LOCATOR, False)
    if len(raw) == 0 or len(raw) > _MAX_TOKEN_LEN:
        return NormToken(_DECOY_LOCATOR, False)
    # Reject encoded separators / traversal before regex
    if any(ch in raw for ch in ("/", "\\", "\0", " ", "?", "#")):
        return NormToken(_DECOY_LOCATOR, False)
    if not _LOCATOR_RE.fullmatch(raw):
        return NormToken(_DECOY_LOCATOR, False)
    return NormToken(raw, True)


def normalize_member(raw: str | None) -> NormToken:
    if raw is None:
        return NormToken(_DECOY_MEMBER, False)
    if not isinstance(raw, str):
        return NormToken(_DECOY_MEMBER, False)
    if len(raw) == 0 or len(raw) > _MAX_TOKEN_LEN:
        return NormToken(_DECOY_MEMBER, False)
    if any(ch in raw for ch in ("/", "\\", "\0", " ", "?", "#")):
        return NormToken(_DECOY_MEMBER, False)
    if not _MEMBER_RE.fullmatch(raw):
        return NormToken(_DECOY_MEMBER, False)
    return NormToken(raw, True)


def _looks_like_selector(q: str) -> bool:
    """Detect client attempts to pass locator/member selectors via search.

    Ordinary free-text queries are not selectors. Structured client locators
    (``locator:`` / ``member:`` prefixes, bare canonical locator tokens, or
    path-like member probes) are rejected inside the reader envelope.
    """
    s = q.strip()
    if not s:
        return False
    lower = s.lower()
    if lower.startswith("locator:") or lower.startswith("member:"):
        return True
    # bare canonical locator (optionally with trailing junk after whitespace)
    first = s.split()[0] if s.split() else s
    if _LOCATOR_RE.fullmatch(first):
        return True
    if "/" in s and ("form-" in s or s.endswith("/body") or "/content/" in s):
        return True
    return False


# ---------------------------------------------------------------------------
# Live path helpers (Permit / materialization only — never during state load)
# ---------------------------------------------------------------------------


def _open_workspace(serve_root: Path, slug: str) -> Workspace | None:
    if not slug or "/" in slug or "\\" in slug or slug in {".", ".."}:
        return None
    try:
        ws = Workspace.open(Path(serve_root) / slug)
    except WorkspaceNotFound:
        return None
    except OSError:
        return None
    try:
        ws.root.resolve().relative_to(Path(serve_root).resolve())
    except (ValueError, OSError):
        return None
    return ws


def _workspace_root_resolved(ws: Workspace) -> Path | None:
    try:
        return ws.root.resolve()
    except OSError:
        return None


def _controlled_references_dir(ws: Workspace) -> Path | None:
    """``references/`` must be a real directory inside the workspace."""
    refs = ws.references_dir()
    try:
        if not refs.exists():
            return None
        if refs.is_symlink() or not refs.is_dir():
            return None
    except OSError:
        return None
    root = _workspace_root_resolved(ws)
    if root is None:
        return None
    try:
        resolved = refs.resolve()
        if resolved != root and root not in resolved.parents:
            return None
        if not resolved.is_dir():
            return None
    except OSError:
        return None
    return resolved


def _controlled_ref_dir(ws: Workspace, ref_id: str) -> Path | None:
    """``references/<ref_id>/`` resolved entity must stay under references/."""
    if not ref_id or "/" in ref_id or "\\" in ref_id or ref_id in {".", ".."}:
        return None
    refs = _controlled_references_dir(ws)
    if refs is None:
        return None
    candidate = ws.references_dir() / ref_id
    try:
        if candidate.is_symlink() or not candidate.is_dir():
            return None
    except OSError:
        return None
    try:
        resolved = candidate.resolve()
        if resolved != refs and refs not in resolved.parents:
            return None
        if not resolved.is_dir():
            return None
        if resolved.name != ref_id:
            return None
    except OSError:
        return None
    return resolved


def _target_body_live_path(ws: Workspace, target_path: str) -> Path | None:
    """Resolve a controlled target body; reject escape / non-file / non-md."""
    known = {t.path for t in ws.constitution.targets}
    if target_path not in known:
        return None
    candidate = ws.root / target_path
    if not _is_ordinary_file(candidate):
        return None
    root = _workspace_root_resolved(ws)
    if root is None:
        return None
    try:
        path = candidate.resolve()
        if path != root and root not in path.parents:
            return None
        if not path.is_file() or path.is_symlink():
            return None
    except OSError:
        return None
    if path.suffix != ".md":
        return None
    return path


def _root_still_valid(serve_root: Path, root: BoundRoot) -> bool:
    """Live root validity (workspace / controlled identity). No member bytes."""
    ws = _open_workspace(serve_root, root.workspace)
    if ws is None:
        return False
    if root.kind == "target":
        if not root.target_path:
            return False
        return _target_body_live_path(ws, root.target_path) is not None
    if not root.ref_id:
        return False
    return _controlled_ref_dir(ws, root.ref_id) is not None


def _resolve_member_live_path(
    serve_root: Path, root: BoundRoot, fm: FrozenMember
) -> Path | None:
    """Resolve frozen relative path to an ordinary in-bound file."""
    ws = _open_workspace(serve_root, root.workspace)
    if ws is None:
        return None
    if root.kind == "target":
        if fm.key != "body" or not root.target_path:
            return None
        if fm.rel_path != root.target_path:
            return None
        return _target_body_live_path(ws, root.target_path)

    if not root.ref_id:
        return None
    ref_dir = _controlled_ref_dir(ws, root.ref_id)
    if ref_dir is None:
        return None
    # Join relative segments under the reference directory only.
    rel = fm.rel_path.replace("\\", "/")
    parts = [p for p in rel.split("/") if p]
    if not parts or any(p in {".", ".."} for p in parts):
        return None
    candidate = ws.references_dir() / root.ref_id
    for part in parts:
        candidate = candidate / part
    try:
        if candidate.is_symlink() or not candidate.is_file():
            return None
        resolved = candidate.resolve()
        if resolved != ref_dir and ref_dir not in resolved.parents:
            return None
        if not resolved.is_file() or resolved.is_symlink():
            return None
    except OSError:
        return None
    return resolved


# ---------------------------------------------------------------------------
# State load — metadata only
# ---------------------------------------------------------------------------


def load_public_read_state(serve_root: Path) -> GenerationSnapshot:
    """Load and validate the pure-metadata publication snapshot.

    Reads only ``public-read.json``. Never opens workspaces, never parses
    manifests, never reads member body/attachment bytes. Structural, string,
    path-grammar, and declared-path uniqueness problems yield ``valid=False``.
    """
    path = Path(serve_root) / PUBLIC_STATE_FILENAME
    try:
        if not path.is_file():
            return GenerationSnapshot(generation=0, valid=False, error="missing")
    except OSError:
        return GenerationSnapshot(generation=0, valid=False, error="missing")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return GenerationSnapshot(generation=0, valid=False, error="corrupt")
    try:
        data = PublicReadStateFile.model_validate(raw)
    except ValidationError:
        return GenerationSnapshot(generation=0, valid=False, error="invalid")
    if data.version != PUBLIC_STATE_VERSION:
        return GenerationSnapshot(generation=0, valid=False, error="version")

    serve_abs = _lexical_join(str(Path(serve_root)))
    locator_to_root: dict[str, BoundRoot] = {}
    identity_to_locator: dict[tuple[str, str, str], str] = {}
    path_owner: dict[str, tuple[str, str, str]] = {}
    locator_set: set[str] = set()

    for item in data.roots:
        if not _LOCATOR_RE.fullmatch(item.locator):
            return GenerationSnapshot(generation=0, valid=False, error="locator")
        if item.locator in locator_set:
            return GenerationSnapshot(generation=0, valid=False, error="dup_locator")
        locator_set.add(item.locator)

        ws_name = (item.workspace or "").strip()
        if not ws_name or "/" in ws_name or "\\" in ws_name or ws_name in {".", ".."}:
            return GenerationSnapshot(generation=0, valid=False, error="workspace")

        label = _clean_label(item.display_label)

        if item.kind == "target":
            tp = (item.target_path or "").strip().replace("\\", "/")
            tp_norm = _validate_rel_path(tp, allow_nested=True)
            if tp_norm is None or not tp_norm.endswith(".md"):
                return GenerationSnapshot(generation=0, valid=False, error="target_path")
            # Target identity base = workspace root (lexical).
            canonical_base = _lexical_join(serve_abs, ws_name)
            ik = _identity_key("target", ws_name, tp_norm, None)
            rid: str | None = None
            tp_final: str | None = tp_norm
        else:
            rid_raw = (item.ref_id or "").strip()
            if (
                not rid_raw
                or "/" in rid_raw
                or "\\" in rid_raw
                or rid_raw in {".", ".."}
            ):
                return GenerationSnapshot(generation=0, valid=False, error="ref_id")
            canonical_base = _lexical_join(serve_abs, ws_name, "references", rid_raw)
            ik = _identity_key("reference", ws_name, None, rid_raw)
            rid = rid_raw
            tp_final = None

        if ik in identity_to_locator:
            return GenerationSnapshot(generation=0, valid=False, error="dup_identity")
        identity_to_locator[ik] = item.locator

        frozen: dict[str, FrozenMember] = {}
        seen_keys: set[str] = set()
        for m in item.members:
            if m.key in seen_keys:
                return GenerationSnapshot(generation=0, valid=False, error="dup_member")
            seen_keys.add(m.key)
            if not _MEMBER_RE.fullmatch(m.key) or m.key == "presentation":
                return GenerationSnapshot(generation=0, valid=False, error="members")

            expected_cat = _category_for_key(m.key)
            if expected_cat is None:
                return GenerationSnapshot(generation=0, valid=False, error="members")
            cat = (m.category or "").strip()
            if cat != expected_cat or not _CATEGORY_RE.fullmatch(cat):
                return GenerationSnapshot(generation=0, valid=False, error="category")

            sha = (m.sha256 or "").strip().lower()
            if not _SHA256_RE.fullmatch(sha):
                return GenerationSnapshot(generation=0, valid=False, error="sha256")

            if item.kind == "target":
                if m.key != "body" or cat != "body":
                    return GenerationSnapshot(generation=0, valid=False, error="members")
                rel = _validate_rel_path(m.path, allow_nested=True)
                if rel is None or rel != tp_final:
                    return GenerationSnapshot(generation=0, valid=False, error="path")
                canon = _lexical_join(canonical_base, *rel.split("/"))
            else:
                if m.key == "body":
                    return GenerationSnapshot(generation=0, valid=False, error="members")
                # Reference members: relative to the reference directory only.
                rel = _validate_rel_path(m.path, allow_nested=True)
                if rel is None:
                    return GenerationSnapshot(generation=0, valid=False, error="path")
                canon = _lexical_join(canonical_base, *rel.split("/"))
                # Ensure still under the reference base after normpath.
                base_prefix = canonical_base.rstrip(os.sep) + os.sep
                if canon != canonical_base and not canon.startswith(base_prefix):
                    return GenerationSnapshot(generation=0, valid=False, error="path")

            mem_label = _validate_member_label(m.label)
            if mem_label is None:
                return GenerationSnapshot(generation=0, valid=False, error="label")
            media = _validate_media_type(m.media_type)
            if media is None:
                return GenerationSnapshot(generation=0, valid=False, error="media_type")
            dname = _validate_download_name(m.download_name)
            if dname is None:
                return GenerationSnapshot(generation=0, valid=False, error="download_name")
            if not isinstance(m.text_capable, bool):
                return GenerationSnapshot(generation=0, valid=False, error="text_capable")

            if canon in path_owner:
                return GenerationSnapshot(generation=0, valid=False, error="path_conflict")
            path_owner[canon] = ik

            frozen[m.key] = FrozenMember(
                key=m.key,
                sha256=sha,
                category=cat,
                label=mem_label,
                media_type=media,
                download_name=dname,
                rel_path=rel,
                text_capable=m.text_capable,
                canonical_path=canon,
            )

        locator_to_root[item.locator] = BoundRoot(
            locator=item.locator,
            kind=item.kind,
            workspace=ws_name,
            target_path=tp_final,
            ref_id=rid,
            display_label=label,
            members=frozen,
            canonical_base=canonical_base,
        )

    candidates = tuple(locator_to_root.keys())
    return GenerationSnapshot(
        generation=data.generation,
        locator_to_root=locator_to_root,
        identity_to_locator=identity_to_locator,
        path_owner=path_owner,
        candidates=candidates,
        valid=True,
    )


# ---------------------------------------------------------------------------
# Content materialization (declared members only; fail-closed per member)
# ---------------------------------------------------------------------------


def _descriptor_from_frozen(fm: FrozenMember) -> SafeMember:
    """Public listing descriptor — state fields only, no I/O."""
    return SafeMember(
        key=fm.key,
        category=fm.category,
        label=fm.label,
        media_type=fm.media_type,
        download_name=None if fm.text_capable else fm.download_name,
        text=None,
        path=None,
        bytes_len=None,
        sha256=None,
    )


def _read_frozen_member(
    serve_root: Path,
    root: BoundRoot,
    fm: FrozenMember,
    *,
    want_text: bool,
) -> SafeMember | None:
    """Resolve, re-hash, and optionally decode text; any error → None."""
    path = _resolve_member_live_path(serve_root, root, fm)
    if path is None:
        return None
    try:
        if path.is_symlink() or not path.is_file():
            return None
        data = path.read_bytes()
    except OSError:
        return None
    digest = _sha256_bytes(data)
    if digest != fm.sha256:
        return None
    text: str | None = None
    if want_text and fm.text_capable:
        try:
            text = data.decode("utf-8")
        except UnicodeError:
            return None
    return SafeMember(
        key=fm.key,
        category=fm.category,
        label=fm.label,
        media_type=fm.media_type,
        download_name=fm.download_name,
        text=text,
        path=path,
        bytes_len=len(data),
        sha256=fm.sha256,
    )


def _presentation_from_root(
    serve_root: Path, root: BoundRoot
) -> SafePresentation | None:
    """Build presentation from state descriptors + independent member Permits.

    Root validity is required. Each declared member is materialized on its own;
    failures omit that member only — never the whole presentation.
    """
    if not _root_still_valid(serve_root, root):
        return None
    members: list[SafeMember] = []
    for key in sorted(root.members.keys()):
        fm = root.members[key]
        # Listing requires the member still matches frozen identity/hash, but a
        # single failure must not hide the root or sibling members.
        safe = _read_frozen_member(serve_root, root, fm, want_text=False)
        if safe is None:
            continue
        members.append(_descriptor_from_frozen(fm))
    return SafePresentation(
        kind=root.kind,
        locator=root.locator,
        display_label=root.display_label,
        members=tuple(members),
    )


# ---------------------------------------------------------------------------
# AnonymousPublicReader
# ---------------------------------------------------------------------------


class AnonymousPublicReader:
    """Single authorization gate for the five public-read entries."""

    def __init__(self, serve_root: Path) -> None:
        self.serve_root = Path(serve_root)

    def current_snapshot(self) -> GenerationSnapshot:
        return load_public_read_state(self.serve_root)

    def permit(
        self,
        locator_raw: str | None,
        member_raw: str | None = "presentation",
        *,
        want_text: bool = True,
    ) -> Permit:
        """Fixed envelope with bounded generation retry.

        ``want_text`` is a caller representation requirement: content/API
        member reads must decode UTF-8 text; file GET/HEAD keeps raw bytes
        and must not fail closed solely because text decode is impossible.
        """
        last: Exception | None = None
        for _ in range(_MAX_GENERATION_RETRIES):
            try:
                return self._permit_once(
                    locator_raw, member_raw, want_text=want_text
                )
            except _GenerationChanged as exc:
                last = exc
                continue
        raise PublicNotFound() from last

    def _permit_once(
        self,
        locator_raw: str | None,
        member_raw: str | None,
        *,
        want_text: bool = True,
    ) -> Permit:
        loc = normalize_locator(locator_raw)
        mem = normalize_member(
            member_raw if member_raw is not None else "presentation"
        )

        snap = self.current_snapshot()
        generation = snap.generation if snap.valid else 0

        # Fixed envelope queries against metadata snapshot only. Missing
        # entities continue with decoy-equivalent None; no member content I/O
        # until a declared member is selected below.
        root: BoundRoot | None = None
        if snap.valid and loc.ok:
            root = snap.locator_to_root.get(loc.token)

        presentation: SafePresentation | None = None
        member: SafeMember | None = None
        if root is not None and mem.ok:
            if mem.token == "presentation":
                presentation = _presentation_from_root(self.serve_root, root)
                if presentation is None:
                    root = None
            else:
                fm = root.members.get(mem.token)
                if fm is None:
                    # Undeclared member — deny without content I/O.
                    root = None
                elif not _root_still_valid(self.serve_root, root):
                    root = None
                else:
                    owner = snap.path_owner.get(fm.canonical_path)
                    ik = _identity_key(
                        root.kind, root.workspace, root.target_path, root.ref_id
                    )
                    if owner != ik:
                        root = None
                    else:
                        member = _read_frozen_member(
                            self.serve_root, root, fm, want_text=want_text
                        )
                        if member is None:
                            root = None

        # generation re-check — whole snapshot must still be current
        snap2 = self.current_snapshot()
        if not snap2.valid or snap2.generation != generation:
            raise _GenerationChanged()
        if root is not None:
            r2 = snap2.locator_to_root.get(loc.token)
            if r2 is None:
                raise _GenerationChanged()
            if (
                r2.kind != root.kind
                or r2.workspace != root.workspace
                or r2.target_path != root.target_path
                or r2.ref_id != root.ref_id
                or set(r2.members.keys()) != set(root.members.keys())
            ):
                raise _GenerationChanged()
            for k, fm in root.members.items():
                fm2 = r2.members.get(k)
                if (
                    fm2 is None
                    or fm2.sha256 != fm.sha256
                    or fm2.rel_path != fm.rel_path
                    or fm2.canonical_path != fm.canonical_path
                    or fm2.label != fm.label
                    or fm2.media_type != fm.media_type
                    or fm2.download_name != fm.download_name
                    or fm2.category != fm.category
                    or fm2.text_capable != fm.text_capable
                ):
                    raise _GenerationChanged()
            # re-materialize against snap2 frozen descriptors (no mixed content)
            root = r2
            if mem.token == "presentation":
                presentation = _presentation_from_root(self.serve_root, root)
                if presentation is None:
                    root = None
            elif root is not None:
                fm = root.members.get(mem.token)
                if fm is None:
                    root = None
                    member = None
                elif not _root_still_valid(self.serve_root, root):
                    root = None
                    member = None
                else:
                    member = _read_frozen_member(
                        self.serve_root, root, fm, want_text=want_text
                    )
                    if member is None:
                        root = None

        if not (
            loc.ok
            and mem.ok
            and snap.valid
            and root is not None
            and (presentation is not None or member is not None)
        ):
            raise PublicNotFound()

        return Permit(
            generation=generation,
            locator=loc.token,
            member_key=mem.token,
            kind=root.kind,
            display_label=root.display_label,
            presentation=presentation,
            member=member,
        )

    def revalidate_permit_bytes(self, permit: Permit) -> tuple[bytes, SafeMember]:
        """Re-check generation + frozen hash and return bytes for download.

        Must be called before any object response headers are emitted.
        """
        if (
            permit.member is None
            or permit.member.path is None
            or permit.member.sha256 is None
        ):
            raise PublicNotFound()
        snap = self.current_snapshot()
        if not snap.valid or snap.generation != permit.generation:
            raise PublicNotFound()
        root = snap.locator_to_root.get(permit.locator)
        if root is None:
            raise PublicNotFound()
        fm = root.members.get(permit.member_key)
        if fm is None or fm.sha256 != permit.member.sha256:
            raise PublicNotFound()
        path = _resolve_member_live_path(self.serve_root, root, fm)
        if path is None:
            raise PublicNotFound()
        try:
            if path.is_symlink() or not path.is_file():
                raise PublicNotFound()
            data = path.read_bytes()
        except OSError as exc:
            raise PublicNotFound() from exc
        if _sha256_bytes(data) != fm.sha256:
            raise PublicNotFound()
        safe = SafeMember(
            key=fm.key,
            category=fm.category,
            label=fm.label,
            media_type=fm.media_type,
            download_name=fm.download_name,
            text=None,
            path=path,
            bytes_len=len(data),
            sha256=fm.sha256,
        )
        return data, safe

    def search(self, q: str | None) -> list[Permit]:
        """Search public candidates only; any invalid candidate fails closed.

        Client-supplied locator/member selectors still enter a bounded
        snapshot envelope (``current_snapshot``, fixed metadata candidate
        traversal / descriptor lookup, generation + candidate recheck) and
        then raise a terminal ``PublicNotFound``. Selector denial is pure
        state: it never calls ``permit()``, never materializes members, and
        never becomes a text filter that can return hits.

        Ordinary free-text search fail-closes if **any** declared member of
        a candidate fails Permit (L2 search integrity contract).
        """
        query = (q or "").strip()
        if len(query) > _MAX_QUERY_LEN:
            query = query[:_MAX_QUERY_LEN]

        reject = bool(query and _looks_like_selector(query))
        needle = query.lower()

        snap = self.current_snapshot()
        if not snap.valid:
            raise PublicNotFound()

        if reject:
            # Metadata-only selector envelope: traverse frozen candidates and
            # build listing descriptors from state fields alone. No permit(),
            # member materialization, workspace/manifest resolution, or
            # content I/O — path cost must not depend on member bytes.
            for locator in snap.candidates:
                root = snap.locator_to_root.get(locator)
                if root is None:
                    raise PublicNotFound()
                # Touch fixed metadata used by ordinary search ranking/blob
                # assembly so the envelope stays bounded and candidate-shaped.
                _ = (root.display_label or "").lower()
                _ = root.kind
                _ = locator.lower()
                for key, fm in root.members.items():
                    _ = key.lower()
                    _ = fm.label.lower()
                    _ = _descriptor_from_frozen(fm)
            snap2 = self.current_snapshot()
            if not snap2.valid or snap2.generation != snap.generation:
                raise PublicNotFound()
            if tuple(snap2.candidates) != snap.candidates:
                raise PublicNotFound()
            raise PublicNotFound()

        hits: list[Permit] = []
        for locator in snap.candidates:
            try:
                permit = self.permit(locator, "presentation")
            except PublicNotFound as exc:
                raise PublicNotFound() from exc

            root = snap.locator_to_root.get(locator)
            if root is None:
                raise PublicNotFound()

            # Always walk every declared member (search fail-closed contract).
            label = (permit.display_label or "").lower()
            blob_parts = [label, permit.kind, locator.lower()]
            for key, fm in root.members.items():
                blob_parts.append(key.lower())
                blob_parts.append(fm.label.lower())
                try:
                    mp = self.permit(locator, key)
                except PublicNotFound as exc:
                    raise PublicNotFound() from exc
                if mp.member and mp.member.text:
                    blob_parts.append(mp.member.text.lower())

            if not needle:
                hits.append(permit)
                continue
            if needle in " ".join(blob_parts):
                hits.append(permit)

        # Final generation check — no mixed candidate set
        snap2 = self.current_snapshot()
        if not snap2.valid or snap2.generation != snap.generation:
            raise PublicNotFound()
        if tuple(snap2.candidates) != snap.candidates:
            raise PublicNotFound()
        return hits


# ---------------------------------------------------------------------------
# HTTP adapters (serialize Permit or fixed rejection only)
# ---------------------------------------------------------------------------


def _html_404() -> HTMLResponse:
    return HTMLResponse(_HTML_NOT_FOUND, status_code=404, headers=_NO_STORE)


def _json_404() -> JSONResponse:
    return JSONResponse(_JSON_NOT_FOUND, status_code=404, headers=_NO_STORE)


def _empty_404() -> Response:
    return Response(content=b"", status_code=404, headers=_NO_STORE)


def _page_shell(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=\"en\"><head>"
        "<meta charset=\"utf-8\">"
        f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{html.escape(title)}</title>"
        "<style>"
        "body{font-family:system-ui,sans-serif;margin:1.5rem;line-height:1.5;color:#111}"
        "a{color:#06c}header{margin-bottom:1.25rem}"
        "nav a{margin-right:1rem}.muted{color:#666}"
        "ul{padding-left:1.2rem}pre,article{max-width:52rem}"
        "article.md h1,article.md h2,article.md h3{line-height:1.25}"
        "</style></head><body>"
        "<header><nav>"
        "<a href=\"/\">kairo</a>"
        "<a href=\"/p/search\">Search</a>"
        "</nav></header>"
        f"{body}</body></html>"
    )


def _request_raw_path(request: Request) -> str:
    """Return the wire path (percent-encoding preserved when available)."""
    raw = request.scope.get("raw_path")
    if isinstance(raw, (bytes, bytearray)):
        try:
            return raw.decode("ascii")
        except UnicodeDecodeError:
            return raw.decode("latin-1", errors="replace")
    path = request.scope.get("path")
    if isinstance(path, str) and path:
        return path
    return request.url.path or "/"


def _public_unmatched_kind(raw_path: str) -> str:
    """Map an unmatched public-namespace path to a fixed denial representation.

    Representation is derived only from the path shape (HTML page / JSON API /
    file download). Encoding artifacts in *raw_path* are treated as opaque
    bytes for prefix matching — they never become route parameters.
    """
    path = raw_path or "/"
    # Strip query/fragment if a caller handed us a full URL path form.
    path = path.split("?", 1)[0].split("#", 1)[0]
    if not path.startswith("/"):
        path = "/" + path

    # File download namespace — empty 404 body, no object headers.
    if path.startswith("/p/") and "/file/" in path:
        return "file"
    # JSON public API namespace.
    if path == "/api/public/v1" or path.startswith("/api/public/v1/"):
        return "json"
    # HTML public page namespace (including /p and encoded probes).
    if path == "/p" or path.startswith("/p/"):
        return "html"
    # Outside the closed public surface: still a fixed JSON 404 so framework
    # defaults (docs/console detail bodies) never leak.
    return "json"


def _fixed_unmatched_response(kind: str) -> Response:
    if kind == "file":
        return _empty_404()
    if kind == "json":
        return _json_404()
    return _html_404()


def create_public_app(root: Path) -> FastAPI:
    """Build the isolated public-read FastAPI app (no Console routes)."""
    # Disable framework docs/OpenAPI surfaces — not part of the closed public
    # route set (L2 §8.2). Health, home, and explicit public routes remain.
    app = FastAPI(
        title="kairo public-read",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.root = Path(root)
    app.state.reader = AnonymousPublicReader(Path(root))
    # FastAPI 0.138+ nests ``include_router`` under ``_IncludedRouter``,
    # which has no ``.path``. Extend the app router directly so ``app.routes``
    # exposes the public path set (``/p/search``, ``/p/{locator}``, …) while
    # keeping the same route objects and match order from ``build_public_router``.
    app.router.routes.extend(build_public_router().routes)

    @app.exception_handler(404)
    async def _public_not_found(request: Request, exc: Exception) -> Response:
        # Collapse every unmatched path — including %2F / %252F probes that
        # never reach a locator/member handler — onto the representation-
        # specific fixed PublicNotFound surface (no-store, fixed body).
        _ = exc
        kind = _public_unmatched_kind(_request_raw_path(request))
        return _fixed_unmatched_response(kind)

    @app.exception_handler(405)
    async def _public_method_not_allowed(request: Request, exc: Exception) -> Response:
        # Method mismatches on public paths must not expose framework default
        # bodies either; same fixed denial as an unknown route.
        _ = exc
        kind = _public_unmatched_kind(_request_raw_path(request))
        return _fixed_unmatched_response(kind)

    return app


def build_public_router() -> APIRouter:
    router = APIRouter()

    def reader(request: Request) -> AnonymousPublicReader:
        return request.app.state.reader

    @router.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse({"ok": True}, headers=_NO_STORE)

    @router.get("/", response_class=HTMLResponse)
    def public_home() -> HTMLResponse:
        body = (
            "<h1>Public documents</h1>"
            "<p class=\"muted\">Anonymous read-only surface for explicitly "
            "public documents.</p>"
            "<p><a href=\"/p/search\">Search</a></p>"
        )
        return HTMLResponse(
            _page_shell("kairo public", body),
            headers=_NO_STORE,
        )

    # Static search MUST be registered before /p/{locator}
    @router.get("/p/search", response_class=HTMLResponse)
    def public_search(
        request: Request, q: str | None = Query(default=None)
    ) -> HTMLResponse:
        rdr = reader(request)
        # Always enter reader envelope — no adapter-side selector short-circuit.
        try:
            hits = rdr.search(q)
        except PublicNotFound:
            return _html_404()
        items = []
        for p in hits:
            label = html.escape(p.display_label or p.locator)
            items.append(
                f'<li><a href="/p/{html.escape(p.locator)}">{label}</a>'
                f' <span class="muted">{html.escape(p.kind)}</span></li>'
            )
        # Never echo the raw query into HTML (S2: private/secret strings in q
        # must not appear in the response body, including input value attrs).
        body = (
            f"<h1>Search</h1>"
            f'<form method="get" action="/p/search">'
            f'<input name="q" value="" maxlength="{_MAX_QUERY_LEN}" autocomplete="off">'
            f'<button type="submit">Search</button></form>'
            f"<ul>{''.join(items)}</ul>"
        )
        return HTMLResponse(_page_shell("Search", body), headers=_NO_STORE)

    @router.get("/p/{locator}", response_class=HTMLResponse)
    def public_page(request: Request, locator: str) -> HTMLResponse:
        rdr = reader(request)
        try:
            permit = rdr.permit(locator, "presentation")
        except PublicNotFound:
            return _html_404()
        assert permit.presentation is not None
        pres = permit.presentation
        label = html.escape(pres.display_label or permit.locator)
        links: list[str] = []
        for m in pres.members:
            is_binary = bool(
                m.media_type
                and not m.media_type.startswith("text/")
                and m.category in {"form", "artifact"}
            )
            if is_binary:
                href = f"/p/{html.escape(permit.locator)}/file/{html.escape(m.key)}"
            else:
                href = f"/p/{html.escape(permit.locator)}/content/{html.escape(m.key)}"
            links.append(
                f'<li><a href="{href}">{html.escape(m.label)}</a></li>'
            )
        body = (
            f"<h1>{label}</h1>"
            f'<p class="muted">{html.escape(pres.kind)}</p>'
            f"<ul>{''.join(links)}</ul>"
            f'<p><a href="/p/{html.escape(permit.locator)}/references">References</a></p>'
        )
        return HTMLResponse(
            _page_shell(pres.display_label or "Document", body), headers=_NO_STORE
        )

    @router.get("/p/{locator}/content/{member}", response_class=HTMLResponse)
    def public_content(request: Request, locator: str, member: str) -> HTMLResponse:
        rdr = reader(request)
        try:
            permit = rdr.permit(locator, member)
        except PublicNotFound:
            return _html_404()
        m = permit.member
        if m is None or m.text is None:
            return _html_404()
        title = html.escape(permit.display_label or permit.locator)
        if (m.media_type or "").startswith("text/markdown") or (
            m.download_name or ""
        ).endswith((".md", ".markdown")):
            content = f'<article class="md">{render_markdown(m.text)}</article>'
        else:
            content = f"<pre>{html.escape(m.text)}</pre>"
        body = (
            f"<h1>{title}</h1>"
            f'<p class="muted">{html.escape(m.label)}</p>{content}'
        )
        return HTMLResponse(_page_shell(m.label, body), headers=_NO_STORE)

    @router.api_route(
        "/p/{locator}/file/{member}",
        methods=["GET", "HEAD"],
        response_model=None,
    )
    def public_file(request: Request, locator: str, member: str) -> Response:
        rdr = reader(request)
        try:
            permit = rdr.permit(locator, member, want_text=False)
        except PublicNotFound:
            return _empty_404()
        m = permit.member
        if m is None or m.path is None or m.sha256 is None:
            return _empty_404()
        # Re-validate before any object headers (TOCTOU / hash drift).
        try:
            data, checked = rdr.revalidate_permit_bytes(permit)
        except PublicNotFound:
            return _empty_404()
        headers = {
            **_NO_STORE,
            "Content-Type": checked.media_type or "application/octet-stream",
            "Content-Length": str(len(data)),
        }
        if checked.download_name:
            safe_name = checked.download_name.replace('"', "")
            headers["Content-Disposition"] = f'inline; filename="{safe_name}"'
        if request.method == "HEAD":
            return Response(content=b"", status_code=200, headers=headers)
        return Response(content=data, status_code=200, headers=headers)

    @router.get("/p/{locator}/references", response_class=HTMLResponse)
    def public_references(request: Request, locator: str) -> HTMLResponse:
        """List independently permitted public targets referenced from this root.

        This surface does not re-grant access: only locators that independently
        Permit are listed. Current generation ships an empty list unless the
        presentation itself enumerates other public locators (none by default).
        """
        rdr = reader(request)
        try:
            permit = rdr.permit(locator, "presentation")
        except PublicNotFound:
            return _html_404()
        _ = permit
        body = (
            f"<h1>References</h1>"
            f'<p class="muted">{html.escape(permit.display_label or permit.locator)}</p>'
            f"<ul></ul>"
        )
        return HTMLResponse(_page_shell("References", body), headers=_NO_STORE)

    @router.get("/api/public/v1/documents/{locator}")
    def api_document(request: Request, locator: str) -> JSONResponse:
        rdr = reader(request)
        try:
            permit = rdr.permit(locator, "presentation")
        except PublicNotFound:
            return _json_404()
        assert permit.presentation is not None
        members = [
            {
                "key": m.key,
                "category": m.category,
                "label": m.label,
                "media_type": m.media_type,
            }
            for m in permit.presentation.members
        ]
        payload = {
            "locator": permit.locator,
            "kind": permit.kind,
            "display_label": permit.display_label,
            "members": members,
            "canonical_url": f"/p/{permit.locator}",
        }
        return JSONResponse(payload, headers=_NO_STORE)

    @router.get("/api/public/v1/documents/{locator}/members/{member}")
    def api_member(request: Request, locator: str, member: str) -> JSONResponse:
        rdr = reader(request)
        try:
            permit = rdr.permit(locator, member)
        except PublicNotFound:
            return _json_404()
        m = permit.member
        if m is None or m.text is None:
            return _json_404()
        payload = {
            "locator": permit.locator,
            "key": m.key,
            "category": m.category,
            "label": m.label,
            "media_type": m.media_type,
            "text": m.text,
        }
        return JSONResponse(payload, headers=_NO_STORE)

    @router.get("/api/public/v1/search")
    def api_search(
        request: Request, q: str | None = Query(default=None)
    ) -> JSONResponse:
        rdr = reader(request)
        # Always enter reader envelope — no adapter-side selector short-circuit.
        try:
            hits = rdr.search(q)
        except PublicNotFound:
            return _json_404()
        payload = {
            "results": [
                {
                    "locator": p.locator,
                    "kind": p.kind,
                    "display_label": p.display_label,
                    "canonical_url": f"/p/{p.locator}",
                }
                for p in hits
            ]
        }
        return JSONResponse(payload, headers=_NO_STORE)

    # Catch-all public-namespace sinks registered AFTER exact routes so
    # legitimate locator/member handlers win. Encoded separators (%2F /
    # %252F) decode into multi-segment paths that exact converters reject;
    # these sinks fold them into the same fixed denial surface.
    @router.api_route(
        "/p/{rest:path}",
        methods=["GET", "HEAD"],
        response_model=None,
        include_in_schema=False,
    )
    def public_p_sink(rest: str = "") -> Response:
        _ = rest
        # Representation from the residual path shape (file vs HTML page).
        if "/file/" in f"/p/{rest}":
            return _empty_404()
        return _html_404()

    @router.api_route(
        "/api/public/v1/{rest:path}",
        methods=["GET", "HEAD"],
        response_model=None,
        include_in_schema=False,
    )
    def public_api_sink(rest: str = "") -> Response:
        _ = rest
        return _json_404()

    return router


def run_public(root: Path, port: int = 8787) -> None:
    import uvicorn

    uvicorn.run(create_public_app(Path(root)), host="127.0.0.1", port=port)
