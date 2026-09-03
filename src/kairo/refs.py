"""全局 Ref 身份、Tag catalog、Topic 包含规则成员。源文件不搬家。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kairo.workspace import AddError, Workspace, WorkspaceNotFound

GLOBAL_HOME_REL = Path(".kairo") / "global-home"
CATALOG_REL = Path(".kairo") / "ref-catalog.json"


class RefError(ValueError):
    """Ref / Tag / 包含规则操作非法。"""


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)
    except Exception as exc:
        temp.unlink(missing_ok=True)
        raise RefError(f"保存失败:{exc}") from exc


def catalog_path(serve: Path) -> Path:
    return Path(serve) / CATALOG_REL


def load_catalog(serve: Path) -> dict[str, Any]:
    path = catalog_path(serve)
    if not path.is_file():
        return {"tags": [], "assignments": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RefError(f"无法解析 {path}: {exc}") from exc
    tags = [str(t) for t in raw.get("tags") or [] if str(t).strip()]
    assignments = {
        str(k): [str(t) for t in (v or []) if str(t).strip()]
        for k, v in (raw.get("assignments") or {}).items()
    }
    return {"tags": tags, "assignments": assignments}


def save_catalog(serve: Path, catalog: dict[str, Any]) -> None:
    tags = sorted({str(t).strip() for t in catalog.get("tags") or [] if str(t).strip()})
    assignments = {}
    for key, vals in (catalog.get("assignments") or {}).items():
        cleaned = []
        for t in vals or []:
            name = str(t).strip()
            if name and name not in cleaned:
                cleaned.append(name)
        assignments[str(key)] = cleaned
    _atomic_json(catalog_path(serve), {"tags": tags, "assignments": assignments})


def global_home_path(serve: Path) -> Path:
    return Path(serve) / GLOBAL_HOME_REL


def global_home(serve: Path) -> Workspace:
    path = global_home_path(serve)
    state = path / ".kairo" / "state.json"
    if not state.is_file():
        Workspace.init(path, topic="global")
    return Workspace.open(path)


def is_topic_dir(path: Path) -> bool:
    return path.is_dir() and (path / "constitution.yaml").is_file() and (path / ".kairo" / "state.json").is_file()


def list_topic_slugs(serve: Path) -> list[str]:
    root = Path(serve)
    if not root.is_dir():
        return []
    slugs: list[str] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if child.name.startswith("."):
            continue
        if is_topic_dir(child):
            slugs.append(child.name)
    return slugs


def open_topic(serve: Path, slug: str) -> Workspace:
    dest = Path(serve) / slug
    if not is_topic_dir(dest):
        raise RefError(f"Topic 不存在:{slug}")
    return Workspace.open(dest)


def ref_key(home: str, ref_id: str) -> str:
    home = (home or "").strip()
    ref_id = (ref_id or "").strip()
    if not ref_id:
        raise RefError("ref id 不能为空")
    return f"{home}/{ref_id}" if home else f"global/{ref_id}"


def parse_ref_key(key: str) -> tuple[str, str]:
    raw = (key or "").strip()
    if "/" not in raw:
        return "", raw
    home, ref_id = raw.split("/", 1)
    if home == "global":
        return "", ref_id
    return home, ref_id


@dataclass
class RefRecord:
    home: str
    id: str
    title: str
    source_class: str
    tags: list[str] = field(default_factory=list)
    digest_path: Path | None = None
    dir: Path | None = None

    @property
    def key(self) -> str:
        return ref_key(self.home, self.id)


def add_global_ref(
    serve: Path,
    files: list[Path | str],
    *,
    ref_id: str | None = None,
    role: str | None = None,
    title: str | None = None,
    source_class: str | None = None,
    copy: bool = False,
    occurred_at: str | None = None,
) -> str:
    ws = global_home(serve)
    return ws.add(
        files,
        ref_id=ref_id,
        role=role,
        title=title,
        source_class=source_class,
        copy=copy,
        occurred_at=occurred_at,
    )


def _record_from_ws(ws: Workspace, home: str, ref_id: str, tags: list[str]) -> RefRecord | None:
    try:
        man = ws.read_manifest(ref_id)
    except Exception:
        return None
    digest = ws.references_dir() / ref_id / "digest.md"
    return RefRecord(
        home=home,
        id=ref_id,
        title=man.title or ref_id,
        source_class=man.source_class,
        tags=list(tags),
        digest_path=digest if digest.is_file() else None,
        dir=ws.references_dir() / ref_id,
    )


def list_all_refs(serve: Path) -> list[RefRecord]:
    catalog = load_catalog(serve)
    assignments: dict[str, list[str]] = catalog.get("assignments") or {}
    out: list[RefRecord] = []
    serve = Path(serve)
    for slug in list_topic_slugs(serve):
        try:
            ws = Workspace.open(serve / slug)
        except WorkspaceNotFound:
            continue
        for ref_id in ws.list_reference_ids():
            rec = _record_from_ws(ws, slug, ref_id, assignments.get(ref_key(slug, ref_id), []))
            if rec is not None:
                out.append(rec)
    gpath = global_home_path(serve)
    if (gpath / ".kairo" / "state.json").is_file():
        gws = Workspace.open(gpath)
        for ref_id in gws.list_reference_ids():
            rec = _record_from_ws(gws, "", ref_id, assignments.get(ref_key("", ref_id), []))
            if rec is not None:
                out.append(rec)
    return out


def _normalize_tag(tag: str) -> str:
    name = (tag or "").strip()
    if not name:
        raise RefError("Tag 不能为空")
    if "/" in name or name.startswith("."):
        raise RefError(f"非法 Tag:{name!r}")
    return name


def add_tag(serve: Path, *, home: str, ref_id: str, tag: str) -> list[str]:
    tag = _normalize_tag(tag)
    recs = {r.key: r for r in list_all_refs(serve)}
    key = ref_key(home, ref_id)
    if key not in recs:
        raise RefError(f"Ref 不存在:{key}")
    catalog = load_catalog(serve)
    if tag not in catalog["tags"]:
        catalog["tags"].append(tag)
    current = list(catalog["assignments"].get(key, []))
    if tag not in current:
        current.append(tag)
    catalog["assignments"][key] = current
    save_catalog(serve, catalog)
    return current


def remove_tag(serve: Path, *, home: str, ref_id: str, tag: str) -> list[str]:
    tag = _normalize_tag(tag)
    key = ref_key(home, ref_id)
    catalog = load_catalog(serve)
    current = [t for t in catalog["assignments"].get(key, []) if t != tag]
    catalog["assignments"][key] = current
    save_catalog(serve, catalog)
    return current


def list_tags(serve: Path) -> list[str]:
    return list(load_catalog(serve).get("tags") or [])


def include_tags_of(ws: Workspace) -> list[str] | None:
    return ws.constitution.include_tags


def set_include_tags(serve: Path, slug: str, tags: list[str] | None) -> list[str] | None:
    ws = open_topic(serve, slug)
    con = ws.constitution
    if tags is None:
        con.include_tags = None
    else:
        cleaned: list[str] = []
        for raw in tags:
            name = _normalize_tag(raw)
            if name not in cleaned:
                cleaned.append(name)
        con.include_tags = cleaned
    ws.write_constitution(con)
    return con.include_tags


def topic_members(serve: Path, slug: str) -> list[RefRecord]:
    ws = open_topic(serve, slug)
    rules = include_tags_of(ws)
    all_refs = list_all_refs(serve)
    if rules is None:
        return [r for r in all_refs if r.home == slug]
    if not rules:
        return []
    wanted = set(rules)
    return [r for r in all_refs if wanted.intersection(r.tags)]


def project_member_refs(serve: Path, slugs: list[str]) -> list[RefRecord]:
    seen: set[str] = set()
    out: list[RefRecord] = []
    for slug in slugs:
        if not slug:
            continue
        try:
            members = topic_members(serve, slug)
        except RefError:
            continue
        for rec in members:
            if rec.key not in seen:
                seen.add(rec.key)
                out.append(rec)
    return out


def resolve_open(serve: Path, home: str, ref_id: str) -> tuple[Workspace, str]:
    if home:
        ws = open_topic(serve, home)
    else:
        path = global_home_path(serve)
        if not (path / ".kairo" / "state.json").is_file():
            raise RefError(f"Ref 不存在:global/{ref_id}")
        ws = Workspace.open(path)
    if ref_id not in ws.list_reference_ids():
        raise RefError(f"Ref 不存在:{ref_key(home, ref_id)}")
    return ws, ref_id


def run_ref_ids(ws: Workspace) -> list[str]:
    """本 Topic 知识 Run 要处理的本地 home Ref。

    include_tags 缺省：全部本地 id。显式 []：无。非空：仅当前成员且 home 为本 Topic。
    """
    local = list(ws.list_reference_ids())
    rules = include_tags_of(ws)
    if rules is None:
        return local
    if not rules:
        return []
    try:
        members = topic_members(ws.root.parent, ws.root.name)
    except RefError:
        return local
    allowed = {m.id for m in members if m.home == ws.root.name}
    return [rid for rid in local if rid in allowed]


def is_global_home(workspace: str) -> bool:
    return not workspace or workspace == "global"


def timeline_digest_path(root: Path, workspace: str, ref_id: str) -> Path:
    if is_global_home(workspace):
        return global_home_path(root) / "references" / ref_id / "digest.md"
    return Path(root) / workspace / "references" / ref_id / "digest.md"


def ref_nav(home: str, ref_id: str) -> dict[str, str | None]:
    if home:
        href = f"/w/{home}?ref={ref_id}"
        return {"href": href, "hx": f"/w/{home}/ref/{ref_id}"}
    return {"href": f"/refs/{ref_id}", "hx": None}


def digest_paths_for(records: list[RefRecord]) -> list[str]:
    out: list[str] = []
    for rec in records:
        if rec.digest_path is not None:
            out.append(str(rec.digest_path.resolve()))
    return out
