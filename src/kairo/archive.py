"""#136: coding agent 会话归档。回执协议 + manifest 单点提交。"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

import yaml

from kairo.models import ArchiveBinding, Form, Manifest
from kairo.workspace import Workspace, WorkspaceNotFound, default_reference_title

ENVELOPE_RE = re.compile(
    r'^<KAIRO_ARCHIVE_RECEIPT preserve="verbatim">'
    r"KAIRO_ARCHIVE/1 "
    r"([0-9a-f]{32}) (\S+) (\S+) (\d+) (\d+) ([0-9a-f]{64})"
    r"</KAIRO_ARCHIVE_RECEIPT>$"
)


class ArchiveError(Exception):
    """不可恢复的归档错误(CLI 退出码 1)。"""


class NeedChoice(Exception):
    """缺确认或无法可靠续接(CLI 退出码 2)。"""

    def __init__(
        self,
        reason: str,
        workspaces: list[dict],
        archives: list[dict],
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.workspaces = workspaces
        self.archives = archives


@dataclass(frozen=True)
class ArchiveReceipt:
    key: str
    workspace: str
    reference: str
    form_index: int
    version: int
    body_sha256: str

    def envelope(self) -> str:
        payload = (
            f"KAIRO_ARCHIVE/1 {self.key} {self.workspace} {self.reference} "
            f"{self.form_index} {self.version} {self.body_sha256}"
        )
        return (
            f'<KAIRO_ARCHIVE_RECEIPT preserve="verbatim">{payload}'
            f"</KAIRO_ARCHIVE_RECEIPT>"
        )


def normalize_newlines(text: str) -> str:
    if text.startswith("\ufeff"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _iter_lines(text: str):
    in_fence = False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            yield line, in_fence
            in_fence = not in_fence
            continue
        yield line, in_fence


def _complete_envelopes(text: str) -> list[tuple[int, ArchiveReceipt]]:
    found: list[tuple[int, ArchiveReceipt]] = []
    for i, (line, in_fence) in enumerate(_iter_lines(text)):
        if in_fence:
            continue
        m = ENVELOPE_RE.fullmatch(line)
        if not m:
            continue
        found.append(
            (
                i,
                ArchiveReceipt(
                    key=m.group(1),
                    workspace=m.group(2),
                    reference=m.group(3),
                    form_index=int(m.group(4)),
                    version=int(m.group(5)),
                    body_sha256=m.group(6),
                ),
            )
        )
    return found


def session_body(text: str) -> str:
    text = normalize_newlines(text)
    kept: list[str] = []
    for line, in_fence in _iter_lines(text):
        if not in_fence and ENVELOPE_RE.fullmatch(line):
            continue
        kept.append(line)
    return "\n".join(kept).strip("\n")


def body_sha256(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _file_hash12(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def _open_listed(serve_root: Path, slug: str) -> Workspace | None:
    dest = (serve_root / slug).resolve()
    if dest.parent != serve_root.resolve():
        return None
    try:
        return Workspace.open(dest)
    except WorkspaceNotFound:
        return None


def _list_workspace_summaries(serve_root: Path) -> list[dict]:
    cwd = Path.cwd().resolve()
    items: list[dict] = []
    if not serve_root.is_dir():
        return items
    for d in sorted(p for p in serve_root.iterdir() if p.is_dir()):
        if not (d / "constitution.yaml").is_file():
            continue
        try:
            ws = Workspace.open(d)
        except WorkspaceNotFound:
            continue
        items.append(
            {
                "slug": d.name,
                "topic": ws.constitution.topic,
                "cwd": d.resolve() == cwd,
            }
        )
    return items


def _list_archives(ws: Workspace, slug: str) -> list[dict]:
    out: list[dict] = []
    for ref_id in ws.list_reference_ids():
        man = ws.read_manifest(ref_id)
        if man.archive is None:
            continue
        out.append(
            {
                "workspace": slug,
                "reference": ref_id,
                "title": man.title,
                "version": man.archive.version,
            }
        )
    return out


def _need(
    reason: str, serve_root: Path, slug: str | None = None
) -> NeedChoice:
    workspaces = _list_workspace_summaries(serve_root)
    archives: list[dict] = []
    if slug:
        opened = _open_listed(serve_root, slug)
        if opened is not None:
            archives = _list_archives(opened, slug)
    return NeedChoice(reason, workspaces, archives)


def _receipt_matches_disk(
    rec: ArchiveReceipt, serve_root: Path
) -> tuple[Workspace, Manifest] | None:
    ws = _open_listed(serve_root, rec.workspace)
    if ws is None or rec.reference not in ws.list_reference_ids():
        return None
    man = ws.read_manifest(rec.reference)
    bind = man.archive
    if bind is None:
        return None
    if (
        bind.key != rec.key
        or bind.version != rec.version
        or bind.form_index != rec.form_index
        or bind.body_sha256 != rec.body_sha256
        or rec.form_index != 0
        or rec.form_index >= len(man.forms)
    ):
        return None
    loc = Path(man.forms[rec.form_index].location)
    path = loc if loc.is_absolute() else ws.root / loc
    if not path.is_file():
        return None
    stored = session_body(path.read_text(encoding="utf-8"))
    if body_sha256(stored) != rec.body_sha256:
        return None
    return ws, man


def last_valid_receipt(
    text: str, *, serve_root: Path
) -> ArchiveReceipt | None:
    text = normalize_newlines(text)
    for _i, rec in reversed(_complete_envelopes(text)):
        if _receipt_matches_disk(rec, serve_root) is not None:
            return rec
    return None


def _stored_body(ws: Workspace, man: Manifest) -> str:
    loc = Path(man.forms[man.archive.form_index].location)
    path = loc if loc.is_absolute() else ws.root / loc
    return session_body(path.read_text(encoding="utf-8"))


def _atomic_write_text(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)


def _commit_manifest(ws: Workspace, ref_id: str, man: Manifest) -> None:
    path = ws.references_dir() / ref_id / "manifest.yaml"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        yaml.safe_dump(
            man.model_dump(by_alias=True, exclude_none=True),
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _write_archive(
    ws: Workspace,
    *,
    slug: str,
    ref_id: str,
    body: str,
    title: str,
    key: str,
    version: int,
    old_location: str | None,
) -> ArchiveReceipt:
    digest = body_sha256(body)
    rel = f"references/{ref_id}/session.{digest}.md"
    dest = ws.root / rel
    payload = body.encode("utf-8")
    if not (dest.is_file() and dest.read_bytes() == payload):
        _atomic_write_text(dest, body)
    canonical = Form(
        role="source_text",
        location=rel,
        hash=_file_hash12(payload),
        origin="added",
    )
    man_path = ws.references_dir() / ref_id / "manifest.yaml"
    if man_path.is_file():
        man = ws.read_manifest(ref_id)
        forms = list(man.forms)
        idx = man.archive.form_index if man.archive is not None else 0
        if 0 <= idx < len(forms):
            forms[idx] = canonical
        else:
            forms = [canonical]
            idx = 0
        man.forms = forms
        man.title = title
        man.archive = ArchiveBinding(
            key=key,
            version=version,
            form_index=idx,
            body_sha256=digest,
        )
    else:
        man = Manifest(
            id=ref_id,
            title=title,
            source_class="stream",
            forms=[canonical],
            archive=ArchiveBinding(
                key=key,
                version=version,
                form_index=0,
                body_sha256=digest,
            ),
        )
    _commit_manifest(ws, ref_id, man)
    if old_location:
        old = Path(old_location)
        old_path = old if old.is_absolute() else ws.root / old
        if old_path.resolve() != dest.resolve():
            try:
                old_path.unlink()
            except OSError:
                pass
    return ArchiveReceipt(
        key=key,
        workspace=slug,
        reference=ref_id,
        form_index=0,
        version=version,
        body_sha256=digest,
    )


def archive_markdown(
    text: str,
    *,
    serve_root: Path,
    workspace: str | None,
    create: bool,
    bind: str | None,
    title: str | None,
) -> ArchiveReceipt:
    serve_root = Path(serve_root).expanduser().resolve()
    if create and bind:
        raise ArchiveError("--create 与 --bind 不能同时使用")
    if not text or not normalize_newlines(text).strip():
        raise ArchiveError("会话 Markdown 为空")
    body = session_body(text)
    rec = last_valid_receipt(text, serve_root=serve_root)

    if rec is not None:
        matched = _receipt_matches_disk(rec, serve_root)
        assert matched is not None
        ws, man = matched
        stored = _stored_body(ws, man)
        prefix_ok = body == stored or body.startswith(stored)
        if workspace and workspace != rec.workspace and not (create or bind):
            raise ArchiveError(
                f"--workspace {workspace!r} 与回执中的 {rec.workspace!r} 不一致"
            )
        if prefix_ok:
            if create:
                raise ArchiveError("会话可续接,拒绝 --create(会复制活会话)")
            if bind and bind != rec.reference:
                raise ArchiveError(
                    f"会话可续接,拒绝绑定其它 reference:{bind}"
                )
            if body == stored:
                return rec
            return _write_archive(
                ws,
                slug=rec.workspace,
                ref_id=rec.reference,
                body=body,
                title=man.title,
                key=man.archive.key,
                version=man.archive.version + 1,
                old_location=man.forms[0].location,
            )
        # fork
        slug = workspace or rec.workspace
        if not create and not bind:
            raise _need("fork", serve_root, slug)
        return _confirmed_write(
            serve_root,
            slug=slug,
            body=body,
            create=create,
            bind=bind,
            title=title,
        )

    slug = workspace
    if not slug:
        raise _need("need-workspace", serve_root)
    if not create and not bind:
        raise _need("need-bind", serve_root, slug)
    return _confirmed_write(
        serve_root,
        slug=slug,
        body=body,
        create=create,
        bind=bind,
        title=title,
    )


def _confirmed_write(
    serve_root: Path,
    *,
    slug: str,
    body: str,
    create: bool,
    bind: str | None,
    title: str | None,
) -> ArchiveReceipt:
    ws = _open_listed(serve_root, slug)
    if ws is None:
        raise ArchiveError(f"workspace 不存在:{slug}")
    if create:
        ref_id = ws._alloc_ref_id("session")
        return _write_archive(
            ws,
            slug=slug,
            ref_id=ref_id,
            body=body,
            title=title if title else default_reference_title(),
            key=secrets.token_hex(16),
            version=1,
            old_location=None,
        )
    if not bind:
        raise ArchiveError("内部错误:确认写入缺少 --bind")
    if bind not in ws.list_reference_ids():
        raise ArchiveError(f"reference 不存在:{bind}")
    man = ws.read_manifest(bind)
    if man.archive is None:
        raise ArchiveError(f"不是归档 reference:{bind}")
    return _write_archive(
        ws,
        slug=slug,
        ref_id=bind,
        body=body,
        title=title if title else man.title,
        key=man.archive.key,
        version=man.archive.version + 1,
        old_location=man.forms[0].location if man.forms else None,
    )
