"""完整 serve root 备份:恢复闭包、备份清单、current 原子切换(#154)。"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from kairo.corpus import CORPUS_TREE_ROLE
from kairo.machine import config_path
from kairo.workspace import Workspace, WorkspaceNotFound

SCHEMA_VERSION = 1
BACKUP_ID_RE = re.compile(r"^b-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$")
REMOTE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SSH_RE = re.compile(r"^[A-Za-z0-9._@:/-]+$")
EXT_PREFIX = ".kairo/backup-external"


class BackupError(Exception):
    """备份失败。code=2 前置错误,1 采集/传输/校验失败。"""

    def __init__(self, stage: str, message: str, *, code: int = 1) -> None:
        super().__init__(message)
        self.stage = stage
        self.message = message
        self.code = code


@dataclass(frozen=True)
class RemoteSpec:
    name: str
    ssh: str
    path: str


@dataclass(frozen=True)
class PublishResult:
    status: str  # pushed | unchanged
    backup_id: str
    files: int
    bytes: int


def load_remote(name: str) -> RemoteSpec:
    if not REMOTE_NAME_RE.fullmatch(name):
        raise BackupError("config", f"非法 remote 名:{name}", code=2)
    path = config_path()
    if not path.is_file():
        raise BackupError("config", "未找到 machine config", code=2)
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise BackupError("config", "machine config 不可读", code=2) from exc
    section = (data.get("remote") or {}).get(name)
    if not isinstance(section, dict):
        raise BackupError("config", f"未配置 remote:{name}", code=2)
    ssh = section.get("ssh")
    dest = section.get("path")
    if not isinstance(ssh, str) or not ssh or ssh.startswith("-") or not SSH_RE.fullmatch(ssh):
        raise BackupError("config", "非法 ssh", code=2)
    if not isinstance(dest, str) or not dest.startswith("/") or any(
        ch in dest for ch in ("\0", "\r", "\n")
    ):
        raise BackupError("config", "非法 path", code=2)
    return RemoteSpec(name=name, ssh=ssh, path=dest)


def validate_backup_id(backup_id: str) -> str:
    if not BACKUP_ID_RE.fullmatch(backup_id):
        raise BackupError("config", "非法 backup_id", code=2)
    return backup_id


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _posix(rel: Path) -> str:
    return rel.as_posix()


def _is_safe_rel(raw: str) -> bool:
    if not raw or raw.startswith("/") or raw.startswith("~"):
        return False
    if any(ch in raw for ch in ("\\", "\0", "\r", "\n")):
        return False
    parts = raw.split("/")
    return bool(parts) and not any(p in {"", ".", ".."} for p in parts)


def _ordinary_file(path: Path) -> bool:
    try:
        st = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode)


def _ordinary_dir(path: Path) -> bool:
    try:
        st = path.lstat()
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode)


def walk_ordinary(root: Path) -> tuple[list[str], list[str]]:
    """列出 root 下普通目录/文件相对 POSIX 路径。符号链接或特殊文件失败。"""
    if not _ordinary_dir(root):
        raise BackupError("scan", "根不是普通目录")
    dirs: list[str] = []
    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dp = Path(dirpath)
        if dp != root and not _ordinary_dir(dp):
            raise BackupError("scan", "含符号链接或特殊目录")
        rel_dir = dp.relative_to(root)
        if rel_dir != Path("."):
            dirs.append(_posix(rel_dir))
        for name in dirnames:
            child = dp / name
            if not _ordinary_dir(child):
                raise BackupError("scan", "含符号链接或特殊目录")
        for name in filenames:
            child = dp / name
            if not _ordinary_file(child):
                raise BackupError("scan", "含符号链接或特殊文件")
            files.append(_posix(child.relative_to(root)))
    dirs.sort()
    files.sort()
    return dirs, files


def _ref_dir_token(ref_id: str) -> str:
    return "r-" + _sha256_bytes(ref_id.encode())


def _payload_rel(ws: str, ref_id: str, index: int, kind: str, basename: str) -> str:
    base = f"{ws}/{EXT_PREFIX}/{_ref_dir_token(ref_id)}/{index}/payload"
    if kind == "directory":
        return base
    return f"{base}/{basename}"


def _form_kind(role: str) -> str:
    return "directory" if role == CORPUS_TREE_ROLE else "file"


def _parse_location(ws_root: Path, location: str) -> Path:
    loc = Path(location)
    return loc if loc.is_absolute() else ws_root / loc


def _inside_workspace(ws_root: Path, location: str) -> bool:
    if not location or any(ch in location for ch in ("\0", "\r", "\n")):
        return False
    loc = Path(location)
    if loc.is_absolute() or any(p in {".", ".."} for p in loc.parts):
        return False
    _ = ws_root
    return True


def _workspace_slugs(serve_root: Path) -> list[str]:
    slugs: list[str] = []
    if not serve_root.is_dir():
        return slugs
    for child in sorted(serve_root.iterdir()):
        if not _ordinary_dir(child):
            if child.is_symlink():
                raise BackupError("scan", "含符号链接或特殊目录")
            continue
        if (child / "constitution.yaml").is_file() or (
            child / ".kairo" / "state.json"
        ).is_file():
            slugs.append(child.name)
    return slugs


def _load_raw_manifest(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise BackupError("scan", "manifest 非法")
    return data


def _materialize_plan(serve_root: Path) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for slug in _workspace_slugs(serve_root):
        ws_root = serve_root / slug
        refs = ws_root / "references"
        if not refs.is_dir():
            continue
        for ref_dir in sorted(p for p in refs.iterdir() if p.is_dir()):
            man_path = ref_dir / "manifest.yaml"
            if not man_path.is_file():
                continue
            man = _load_raw_manifest(man_path)
            forms = man.get("forms") or []
            if not isinstance(forms, list):
                raise BackupError("scan", "manifest forms 非法")
            for idx, form in enumerate(forms):
                if not isinstance(form, dict):
                    raise BackupError("scan", "form 非法")
                loc = form.get("location")
                role = form.get("role") or ""
                if not isinstance(loc, str):
                    raise BackupError("scan", "form location 非法")
                kind = _form_kind(str(role))
                if _inside_workspace(ws_root, loc):
                    continue
                src = _parse_location(ws_root, loc)
                if kind == "directory":
                    if not _ordinary_dir(src):
                        raise BackupError("scan", f"路径指针缺失 {slug}/{ref_dir.name}/{idx}")
                elif not _ordinary_file(src):
                    raise BackupError("scan", f"路径指针缺失 {slug}/{ref_dir.name}/{idx}")
                basename = src.name
                if any(ch in basename for ch in ("/", "\\", "\0", "\r", "\n")):
                    raise BackupError("scan", "非法 basename")
                dest = _payload_rel(slug, ref_dir.name, idx, kind, basename)
                plan.append(
                    {
                        "workspace": slug,
                        "ref_id": ref_dir.name,
                        "form_index": idx,
                        "kind": kind,
                        "path": dest,
                        "source": str(src),
                        "manifest": str(man_path.relative_to(serve_root)),
                    }
                )
    return plan


def _copy_tree(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    dirs, files = walk_ordinary(src)
    for rel in dirs:
        (dest / rel).mkdir(parents=True, exist_ok=True)
    for rel in files:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src / rel, target, follow_symlinks=False)


def _logical_fingerprint(
    serve_root: Path, plan: list[dict[str, Any]], *, payload_root: Path | None = None
) -> str:
    token_by_manifest: dict[str, dict[int, str]] = {}
    material_keys: set[str] = set()
    for item in plan:
        man = item["manifest"]
        token = (
            f"{item['workspace']}/{item['ref_id']}/{item['form_index']}/{item['kind']}"
        )
        token_by_manifest.setdefault(man, {})[item["form_index"]] = token
        src = (
            payload_root / item["path"]
            if payload_root is not None
            else Path(item["source"])
        )
        if item["kind"] == "file":
            material_keys.add(f"X:{token}:{_sha256_file(src)}")
        else:
            d, f = walk_ordinary(src)
            parts = [f"D:{x}" for x in d] + [
                f"F:{x}:{_sha256_file(src / x)}" for x in f
            ]
            material_keys.add(f"X:{token}:" + "|".join(parts))
    dirs, files = walk_ordinary(serve_root)
    entries: list[str] = []
    for rel in dirs:
        if "/.kairo/backup-external" in f"/{rel}":
            continue
        entries.append(f"D:{rel}")
    for rel in files:
        if "/.kairo/backup-external/" in f"/{rel}":
            continue
        if rel in token_by_manifest:
            raw = _load_raw_manifest(serve_root / rel)
            forms = raw.get("forms") or []
            for idx, tok in token_by_manifest[rel].items():
                if not isinstance(forms, list) or idx >= len(forms):
                    raise BackupError("scan", "manifest form 丢失")
                forms[idx]["location"] = tok
            entries.append(
                "M:"
                + rel
                + ":"
                + json.dumps(raw, sort_keys=True, ensure_ascii=False, default=str)
            )
        else:
            entries.append(f"F:{rel}:{_sha256_file(serve_root / rel)}")
    entries.extend(sorted(material_keys))
    entries.sort()
    return _sha256_bytes("\n".join(entries).encode())


def _listing_from_data(data_root: Path, materialized: list[dict[str, Any]]) -> dict:
    dirs, files = walk_ordinary(data_root)
    file_recs = [
        {
            "path": rel,
            "size": (data_root / rel).stat().st_size,
            "sha256": _sha256_file(data_root / rel),
        }
        for rel in files
    ]
    mats = [
        {
            "workspace": m["workspace"],
            "ref_id": m["ref_id"],
            "form_index": m["form_index"],
            "kind": m["kind"],
            "path": m["path"],
        }
        for m in materialized
    ]
    mats.sort(key=lambda x: (x["workspace"], x["ref_id"], x["form_index"]))
    body = {
        "directories": dirs,
        "files": file_recs,
        "materialized": mats,
    }
    digest = _sha256_bytes(
        json.dumps(body, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    )
    return {**body, "content_sha256": digest}


def _validate_rel_list(paths: list[str], *, kind: str) -> None:
    seen: set[str] = set()
    for raw in paths:
        if not _is_safe_rel(raw) or raw in seen:
            raise BackupError("verify", f"非法{kind}路径")
        seen.add(raw)


def validate_listing(gen_dir: Path) -> dict:
    man_path = gen_dir / "backup.json"
    data_root = gen_dir / "data"
    if not man_path.is_file() or not data_root.is_dir():
        raise BackupError("verify", "generation 不完整")
    try:
        listing = json.loads(man_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError("verify", "备份清单损坏") from exc
    if listing.get("schema_version") != SCHEMA_VERSION:
        raise BackupError("verify", "未知备份清单版本")
    backup_id = listing.get("backup_id")
    if not isinstance(backup_id, str) or not BACKUP_ID_RE.fullmatch(backup_id):
        raise BackupError("verify", "非法 backup_id")
    if gen_dir.name != backup_id:
        raise BackupError("verify", "backup_id 与目录名不一致")
    dirs = listing.get("directories")
    files = listing.get("files")
    mats = listing.get("materialized")
    if not isinstance(dirs, list) or not isinstance(files, list) or not isinstance(mats, list):
        raise BackupError("verify", "备份清单字段非法")
    _validate_rel_list(dirs, kind="目录")
    file_paths = []
    for rec in files:
        if not isinstance(rec, dict):
            raise BackupError("verify", "文件记录非法")
        p = rec.get("path")
        if not isinstance(p, str):
            raise BackupError("verify", "文件路径非法")
        file_paths.append(p)
        live = data_root / p
        if not _ordinary_file(live):
            raise BackupError("verify", "缺失文件")
        if rec.get("sha256") != _sha256_file(live):
            raise BackupError("verify", "文件 hash 漂移")
        if live.stat().st_size != rec.get("size"):
            raise BackupError("verify", "文件大小漂移")
    _validate_rel_list(file_paths, kind="文件")
    live_dirs, live_files = walk_ordinary(data_root)
    if live_dirs != sorted(dirs) or live_files != sorted(file_paths):
        raise BackupError("verify", "备份清单与磁盘不一致")
    for m in mats:
        if not isinstance(m, dict):
            raise BackupError("verify", "物化记录非法")
        kind = m.get("kind")
        rel = m.get("path")
        if kind not in {"file", "directory"} or not isinstance(rel, str) or not _is_safe_rel(rel):
            raise BackupError("verify", "物化路径非法")
        target = data_root / rel
        if kind == "file" and not _ordinary_file(target):
            raise BackupError("verify", "物化文件缺失")
        if kind == "directory" and not _ordinary_dir(target):
            raise BackupError("verify", "物化目录缺失")
    expect = _listing_from_data(data_root, mats)
    if expect["content_sha256"] != listing.get("content_sha256"):
        raise BackupError("verify", "content_sha256 不匹配")
    if not backup_id.endswith(expect["content_sha256"][:12]):
        raise BackupError("verify", "backup_id 摘要不匹配")
    return listing


def _covered_file(listing: dict, rel: str) -> bool:
    return any(rec["path"] == rel for rec in listing["files"])


def _covered_dir(listing: dict, rel: str) -> bool:
    return rel in listing["directories"] or rel == ""


def validate_semantics(data_root: Path, listing: dict) -> None:
    for slug in _workspace_slugs(data_root):
        ws_root = data_root / slug
        if not (ws_root / "constitution.yaml").is_file() or not (
            ws_root / ".kairo" / "state.json"
        ).is_file():
            raise BackupError("verify", f"workspace 不完整:{slug}")
        try:
            ws = Workspace.open(ws_root)
        except WorkspaceNotFound as exc:
            raise BackupError("verify", f"workspace 无法打开:{slug}") from exc
        refs = ws_root / "references"
        if not refs.exists():
            continue
        if not _ordinary_dir(refs):
            raise BackupError("verify", "references 非法")
        for ref_id in ws.list_reference_ids():
            man_path = refs / ref_id / "manifest.yaml"
            if not man_path.is_file():
                raise BackupError("verify", "manifest 缺失")
            man = _load_raw_manifest(man_path)
            forms = man.get("forms") or []
            if not isinstance(forms, list):
                raise BackupError("verify", "forms 非法")
            for idx, form in enumerate(forms):
                loc = form.get("location")
                role = str(form.get("role") or "")
                if not isinstance(loc, str) or not _inside_workspace(ws_root, loc):
                    raise BackupError("verify", f"form 逃逸 {slug}/{ref_id}/{idx}")
                kind = _form_kind(role)
                obj = ws_root / loc
                rel = _posix((ws_root / loc).relative_to(data_root))
                if kind == "file":
                    if not _ordinary_file(obj) or not _covered_file(listing, rel):
                        raise BackupError("verify", f"form 文件不可恢复 {slug}/{ref_id}/{idx}")
                else:
                    if not _ordinary_dir(obj) or not _covered_dir(listing, rel):
                        raise BackupError("verify", f"form 目录不可恢复 {slug}/{ref_id}/{idx}")
                    d, f = walk_ordinary(obj)
                    for sub in d:
                        child = rel + "/" + sub
                        if not _covered_dir(listing, child):
                            raise BackupError("verify", "目录未覆盖")
                    for sub in f:
                        child = rel + "/" + sub
                        if not _covered_file(listing, child):
                            raise BackupError("verify", "目录文件未覆盖")


def validate_generation(gen_dir: Path) -> dict:
    listing = validate_listing(gen_dir)
    validate_semantics(gen_dir / "data", listing)
    return listing


def build_candidate(serve_root: Path, dest: Path) -> dict:
    serve_root = Path(serve_root)
    if not _ordinary_dir(serve_root):
        raise BackupError("scan", "serve root 不是目录")
    plan1 = _materialize_plan(serve_root)
    fp1 = _logical_fingerprint(serve_root, plan1)
    data_root = dest / "data"
    _copy_tree(serve_root, data_root)
    for item in plan1:
        src = Path(item["source"])
        payload = data_root / item["path"]
        payload.parent.mkdir(parents=True, exist_ok=True)
        if item["kind"] == "file":
            shutil.copyfile(src, payload, follow_symlinks=False)
        else:
            _copy_tree(src, payload)
        man_path = data_root / item["manifest"]
        raw = _load_raw_manifest(man_path)
        raw["forms"][item["form_index"]]["location"] = str(
            Path(item["path"]).relative_to(item["workspace"])
        )
        man_path.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    plan2 = _materialize_plan(serve_root)
    fp2 = _logical_fingerprint(serve_root, plan2)
    cand_plan = _materialize_plan(data_root)
    # 候选内路径指针应已全部物化
    if cand_plan:
        raise BackupError("scan", "候选仍含路径指针")
    fp_c = _logical_fingerprint(data_root, plan1, payload_root=data_root)
    if not (fp1 == fp2 == fp_c):
        raise BackupError("scan", "源在采集中变化")
    listing = _listing_from_data(data_root, plan1)
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    backup_id = f"b-{stamp}-{listing['content_sha256'][:12]}"
    created = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "backup_id": backup_id,
        "created_at": created,
        **listing,
    }
    (dest / "backup.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def _read_current_id(remote_root: Path) -> str | None:
    link = remote_root / "current"
    if not link.exists() and not link.is_symlink():
        return None
    if not link.is_symlink():
        raise BackupError("verify", "current 不是符号链接")
    target = os.readlink(link)
    if not target.startswith("generations/") or "/" in target[len("generations/") :]:
        raise BackupError("verify", "current 目标非法")
    backup_id = target.split("/", 1)[1]
    validate_backup_id(backup_id)
    if Path(target).as_posix() != f"generations/{backup_id}":
        raise BackupError("verify", "current 目标非法")
    return backup_id


def _atomic_current(remote_root: Path, backup_id: str) -> None:
    tmp = remote_root / ".current.tmp"
    if tmp.exists() or tmp.is_symlink():
        tmp.unlink()
    tmp.symlink_to(f"generations/{backup_id}")
    os.replace(tmp, remote_root / "current")


def commit_generation(
    remote_root: Path, incoming: Path, *, observed: str | None
) -> str:
    """把已校验 incoming 目录提交为 generation 并 CAS current。"""
    remote_root = Path(remote_root)
    incoming = Path(incoming)
    listing = validate_generation(incoming)
    backup_id = listing["backup_id"]
    gens = remote_root / "generations"
    gens.mkdir(parents=True, exist_ok=True)
    final = gens / backup_id
    current = _read_current_id(remote_root) if (remote_root / "current").exists() or (
        remote_root / "current"
    ).is_symlink() else None
    if current != observed:
        raise BackupError("submit", "current 已变化")
    if final.exists():
        existing = validate_generation(final)
        if existing["content_sha256"] != listing["content_sha256"]:
            raise BackupError("submit", "backup_id 冲突")
        shutil.rmtree(incoming, ignore_errors=True)
        if current != backup_id:
            _atomic_current(remote_root, backup_id)
        return backup_id
    os.rename(incoming, final)
    _atomic_current(remote_root, backup_id)
    return backup_id


def publish(serve_root: Path, remote_root: Path) -> PublishResult:
    """本机等价 remote 根上的完整 push(测试与 SSH 落地后共用)。"""
    serve_root = Path(serve_root).resolve()
    remote_root = Path(remote_root)
    remote_root.mkdir(parents=True, exist_ok=True)
    (remote_root / "generations").mkdir(exist_ok=True)
    (remote_root / ".incoming").mkdir(exist_ok=True)
    lock_path = remote_root / ".commit.lock"
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        return _publish_locked(serve_root, remote_root)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _publish_locked(serve_root: Path, remote_root: Path) -> PublishResult:
    observed = None
    try:
        if (remote_root / "current").exists() or (remote_root / "current").is_symlink():
            observed = _read_current_id(remote_root)
    except BackupError:
        observed = None
        # 非法 current:仍允许首次覆盖? 否,fail-closed 除非不存在
        if (remote_root / "current").exists() or (remote_root / "current").is_symlink():
            raise
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw) / "cand"
        tmp.mkdir()
        payload = build_candidate(serve_root, tmp)
        backup_id = payload["backup_id"]
        named = Path(raw) / backup_id
        tmp.rename(named)
        if observed:
            cur_gen = remote_root / "generations" / observed
            if cur_gen.is_dir():
                cur_list = json.loads((cur_gen / "backup.json").read_text())
                if cur_list.get("content_sha256") == payload["content_sha256"]:
                    return PublishResult(
                        "unchanged",
                        observed,
                        len(cur_list.get("files") or []),
                        sum(f.get("size") or 0 for f in cur_list.get("files") or []),
                    )
        incoming = remote_root / ".incoming" / backup_id
        if incoming.exists():
            shutil.rmtree(incoming)
        shutil.copytree(named, incoming, symlinks=False)
        commit_generation(remote_root, incoming, observed=observed)
        nbytes = sum(f["size"] for f in payload["files"])
        return PublishResult("pushed", backup_id, len(payload["files"]), nbytes)


def verify_generation(remote_root: Path, backup_id: str | None = None) -> PublishResult:
    remote_root = Path(remote_root)
    if backup_id is None:
        backup_id = _read_current_id(remote_root)
        if backup_id is None:
            raise BackupError("verify", "没有 current", code=2)
    else:
        validate_backup_id(backup_id)
    gen = remote_root / "generations" / backup_id
    listing = validate_generation(gen)
    nbytes = sum(f["size"] for f in listing["files"])
    return PublishResult("ok", backup_id, len(listing["files"]), nbytes)


def restore_generation(
    remote_root: Path, dest: Path, backup_id: str | None = None
) -> PublishResult:
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise BackupError("restore", "目标不是空目录", code=2)
    result = verify_generation(remote_root, backup_id)
    gen_data = Path(remote_root) / "generations" / result.backup_id / "data"
    parent = dest.parent
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent) as raw:
        tmp = Path(raw) / "data"
        shutil.copytree(gen_data, tmp, symlinks=False)
        listing = validate_generation(
            Path(remote_root) / "generations" / result.backup_id
        )
        validate_semantics(tmp, listing)
        if dest.exists():
            dest.rmdir()
        tmp.replace(dest)
    return PublishResult("restored", result.backup_id, result.files, result.bytes)


RESULT_SCHEMA = 1


def _state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    path = Path(base) / "kairo" / "backup"
    path.mkdir(parents=True, exist_ok=True)
    return path


def result_path(name: str) -> Path:
    if not REMOTE_NAME_RE.fullmatch(name):
        raise BackupError("config", f"非法 remote 名:{name}", code=2)
    return _state_dir() / f"{name}.json"


_RESULT_STATUSES = frozenset({"pushed", "unchanged", "failed", "skipped"})


def read_result(name: str) -> dict | None:
    path = result_path(name)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError("status", "最近结果不可读") from exc
    if not isinstance(data, dict):
        raise BackupError("status", "最近结果不可读")
    if data.get("schema_version") != RESULT_SCHEMA:
        raise BackupError("status", "最近结果版本未知")
    status = data.get("status")
    if status not in _RESULT_STATUSES:
        raise BackupError("status", "最近结果不可读")
    for key in ("last_attempt_at", "last_success_at", "backup_id"):
        if key not in data:
            raise BackupError("status", "最近结果不可读")
    return data


def _write_result(payload: dict, *, unless_newer_than: str | None = None) -> None:
    path = result_path(payload["remote"])
    if unless_newer_than is not None and path.is_file():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            current = None
        if isinstance(current, dict):
            existing = current.get("last_attempt_at") or ""
            if existing > unless_newer_than:
                return
    fd, tmp_name = tempfile.mkstemp(prefix=path.stem + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _prev_fields(name: str) -> tuple[dict | None, str, str]:
    try:
        prev = read_result(name)
    except BackupError:
        prev = None
        path = result_path(name)
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = None
            if isinstance(raw, dict):
                return None, str(raw.get("last_success_at") or ""), str(raw.get("backup_id") or "")
        return None, "", ""
    if prev is None:
        return None, "", ""
    return prev, str(prev.get("last_success_at") or ""), str(prev.get("backup_id") or "")


def push_named(name: str, serve_root: Path) -> PublishResult:
    """带源侧锁与最近结果的 push(#156)。重叠跳过 code=3。"""
    spec = load_remote(name)
    prev, prev_success, prev_id = _prev_fields(name)
    prev_attempt = (prev or {}).get("last_attempt_at") or ""
    lock_fd = os.open(_state_dir() / f"{name}.lock", os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(lock_fd)
        lock_fd = os.open(_state_dir() / f"{name}.lock", os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(lock_fd)
            _write_result(
                {
                    "schema_version": RESULT_SCHEMA,
                    "remote": name,
                    "last_attempt_at": _now(),
                    "last_success_at": prev_success,
                    "backup_id": prev_id,
                    "status": "skipped",
                    "summary": "重叠跳过",
                },
                unless_newer_than=prev_attempt,
            )
            raise BackupError("lock", "重叠跳过", code=3) from exc
    attempt = _now()
    try:
        result = publish(serve_root, Path(spec.path))
        _write_result(
            {
                "schema_version": RESULT_SCHEMA,
                "remote": name,
                "last_attempt_at": attempt,
                "last_success_at": _now(),
                "backup_id": result.backup_id,
                "status": result.status,
                "summary": "",
            }
        )
        return result
    except BackupError as exc:
        _write_result(
            {
                "schema_version": RESULT_SCHEMA,
                "remote": name,
                "last_attempt_at": attempt,
                "last_success_at": prev_success,
                "backup_id": prev_id,
                "status": "failed",
                "summary": f"{exc.stage}: {exc.message}",
            }
        )
        raise
    except Exception as exc:
        _write_result(
            {
                "schema_version": RESULT_SCHEMA,
                "remote": name,
                "last_attempt_at": attempt,
                "last_success_at": prev_success,
                "backup_id": prev_id,
                "status": "failed",
                "summary": type(exc).__name__,
            }
        )
        raise
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
