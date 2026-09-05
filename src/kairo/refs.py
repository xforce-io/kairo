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
MIGRATION_JOURNAL_REL = Path(".kairo") / "tag-rule-migration.json"


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


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        temp.write_text(content, encoding="utf-8")
        os.replace(temp, path)
    except Exception as exc:
        temp.unlink(missing_ok=True)
        raise RefError(f"保存失败:{exc}") from exc


def catalog_path(serve: Path) -> Path:
    return Path(serve) / CATALOG_REL


def migration_journal_path(serve: Path) -> Path:
    return Path(serve) / MIGRATION_JOURNAL_REL


def recover_tag_rule_migration(serve: Path) -> None:
    """恢复被中断的 Tag 规则迁移，或清理已提交但未清理的 journal。"""
    serve = Path(serve)
    journal_path = migration_journal_path(serve)
    if not journal_path.is_file():
        return
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RefError("Tag 规则迁移 journal 不可读") from exc
    if journal.get("phase") == "committed":
        journal_path.unlink()
        return
    files = journal.get("files")
    if not isinstance(files, dict):
        raise RefError("Tag 规则迁移 journal 不完整")
    for rel, content in files.items():
        path = (serve / str(rel)).resolve()
        if path != serve.resolve() and serve.resolve() not in path.parents:
            raise RefError("Tag 规则迁移 journal 路径非法")
        if not isinstance(content, str):
            raise RefError("Tag 规则迁移 journal 内容非法")
        _atomic_text(path, content)
    catalog_before = journal.get("catalog_before")
    if catalog_before is None:
        catalog_path(serve).unlink(missing_ok=True)
    elif isinstance(catalog_before, str):
        _atomic_text(catalog_path(serve), catalog_before)
    else:
        raise RefError("Tag 规则迁移 catalog 快照非法")
    journal_path.unlink()


def load_catalog(serve: Path) -> dict[str, Any]:
    recover_tag_rule_migration(serve)
    path = catalog_path(serve)
    if not path.is_file():
        return {"tags": [], "assignments": {}, "strict_membership": False}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RefError(f"无法解析 {path}: {exc}") from exc
    tags = [str(t) for t in raw.get("tags") or [] if str(t).strip()]
    assignments = {
        str(k): [str(t) for t in (v or []) if str(t).strip()]
        for k, v in (raw.get("assignments") or {}).items()
    }
    return {
        "tags": tags,
        "assignments": assignments,
        "strict_membership": bool(raw.get("strict_membership", False)),
    }


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
    _atomic_json(
        catalog_path(serve),
        {
            "tags": tags,
            "assignments": assignments,
            "strict_membership": bool(catalog.get("strict_membership", False)),
        },
    )


def global_home_path(serve: Path) -> Path:
    return Path(serve) / GLOBAL_HOME_REL


def serve_root_of(ws: Workspace) -> Path:
    """Topic 目录的父级，或全局库 `.kairo/global-home` 再上两级。"""
    root = Path(ws.root).resolve()
    if root.name == "global-home" and root.parent.name == ".kairo":
        return root.parent.parent
    return root.parent


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


def create_tag(serve: Path, tag: str) -> str:
    """在全局词表中创建 Tag；Ref/Topic 只能引用已存在项。"""
    name = _normalize_tag(tag)
    catalog = load_catalog(serve)
    if name in catalog["tags"]:
        raise RefError(f"Tag 已存在:{name}")
    catalog["tags"].append(name)
    save_catalog(serve, catalog)
    return name


def tag_usages(serve: Path, tag: str) -> dict[str, Any]:
    """返回删除判定所需的引用计数；不暴露 Ref 正文。"""
    name = _normalize_tag(tag)
    catalog = load_catalog(serve)
    ref_count = sum(name in vals for vals in (catalog.get("assignments") or {}).values())
    rule_count = 0
    protected_by: list[str] = []
    for slug in list_topic_slugs(serve):
        ws = open_topic(serve, slug)
        if name in (ws.constitution.include_tags or []):
            rule_count += 1
        if ws.constitution.topic == name:
            protected_by.append(slug)
    return {
        "name": name,
        "ref_count": ref_count,
        "rule_count": rule_count,
        "protected_by": protected_by,
    }


def list_tag_records(serve: Path) -> list[dict[str, Any]]:
    return [tag_usages(serve, tag) for tag in list_tags(serve)]


