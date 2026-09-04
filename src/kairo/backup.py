"""完整 serve root 备份:恢复闭包、备份清单、current 原子切换(#154)。"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
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
RESTORE_STAGE_SCHEMA = 1
BACKUP_ID_RE = re.compile(r"^b-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$")
REMOTE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SSH_RE = re.compile(r"^[A-Za-z0-9._@:/-]+$")
EXT_PREFIX = ".kairo/backup-external"
SSH_OPTS = (
    "-T",
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=5",
    "-o",
    "LogLevel=ERROR",
)

_REMOTE_MKDIR = """
import sys
from pathlib import Path
root = Path(sys.argv[1])
root.mkdir(parents=True, exist_ok=True)
(root / "generations").mkdir(exist_ok=True)
(root / ".incoming").mkdir(exist_ok=True)
"""

_REMOTE_READLINK = """
import os
import sys
from pathlib import Path
link = Path(sys.argv[1]) / "current"
if not link.exists() and not link.is_symlink():
    sys.stdout.write("KAIRO:")
    sys.exit(0)
if not link.is_symlink():
    sys.exit(12)
sys.stdout.write("KAIRO:" + os.readlink(link))
"""

_REMOTE_READ_TEXT = """
import sys
from pathlib import Path
path = Path(sys.argv[1])
try:
    sys.stdout.write(path.read_text(encoding="utf-8"))
except OSError:
    sys.exit(15)
"""

_REMOTE_VALIDATE = """
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