def delete_tag(serve: Path, tag: str) -> None:
    name = _normalize_tag(tag)
    catalog = load_catalog(serve)
    if name not in catalog["tags"]:
        raise RefError(f"Tag 不存在:{name}")
    usage = tag_usages(serve, name)
    if usage["ref_count"] or usage["rule_count"] or usage["protected_by"]:
        raise RefError(
            "Tag 仍被引用:"
            f"Ref {usage['ref_count']}、Topic 规则 {usage['rule_count']}、"
            f"Topic 名称 {len(usage['protected_by'])}"
        )
    catalog["tags"] = [item for item in catalog["tags"] if item != name]
    save_catalog(serve, catalog)


def _load_tag_migration_evidence(evidence_path: Path | str) -> dict[str, Any]:
    """校验 Tag 数据迁移共用的已验证恢复证据。"""
    evidence = Path(evidence_path)
    try:
        proof = json.loads(evidence.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RefError("迁移前备份证据不可读") from exc
    required = (
        "remote",
        "snapshot_path",
        "backup_id",
        "created_at",
        "manifest_sha256",
        "verified_at",
        "restored",
        "restored_root",
    )
    if (
        any(not proof.get(key) for key in required)
        or proof.get("remote") != "jms-115"
        or proof.get("restored") is not True
    ):
        raise RefError("迁移前备份证据不完整")
    return proof


def migrate_tag_rules(
    serve: Path, evidence_path: Path | str, *, dry_run: bool = False
) -> dict[str, Any]:
    """将历史 home 成员语义切换为严格 Tag 规则。

    证据由运维流程产生；本函数只校验其恢复已验证的最小契约，不访问 remote。
    """
    serve = Path(serve)
    recover_tag_rule_migration(serve)
    proof = _load_tag_migration_evidence(evidence_path)

    catalog = load_catalog(serve)
    raw_tags = [str(tag).strip() for tag in catalog.get("tags") or [] if str(tag).strip()]
    if len(raw_tags) != len(set(raw_tags)):
        raise RefError("迁移前 Tag 词表存在规范化冲突")
    tags = set(catalog["tags"])
    for values in (catalog.get("assignments") or {}).values():
        tags.update(_normalize_tag(value) for value in values)
    topics: list[Workspace] = []
    legacy_none: list[str] = []
    for slug in list_topic_slugs(serve):
        ws = open_topic(serve, slug)
        topics.append(ws)
        tags.add(_normalize_tag(ws.constitution.topic))
        if ws.constitution.include_tags is None:
            legacy_none.append(slug)
        else:
            tags.update(_normalize_tag(value) for value in ws.constitution.include_tags)
    names = [ws.constitution.topic.strip() for ws in topics]
    if len(names) != len(set(names)):
        raise RefError("迁移前 Topic 名称存在规范化冲突")
    known_ref_keys = {rec.key for rec in list_all_refs(serve)}
    unknown_assignments = sorted(
        key for key in (catalog.get("assignments") or {}) if key not in known_ref_keys
    )
    if unknown_assignments:
        raise RefError("迁移前存在无法解析的 Ref Tag 引用")
    report = {
        "ok": True,
        "dry_run": dry_run,
        "topics": [ws.root.name for ws in topics],
        "legacy_home_topics": legacy_none,
        "tags": sorted(tags),
        "ledger_conversion": True,
        "backup_id": proof["backup_id"],
    }
    if dry_run:
        return report

    # 先把将被替换的所有文件写入 journal，再写 live root。进程被杀时，
    # 下次任一 catalog 读取会先恢复这个快照；成功则 journal 标记 committed。
    files = {
        str(path.relative_to(serve)): path.read_text(encoding="utf-8")
        for ws in topics
        for path in (ws.root / "constitution.yaml", ws.state_path)
    }
    catalog_file = catalog_path(serve)
    journal_path = migration_journal_path(serve)
    journal = {
        "phase": "prepared",
        "files": files,
        "catalog_before": (
            catalog_file.read_text(encoding="utf-8") if catalog_file.is_file() else None
        ),
    }
    _atomic_json(journal_path, journal)
    try:
        for ws in topics:
            state = ws.read_state()
            if ws.constitution.include_tags is None:
                ws.constitution.include_tags = []
                ws.write_constitution(ws.constitution)
            # 旧账本只能指向本 Topic 自己的 digest；把它们转换成稳定的
            # Ref 身份键，不复制 digest，也不改 target 正文。
            local_ids = set(ws.list_reference_ids())
            changed = False
            for target in state.targets.values():
                for attr in ("folded", "last_major_folded"):
                    ledger = getattr(target, attr)
                    converted: dict[str, str] = {}
                    for key, digest_hash in ledger.items():
                        prefix, suffix = "references/", "/digest.md"
                        if key.startswith(prefix) and key.endswith(suffix):
                            ref_id = key[len(prefix) : -len(suffix)]
                            if ref_id in local_ids and "/" not in ref_id:
                                key = ref_key(ws.root.name, ref_id)
                                changed = True
                        converted[key] = digest_hash
                    setattr(target, attr, converted)
            if changed:
                ws.write_state(state)
        catalog["tags"] = sorted(tags)
        catalog["strict_membership"] = True
        save_catalog(serve, catalog)
        journal["phase"] = "committed"
        _atomic_json(journal_path, journal)
        journal_path.unlink()
    except Exception as exc:
        try:
            recover_tag_rule_migration(serve)
        except Exception as recovery_exc:
            raise RefError(f"迁移失败，且恢复失败:{recovery_exc}") from exc
        raise RefError(f"迁移失败:{exc}") from exc
    return report


def migrate_home_membership(
    serve: Path, evidence_path: Path | str, *, dry_run: bool = False
) -> dict[str, Any]:
    """把历史 Topic home 明确回填为同名 Tag 规则和 Ref Tag。"""
    serve = Path(serve)
    recover_tag_rule_migration(serve)
    proof = _load_tag_migration_evidence(evidence_path)
    catalog = load_catalog(serve)
    if not catalog.get("strict_membership", False):
        raise RefError("请先完成 Tag 严格成员迁移")

    topics = [open_topic(serve, slug) for slug in list_topic_slugs(serve)]
    topic_tags: dict[str, str] = {}
    for ws in topics:
        tag = _normalize_tag(ws.constitution.topic)
        if tag in topic_tags.values():
            raise RefError("Topic 名称存在规范化冲突")
        if tag not in catalog["tags"]:
            raise RefError(f"Topic 名称 Tag 不在词表中:{tag}")
        existing = ws.constitution.include_tags or []
        if existing and existing != [tag]:
            raise RefError(f"Topic 已有不同包含规则:{ws.root.name}")
        topic_tags[ws.root.name] = tag

    refs = list_all_refs(serve)
    known_ref_keys = {rec.key for rec in refs}
    unreadable_refs = sorted(
        ref_key(ws.root.name, ref_id)
        for ws in topics
        for ref_id in ws.list_reference_ids()
        if ref_key(ws.root.name, ref_id) not in known_ref_keys
    )
    if unreadable_refs:
        raise RefError("存在无法解析的历史 home Ref")
    unknown_assignments = sorted(
        key for key in (catalog.get("assignments") or {}) if key not in known_ref_keys
    )
    if unknown_assignments:
        raise RefError("存在无法解析的 Ref Tag 引用")

    assignments = {
        key: list(values) for key, values in (catalog.get("assignments") or {}).items()
    }
    rule_updates = 0
    tag_additions = 0
    for ws in topics:
        tag = topic_tags[ws.root.name]
        if ws.constitution.include_tags != [tag]:
            rule_updates += 1
    for rec in refs:
        if rec.home not in topic_tags:
            continue
        values = assignments.setdefault(rec.key, [])
        tag = topic_tags[rec.home]
        if tag not in values:
            values.append(tag)
            tag_additions += 1

    report = {
        "ok": True,
        "dry_run": dry_run,
        "backup_id": proof["backup_id"],
        "topics": len(topics),
        "home_refs": sum(1 for rec in refs if rec.home in topic_tags),
        "rule_updates": rule_updates,
        "tag_additions": tag_additions,
        "changed": bool(rule_updates or tag_additions),
    }
    if dry_run or not report["changed"]:
        return report

    files = {
        str(ws.root.joinpath("constitution.yaml").relative_to(serve)): (
            ws.root / "constitution.yaml"
        ).read_text(encoding="utf-8")
        for ws in topics
    }
    catalog_file = catalog_path(serve)
    journal_path = migration_journal_path(serve)
    journal = {
        "phase": "prepared",
        "files": files,
        "catalog_before": (
            catalog_file.read_text(encoding="utf-8") if catalog_file.is_file() else None
        ),
    }
    _atomic_json(journal_path, journal)
    try:
        for ws in topics:
            constitution = ws.constitution
            constitution.include_tags = [topic_tags[ws.root.name]]
            ws.write_constitution(constitution)
        catalog["assignments"] = assignments
        save_catalog(serve, catalog)
        journal["phase"] = "committed"
        _atomic_json(journal_path, journal)
        journal_path.unlink()
    except Exception as exc:
        try:
            recover_tag_rule_migration(serve)
        except Exception as recovery_exc:
            raise RefError(f"历史归属回填失败，且恢复失败:{recovery_exc}") from exc
        raise RefError(f"历史归属回填失败:{exc}") from exc
    return report


def add_tag(serve: Path, *, home: str, ref_id: str, tag: str) -> list[str]:
    tag = _normalize_tag(tag)
    recs = {r.key: r for r in list_all_refs(serve)}
    key = ref_key(home, ref_id)
    if key not in recs:
        raise RefError(f"Ref 不存在:{key}")
    catalog = load_catalog(serve)
    if tag not in catalog["tags"]:
        raise RefError(f"Tag 不在词表中:{tag}")
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


def topic_name_tag(ws: Workspace) -> str:
    return _normalize_tag(ws.constitution.topic)


def ref_tags(serve: Path, home: str, ref_id: str) -> list[str]:
    catalog = load_catalog(serve)
    return list((catalog.get("assignments") or {}).get(ref_key(home, ref_id), []))


def related_topics_for_ref(serve: Path, home: str, ref_id: str) -> list[dict[str, str]]:
    """Topics whose stored include rules intersect this Ref's Tags.

    Reads catalog assignments and each Topic constitution only. Does not
    enumerate other Refs or compute Topic member lists.
    """
    tags = set(ref_tags(serve, home, ref_id))
    if not tags:
        return []
    out: list[dict[str, str]] = []
    for slug in list_topic_slugs(serve):
        try:
            ws = open_topic(serve, slug)
        except RefError:
            continue
        con = ws.constitution
        rules = set(con.include_tags or [])
        if tags.intersection(rules):
            out.append({"slug": slug, "title": con.topic})
    return out


def set_include_tags(serve: Path, slug: str, tags: list[str] | None) -> list[str]:
    ws = open_topic(serve, slug)
    con = ws.constitution
    catalog = load_catalog(serve)
    name = topic_name_tag(ws)
    cleaned: list[str] = [name]
    for raw in tags or []:
        extra = _normalize_tag(raw)
        if extra == name:
            continue
        if extra not in catalog["tags"]:
            raise RefError(f"Tag 不在词表中:{extra}")
        if extra not in cleaned:
            cleaned.append(extra)
    con.include_tags = cleaned
    ws.write_constitution(con)
    return con.include_tags


def topic_members(serve: Path, slug: str) -> list[RefRecord]:
    ws = open_topic(serve, slug)
    rules = include_tags_of(ws)
    all_refs = list_all_refs(serve)
    if rules is None and not load_catalog(serve).get("strict_membership", False):
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


def run_members(ws: Workspace) -> list[RefRecord]:
    """本 Topic 知识 Run 的成员（含跨 home）。home 只定位源与唯一 digest。"""
    serve = ws.root.parent
    if ws.root.name.startswith(".") or not is_topic_dir(ws.root):
        return []
    try:
        return topic_members(serve, ws.root.name)
    except RefError:
        return []


def member_sources(ws: Workspace) -> list[tuple[Workspace, str, RefRecord]]:
    """解析成员的 home workspace；打不开的来源跳过，不回退为目录内文件。"""
    serve = ws.root.parent
    out: list[tuple[Workspace, str, RefRecord]] = []
    for rec in run_members(ws):
        try:
            source_ws, ref_id = resolve_open(serve, rec.home, rec.id)
        except RefError:
            continue
        out.append((source_ws, ref_id, rec))
    return out


def run_ref_ids(ws: Workspace) -> list[str]:
    """本 Topic 目录内、且已是成员的 Ref id（不含跨 home）。"""
    local = list(ws.list_reference_ids())
    members = run_members(ws)
    if not members and include_tags_of(ws) is None and not load_catalog(
        ws.root.parent
    ).get("strict_membership", False):
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