def sha256_file(path):
    digest = hashlib.sha256()
    with open(str(path), "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()

def walk_ordinary(root):
    dirs = []
    files = []
    for dirpath, dirnames, filenames in os.walk(str(root), followlinks=False):
        dp = Path(dirpath)
        rel = dp.relative_to(root)
        if rel != Path("."):
            dirs.append(rel.as_posix())
        for name in dirnames:
            st = (dp / name).lstat()
            if not stat.S_ISDIR(st.st_mode):
                sys.exit(15)
        for name in filenames:
            child = dp / name
            st = child.lstat()
            if not stat.S_ISREG(st.st_mode):
                sys.exit(15)
            files.append(child.relative_to(root).as_posix())
    dirs.sort()
    files.sort()
    return dirs, files

gen = Path(sys.argv[1])
man_path = gen / "backup.json"
data = gen / "data"
if not man_path.is_file() or not data.is_dir():
    sys.exit(15)
try:
    listing = json.loads(man_path.read_text())
except Exception:
    sys.exit(15)
if listing.get("backup_id") != gen.name:
    sys.exit(15)
dirs, files = walk_ordinary(data)
if dirs != sorted(listing.get("directories") or []):
    sys.exit(15)
rec_paths = []
for rec in listing.get("files") or []:
    p = rec.get("path")
    rec_paths.append(p)
    live = data / p
    if not live.is_file():
        sys.exit(15)
    if sha256_file(live) != rec.get("sha256"):
        sys.exit(15)
    if live.stat().st_size != rec.get("size"):
        sys.exit(15)
if files != sorted(rec_paths):
    sys.exit(15)
sys.stdout.write("KAIRO:ok")
"""

_REMOTE_COMMIT = """
import fcntl
import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
backup_id = sys.argv[2]
observed = sys.argv[3] or None
(root / "generations").mkdir(parents=True, exist_ok=True)
(root / ".incoming").mkdir(exist_ok=True)
lock_fd = os.open(root / ".commit.lock", os.O_CREAT | os.O_RDWR, 0o644)
fcntl.flock(lock_fd, fcntl.LOCK_EX)
try:
    link = root / "current"
    current = None
    if link.exists() or link.is_symlink():
        if not link.is_symlink():
            sys.exit(12)
        current = os.readlink(link)
    expected = f"generations/{observed}" if observed else None
    if current != expected:
        sys.exit(11)
    incoming = root / ".incoming" / backup_id
    final = root / "generations" / backup_id
    if not incoming.is_dir():
        sys.exit(13)
    if final.exists():
        sys.exit(14)
    os.rename(incoming, final)
    tmp = root / ".current.tmp"
    if tmp.exists() or tmp.is_symlink():
        tmp.unlink()
    tmp.symlink_to(f"generations/{backup_id}")
    os.replace(tmp, root / "current")
finally:
    fcntl.flock(lock_fd, fcntl.LOCK_UN)
    os.close(lock_fd)
"""

_REMOTE_EXIT = {
    11: ("submit", "current 已变化"),
    12: ("verify", "current 不是符号链接"),
    13: ("transfer", "incoming 缺失"),
    14: ("submit", "backup_id 冲突"),
    15: ("verify", "备份清单损坏"),
}


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


def _ssh_python(
    host: str,
    script: str,
    args: list[str],
    *,
    stage: str = "connect",
    timeout: int = 120,
) -> str:
    remote = "python3 -u - " + " ".join(shlex.quote(a) for a in args)
    try:
        proc = subprocess.run(
            ["ssh", *SSH_OPTS, host, remote],
            input=script,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BackupError(stage, "SSH 连接失败") from exc
    if proc.returncode != 0:
        mapped = _REMOTE_EXIT.get(proc.returncode)
        if mapped:
            raise BackupError(mapped[0], mapped[1])
        raise BackupError(stage, "SSH 连接失败")
    return proc.stdout


def _ssh_payload(raw: str) -> str:
    marker = "KAIRO:"
    idx = raw.rfind(marker)
    if idx >= 0:
        return raw[idx + len(marker) :]
    return raw


def _rsync_bin() -> str | None:
    seen: list[str] = []
    which = shutil.which("rsync")
    if which:
        seen.append(which)
    for extra in ("/opt/homebrew/bin/rsync", "/usr/local/bin/rsync"):
        if extra not in seen:
            seen.append(extra)
    for cand in seen:
        if not os.path.isfile(cand) or not os.access(cand, os.X_OK):
            continue
        try:
            proc = subprocess.run(
                [cand, "--version"], capture_output=True, text=True, timeout=5
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        text = (proc.stdout or "") + (proc.stderr or "")
        if "openrsync" in text.lower():
            continue
        return cand
    return None


def _tar_ssh(local: Path, host: str, remote_path: str, *, reverse: bool = False) -> None:
    local.mkdir(parents=True, exist_ok=True)
    dest = shlex.quote(remote_path)
    env = os.environ.copy()
    env["COPYFILE_DISABLE"] = "1"
    if reverse:
        remote = f"tar -C {dest} -cf - ."
        ssh = subprocess.Popen(
            ["ssh", *SSH_OPTS, host, remote],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        tar = subprocess.run(
            ["tar", "-C", str(local), "-xf", "-"],
            stdin=ssh.stdout,
            capture_output=True,
            env=env,
            timeout=3600,
        )
        ssh.communicate(timeout=30)
        if tar.returncode != 0 or ssh.returncode not in (0, None):
            raise BackupError("transfer", "传输失败")
        return
    remote = f"mkdir -p {dest} && tar -C {dest} -xf -"
    tar = subprocess.Popen(
        ["tar", "-C", str(local), "-cf", "-", "."],
        stdout=subprocess.PIPE,
        env=env,
    )
    assert tar.stdout is not None
    try:
        ssh = subprocess.run(
            ["ssh", *SSH_OPTS, host, remote],
            stdin=tar.stdout,
            capture_output=True,
            timeout=3600,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        tar.kill()
        raise BackupError("transfer", "传输失败") from exc
    tar.stdout.close()
    tar.wait(timeout=30)
    if ssh.returncode != 0 or tar.returncode not in (0, None):
        raise BackupError("transfer", "传输失败")


def _rsync(
    local: Path,
    host: str,
    remote_path: str,
    *,
    reverse: bool = False,
    resumable: bool = False,
) -> None:
    """OpenSSH 上优先 rsync;JumpServer/协议不兼容时改 tar 管道。"""
    local.mkdir(parents=True, exist_ok=True)
    rsync = _rsync_bin()
    if rsync:
        spec = f"{host}:{remote_path}/"
        src, dst = (spec, f"{local}/") if reverse else (f"{local}/", spec)
        rsh = " ".join([shutil.which("ssh") or "ssh", *SSH_OPTS])
        args = [
            rsync,
            "--protocol=29",
            "--old-args",
            "-a",
            "-e",
            rsh,
        ]
        if resumable:
            # generation 不可变；保留 partial 并附加校验，重试可复用已写入字节。
            args.extend(["--partial", "--append-verify"])
        else:
            args.append("--delete")
        args.extend([src, dst])
        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=3600 if resumable else 8,
            )
        except (OSError, subprocess.TimeoutExpired):
            proc = None
        if proc is not None and proc.returncode == 0:
            return
        if resumable:
            raise BackupError("transfer", "可续传传输失败")
    if resumable:
        raise BackupError("transfer", "本机 rsync 不可用，无法续传")
    _tar_ssh(local, host, remote_path, reverse=reverse)


def _ensure_remote_layout(spec: RemoteSpec) -> None:
    _ssh_python(spec.ssh, _REMOTE_MKDIR, [spec.path])


def _ssh_read_current(spec: RemoteSpec) -> str | None:
    raw = _ssh_python(spec.ssh, _REMOTE_READLINK, [spec.path], stage="verify")
    target = _ssh_payload(raw).strip()
    if not target:
        return None
    if not target.startswith("generations/") or "/" in target[len("generations/") :]:
        raise BackupError("verify", "current 目标非法")
    backup_id = target.split("/", 1)[1]
    validate_backup_id(backup_id)
    if target != f"generations/{backup_id}":
        raise BackupError("verify", "current 目标非法")
    return backup_id


def _ssh_read_text(spec: RemoteSpec, rel: str) -> str:
    path = spec.path.rstrip("/") + "/" + rel.lstrip("/")
    return _ssh_python(spec.ssh, _REMOTE_READ_TEXT, [path], stage="verify")


def _hydrate_remote(
    spec: RemoteSpec, dest_root: Path, backup_id: str | None
) -> str:
    if backup_id is None:
        backup_id = _ssh_read_current(spec)
        if backup_id is None:
            raise BackupError("verify", "没有 current", code=2)
    else:
        validate_backup_id(backup_id)
    local_gen = dest_root / "generations" / backup_id
    remote_gen = spec.path.rstrip("/") + f"/generations/{backup_id}"
    _rsync(local_gen, spec.ssh, remote_gen, reverse=True)
    _atomic_current(dest_root, backup_id)
    return backup_id


def publish_remote(spec: RemoteSpec, serve_root: Path) -> PublishResult:
    """经 OpenSSH/rsync 把 generation 传到 remote,校验后原子切 current。"""
    serve_root = Path(serve_root).resolve()
    with tempfile.TemporaryDirectory() as raw:
        cand = Path(raw) / "cand"
        cand.mkdir()
        payload = build_candidate(serve_root, cand)
        _ensure_remote_layout(spec)
        observed = _ssh_read_current(spec)
        if observed:
            try:
                cur_list = json.loads(
                    _ssh_read_text(spec, f"generations/{observed}/backup.json")
                )
            except (json.JSONDecodeError, TypeError) as exc:
                raise BackupError("verify", "备份清单损坏") from exc
            if cur_list.get("content_sha256") == payload["content_sha256"]:
                files = cur_list.get("files") or []
                return PublishResult(
                    "unchanged",
                    observed,
                    len(files),
                    sum(f.get("size") or 0 for f in files),
                )
        incoming = spec.path.rstrip("/") + f"/.incoming/{payload['backup_id']}"
        _ssh_python(
            spec.ssh,
            "import shutil,sys\nfrom pathlib import Path\n"
            "p=Path(sys.argv[1])\n"
            "shutil.rmtree(p, ignore_errors=True)\n"
            "p.mkdir(parents=True)\n",
            [incoming],
            stage="transfer",
        )
        _rsync(cand, spec.ssh, incoming)
        _ssh_python(
            spec.ssh,
            _REMOTE_VALIDATE,
            [incoming],
            stage="verify",
            timeout=3600,
        )
        _ssh_python(
            spec.ssh,
            _REMOTE_COMMIT,
            [spec.path, payload["backup_id"], observed or ""],
            stage="submit",
        )
        nbytes = sum(f["size"] for f in payload["files"])
        return PublishResult("pushed", payload["backup_id"], len(payload["files"]), nbytes)


def verify_remote(spec: RemoteSpec, backup_id: str | None = None) -> PublishResult:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        bid = _hydrate_remote(spec, root, backup_id)
        return verify_generation(root, bid)


def restore_remote(
    spec: RemoteSpec, dest: Path, backup_id: str | None = None
) -> PublishResult:
    dest = Path(dest)
    _ensure_restore_destination(dest)
    bid = _resolve_restore_backup_id(spec, dest, backup_id)
    stage = _prepare_restore_stage(spec, dest, bid)
    generation = stage / bid
    remote_gen = spec.path.rstrip("/") + f"/generations/{bid}"
    _rsync(generation, spec.ssh, remote_gen, reverse=True, resumable=True)
    _validate_restore_stage_tree(stage, bid)
    listing = validate_generation(generation)
    _promote_restored_data(stage, dest)
    shutil.rmtree(stage)
    return PublishResult(
        "restored",
        bid,
        len(listing["files"]),
        sum(item["size"] for item in listing["files"]),
    )


def _restore_stage_parent(dest: Path) -> Path:
    return dest.parent / ".kairo-restore"


def _restore_destination_id(dest: Path) -> str:
    return str(dest.resolve(strict=False))


def _restore_stage_key(spec: RemoteSpec, dest: Path, backup_id: str) -> str:
    raw = "\0".join((_restore_destination_id(dest), spec.name, backup_id)).encode()
    return hashlib.sha256(raw).hexdigest()


def _restore_stage_path(spec: RemoteSpec, dest: Path, backup_id: str) -> Path:
    return _restore_stage_parent(dest) / _restore_stage_key(spec, dest, backup_id)


def _ensure_restore_destination(dest: Path) -> None:
    if dest.exists():
        if not _ordinary_dir(dest):
            raise BackupError("restore", "目标不是空目录", code=2)
        try:
            if any(dest.iterdir()):
                raise BackupError("restore", "目标不是空目录", code=2)
        except OSError as exc:
            raise BackupError("restore", "目标不可读", code=2) from exc
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)


def _restore_stage_metadata(spec: RemoteSpec, dest: Path, backup_id: str) -> dict[str, Any]:
    return {
        "schema_version": RESTORE_STAGE_SCHEMA,
        "remote": spec.name,
        "destination": _restore_destination_id(dest),
        "backup_id": backup_id,
    }


def _write_restore_stage_metadata(stage: Path, data: dict[str, Any]) -> None:
    tmp = stage / ".restore.json.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, stage / "restore.json")


def _read_restore_stage_metadata(stage: Path) -> dict[str, Any]:
    path = stage / "restore.json"
    if not _ordinary_file(path):
        raise BackupError("restore", "恢复暂存元数据损坏", code=2)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError("restore", "恢复暂存元数据损坏", code=2) from exc
    if (
        not isinstance(data, dict)
        or data.get("schema_version") != RESTORE_STAGE_SCHEMA
        or not isinstance(data.get("remote"), str)
        or not isinstance(data.get("destination"), str)
        or not isinstance(data.get("backup_id"), str)
    ):
        raise BackupError("restore", "恢复暂存元数据损坏", code=2)
    validate_backup_id(data["backup_id"])
    return data


def _matching_restore_stages(spec: RemoteSpec, dest: Path) -> list[dict[str, Any]]:
    parent = _restore_stage_parent(dest)
    if not parent.exists():
        return []
    if not _ordinary_dir(parent):
        raise BackupError("restore", "恢复暂存目录非法", code=2)
    matches: list[dict[str, Any]] = []
    try:
        children = list(parent.iterdir())
    except OSError as exc:
        raise BackupError("restore", "恢复暂存目录不可读", code=2) from exc
    for child in children:
        if not _ordinary_dir(child):
            continue
        metadata = _read_restore_stage_metadata(child)
        if (
            metadata["remote"] == spec.name
            and metadata["destination"] == _restore_destination_id(dest)
        ):
            matches.append({"path": child, **metadata})
    return matches


def _resolve_restore_backup_id(
    spec: RemoteSpec, dest: Path, backup_id: str | None
) -> str:
    if backup_id is not None:
        return validate_backup_id(backup_id)
    matches = _matching_restore_stages(spec, dest)
    if len(matches) == 1:
        return matches[0]["backup_id"]
    if len(matches) > 1:
        raise BackupError("restore", "存在多个未完成恢复，请指定 --backup-id", code=2)
    current = _ssh_read_current(spec)
    if current is None:
        raise BackupError("verify", "没有 current", code=2)
    return current


def _prepare_restore_stage(spec: RemoteSpec, dest: Path, backup_id: str) -> Path:
    stage = _restore_stage_path(spec, dest, backup_id)
    expected = _restore_stage_metadata(spec, dest, backup_id)
    if stage.exists():
        if not _ordinary_dir(stage) or _read_restore_stage_metadata(stage) != expected:
            raise BackupError("restore", "恢复暂存与输入不匹配", code=2)
    else:
        stage.mkdir(parents=True)
        _write_restore_stage_metadata(stage, expected)
    _validate_restore_stage_tree(stage, backup_id)
    return stage


def _validate_restore_stage_tree(stage: Path, backup_id: str) -> None:
    """恢复前后均拒绝暂存里的符号链接或特殊文件。"""
    if not _ordinary_dir(stage):
        raise BackupError("restore", "恢复暂存目录非法", code=2)
    try:
        children = {child.name for child in stage.iterdir()}
    except OSError as exc:
        raise BackupError("restore", "恢复暂存目录不可读", code=2) from exc
    if children - {"restore.json", backup_id}:
        raise BackupError("restore", "恢复暂存内容非法", code=2)
    if not _ordinary_file(stage / "restore.json"):
        raise BackupError("restore", "恢复暂存元数据损坏", code=2)
    generation = stage / backup_id
    if not generation.exists():
        return
    if not _ordinary_dir(generation):
        raise BackupError("restore", "恢复暂存 generation 非法", code=2)
    try:
        walk_ordinary(generation)
    except BackupError as exc:
        raise BackupError("restore", "恢复暂存含符号链接或特殊文件", code=2) from exc


def _promote_restored_data(stage: Path, dest: Path) -> None:
    metadata = _read_restore_stage_metadata(stage)
    generation = stage / metadata["backup_id"]
    data = generation / "data"
    if not _ordinary_dir(data):
        raise BackupError("verify", "generation 不完整")
    # 传输期间若目标被写入，宁可保留暂存，也绝不覆盖用户内容。
    _ensure_restore_destination(dest)
    if dest.exists():
        dest.rmdir()
    try:
        data.replace(dest)
    except OSError as exc:
        raise BackupError("restore", "目标提升失败") from exc


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
                try:
                    cur_list = json.loads(
                        (cur_gen / "backup.json").read_text(encoding="utf-8")
                    )
                except (OSError, json.JSONDecodeError) as exc:
                    raise BackupError("verify", "备份清单损坏") from exc
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
    gen = Path(remote_root) / "generations" / result.backup_id
    parent = dest.parent
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent) as raw:
        staged = Path(raw) / result.backup_id
        staged.mkdir()
        shutil.copyfile(gen / "backup.json", staged / "backup.json")
        shutil.copytree(gen / "data", staged / "data", symlinks=False)
        validate_generation(staged)
        data = staged / "data"
        if dest.exists():
            dest.rmdir()
        data.replace(dest)
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
        result = publish_remote(spec, serve_root)
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
