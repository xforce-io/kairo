"""Workspace —— 一个 topic 的自包含目录。"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import secrets
import shutil
from pathlib import Path

import yaml

from kairo import corpus
from kairo.models import (
    Constitution,
    Form,
    GlossaryEntry,
    Manifest,
    State,
    _default_roles_by_ext,
)
from kairo.timeline import is_fold_class, parse_added_at, parse_calendar_date


class AddError(Exception):
    """add 的输入不合法(如目录摄入未加 --corpus);CLI 转友好提示。"""


class WorkspaceNotFound(Exception):
    """当前目录不是 kairo 工作区(无 .kairo/state.json)。"""


class WorkspaceBusy(Exception):
    """工作区正忙(如 step 运行中),拒绝删除等危险操作。"""


def delete_workspace(serve_root: Path | str, slug: str) -> None:
    """#78:删除 serve root 下某个 workspace 整目录。

    - 仅允许 root 的直接子目录;拒绝 `..` / 越界
    - 必须是可识别的 workspace(含 constitution.yaml)
    - 不碰 root/glossary.yaml 及其它 workspace
    """
    root = Path(serve_root).resolve()
    # 拒绝路径分隔与隐藏名(/ 与反斜杠)
    if (
        not slug
        or slug in (".", "..")
        or "/" in slug
        or chr(92) in slug
        or slug.startswith(".")
    ):
        raise ValueError(f"非法 workspace 名:{slug!r}")
    dest = (root / slug).resolve()
    if dest.parent != root:
        raise ValueError(f"非法 workspace 名:{slug!r}")
    if not dest.is_dir() or not (dest / "constitution.yaml").is_file():
        raise WorkspaceNotFound(dest)
    shutil.rmtree(dest)


def restep_target_for(key: str) -> str:
    """digest 产物键 → reference id;活 target 路径原样。"""
    prefix, suffix = "references/", "/digest.md"
    if key.startswith(prefix) and key.endswith(suffix):
        mid = key[len(prefix) : -len(suffix)]
        if mid and "/" not in mid:
            return mid
    return key


def stamp_serve_workspaces(serve_root: Path | str) -> None:
    """#163:root 真名册变更后,给各 workspace 已有产物打尚未重新校正。"""
    root = Path(serve_root)
    if not root.is_dir():
        return
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        if not (d / "constitution.yaml").is_file():
            continue
        try:
            Workspace.open(d).stamp_glossary_pending()
        except WorkspaceNotFound:
            continue


def _slug(text: str) -> str:
    # 保留中文/字母数字(unicode word),标点/空白 → -;全标点(空)回退内容 hash 保唯一
    s = re.sub(r"[^\w]+", "-", text.lower()).strip("-_")
    return s or hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def _keyed_transform_filename(role: str, source: Form, sources: list[Form]) -> str:
    """多源派生产物名；basename 冲突时用 location hash 消歧。"""
    slug = _slug(Path(source.location).name)
    if sum(_slug(Path(item.location).name) == slug for item in sources) > 1:
        suffix = hashlib.sha256(source.location.encode()).hexdigest()[:8]
        slug = f"{slug}-{suffix}"
    return f"{role}.{slug}.md"


def default_reference_title(*, now: datetime.datetime | None = None) -> str:
    """新建 reference 的默认展示名:本地时间 ``YYYYMMDD-HH``(#103)。

    仅人读 title;不参与 ref_id / 目录分配。``now`` 供测试注入。
    """
    t = now if now is not None else datetime.datetime.now()
    return t.strftime("%Y%m%d-%H")


def _resolve_new_title(title: str | None) -> str:
    """新建 manifest 时解析 title:显式传入保留,否则用默认时间格式。"""
    return title if title is not None else default_reference_title()


class Workspace:
    def _alloc_ref_id(self, name: str) -> str:
        """自动派生 ref_id 并用 mkdir 独占认领 references/<id>/ (#81 E1)。

        格式 ``YYYY-MM-DD-<slug>``;目录已存在则 ``-2``/``-3``…,再随机后缀。
        ``mkdir(exist_ok=False)`` 在并发下保证两个 add 不会认到同一 id(静默丢材料)。
        显式传入的 ref_id 不走本函数,仍可向既有 ref 追加 forms。
        """
        today = datetime.date.today().isoformat()
        base = f"{today}-{_slug(name)}"
        refs = self.references_dir()
        refs.mkdir(parents=True, exist_ok=True)
        candidates = [base]
        candidates.extend(f"{base}-{n}" for n in range(2, 64))
        candidates.extend(f"{base}-{secrets.token_hex(3)}" for _ in range(16))
        for rid in candidates:
            try:
                (refs / rid).mkdir(exist_ok=False)
                return rid
            except FileExistsError:
                continue
        raise AddError(f"无法分配唯一 reference id(base={base})")
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @classmethod
    def open(cls, root: Path | str) -> "Workspace":
        """打开既有工作区;非工作区抛 WorkspaceNotFound(供 CLI 转友好提示)。"""
        ws = cls(root)
        if not ws.state_path.exists():
            raise WorkspaceNotFound(ws.root)
        return ws

    @classmethod
    def init(cls, root: Path | str, topic: str = "main", *, kind: str | None = None) -> "Workspace":
        from kairo.kind import KIND_TOPIC, fill_at_create

        root = Path(root)
        (root / ".kairo").mkdir(parents=True, exist_ok=True)
        con = Constitution(topic=topic, kind=kind or KIND_TOPIC)
        fill_at_create(con)
        (root / "constitution.yaml").write_text(
            yaml.safe_dump(con.model_dump(), allow_unicode=True, sort_keys=False)
        )
        (root / ".kairo" / "state.json").write_text(
            json.dumps({"products": {}, "targets": {}}, ensure_ascii=False, indent=2)
        )
        return cls(root)

    @property
    def constitution(self) -> Constitution:
        data = yaml.safe_load((self.root / "constitution.yaml").read_text())
        return Constitution.model_validate(data)

    @property
    def state_path(self) -> Path:
        return self.root / ".kairo" / "state.json"

    def read_state(self) -> State:
        data = json.loads(self.state_path.read_text())
        return State.model_validate(data)

    def write_state(self, state: State) -> None:
        self.state_path.write_text(
            json.dumps(state.model_dump(), ensure_ascii=False, indent=2)
        )

    # ---- references ----

    def references_dir(self) -> Path:
        return self.root / "references"

    def guess_role(self, path: Path) -> str:
        """按扩展名猜 role:constitution.roles_by_ext(用户/旧 workspace 配置)优先,缺失则
        回退内置默认映射(音频/文档/图片),再退 default_role。旧 workspace 的 constitution
        冻结了旧映射,内置回退确保新增内置类型(如图片→attachment)对既有 workspace 也生效。"""
        ext = path.suffix.lower()
        rbe = self.constitution.roles_by_ext
        if ext in rbe:
            return rbe[ext]
        return _default_roles_by_ext().get(ext, self.constitution.default_role)

    def _copy_into(self, src: Path, dest_dir: Path) -> Path:
        """把源文件拷进 dest_dir;同名则 stem-1/stem-2…。返回副本路径。

        文件名取自源 basename,不依赖 title(#64:title ⊥ 副本名)。
        #81 E1:用 O_EXCL 独占创建空文件再 copy2,避免并发同名互盖静默丢字节。
        """
        dest_dir.mkdir(parents=True, exist_ok=True)
        stem, suffix = src.stem, src.suffix
        names = [src.name]
        names.extend(f"{stem}-{n}{suffix}" for n in range(1, 64))
        names.extend(f"{stem}-{secrets.token_hex(3)}{suffix}" for _ in range(8))
        for name in names:
            dest = dest_dir / name
            try:
                fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
            except FileExistsError:
                continue
            shutil.copy2(src, dest)
            return dest
        raise AddError(f"无法在 {dest_dir} 为 {src.name} 分配唯一副本名")

    def _known_role_exts(self) -> set[str]:
        """可摄入扩展名:constitution 声明 ∪ 内置默认(音频/文档/图片)。"""
        return set(self.constitution.roles_by_ext) | set(_default_roles_by_ext())

    # 未进 roles_by_ext 但仍常作正文的扩展(走 default_role=transcript)
    _TEXT_FALLBACK_EXTS = frozenset({".txt", ".md", ".markdown", ".text"})

    def _list_stream_dir_files(self, d: Path) -> list[Path]:
        """目录一层内可作 stream form 的文件(稳定按名排序)。"""
        known = self._known_role_exts() | self._TEXT_FALLBACK_EXTS
        out: list[Path] = []
        for p in sorted(d.iterdir(), key=lambda x: x.name.lower()):
            if not p.is_file():
                continue
            if p.name.startswith(".") or p.name == ".DS_Store":
                continue
            if p.suffix.lower() not in known:
                continue
            out.append(p)
        return out

    def _form_location(self, f: Path) -> str:
        return str(f.relative_to(self.root)) if f.is_relative_to(self.root) else str(f)

    def _add_stream_dir(
        self,
        d: Path,
        *,
        ref_id: str | None,
        title: str | None,
        role: str | None,
        copy: bool,
        occurred_at: str | None = None,
    ) -> str:
        """#67:目录 → 一条 stream 多形态 reference(夹内文件全部挂 forms)。"""
        members = self._list_stream_dir_files(d)
        if not members:
            raise AddError(
                f"目录内没有可添加为参考的文件:{d}"
                "(仅识别音频/文档/图片/文本等已知扩展名,且不递归子目录)"
            )
        if ref_id is None:
            ref_id = self._alloc_ref_id(d.name)  # 已独占创建 references/<id>/
        else:
            (self.references_dir() / ref_id).mkdir(parents=True, exist_ok=True)
        ref_dir = self.references_dir() / ref_id
        if copy:
            members = [self._copy_into(f, ref_dir) for f in members]
        # 复用文件 add;copy 已处理。title 原样下传(None → add 内默认 YYYYMMDD-HH,#103)
        return self.add(
            members,
            ref_id=ref_id,
            role=role,
            title=title,
            source_class="stream",
            copy=False,
            occurred_at=occurred_at,
        )

    def add(
        self,
        files: list[Path | str],
        ref_id: str | None = None,
        role: str | None = None,
        title: str | None = None,
        source_class: str | None = None,
        copy: bool = False,
        occurred_at: str | None = None,
    ) -> str:
        """登记 reference 形态。

        - 文件:默认路径指针;copy=True 物化(#64)
        - 目录 + stream:一条多形态 ref(#67)
        - 目录 + corpus:目录树指针(#24);不支持 copy
        """
        files = [Path(f).expanduser() for f in files]
        missing = [f for f in files if not f.exists()]
        if missing:
            raise AddError(f"路径不存在:{missing[0]}")

        occ = None
        if occurred_at is not None:
            occ = parse_calendar_date(occurred_at)
            if occ is None:
                raise AddError(f"非法发生时间:{occurred_at}")

        dirs = [f for f in files if f.is_dir()]
        if dirs:
            if len(files) != 1:
                raise AddError("目录摄入仅支持单个目录参数(不与文件混加)")
            d = dirs[0]
            cls = source_class or self.constitution.default_class
            if occ is not None and not is_fold_class(self, cls):
                raise AddError("fold=false 不能设发生时间")
            if cls == "corpus":
                if copy:
                    raise AddError(
                        "基线目录不支持复制整树;请用目录指针(添加基线 / add --corpus,勿勾选复制)"
                    )
                return self._add_corpus_tree(
                    [d], ref_id=ref_id, title=title, source_class="corpus"
                )
            return self._add_stream_dir(
                d,
                ref_id=ref_id,
                title=title,
                role=role,
                copy=copy,
                occurred_at=occurred_at,
            )

        if copy:
            if ref_id is not None and (self.references_dir() / ref_id).is_dir():
                dest_dir = self.references_dir() / ref_id
            else:
                dest_dir = self.root / ".kairo" / "uploads"
            files = [self._copy_into(f, dest_dir) for f in files]
        claimed = False
        if ref_id is None:
            # #81 E1:自动 id 独占认领,避免同 stem 并发/连加认到同一 id 静默丢材料
            ref_id = self._alloc_ref_id(files[0].stem)
            claimed = True
        ref_dir = self.references_dir() / ref_id
        existing = ref_dir / "manifest.yaml"
        new_forms = [
            Form(
                role=role or self.guess_role(f),
                location=self._form_location(f),
                hash=hashlib.sha256(f.read_bytes()).hexdigest()[:12],
                origin="added",
            )
            for f in files
        ]
        cls = source_class or self.constitution.default_class
        if occ is not None and not is_fold_class(self, cls):
            raise AddError("fold=false 不能设发生时间")
        if existing.is_file():
            # 追加到已有 ref(仅显式 ref_id):保留既有 forms,按 location 去重
            man = self.read_manifest(ref_id)
            have = {fm.location for fm in man.forms}
            man.forms.extend(fm for fm in new_forms if fm.location not in have)
            if occ is not None:
                if not is_fold_class(self, man.source_class):
                    raise AddError("fold=false 不能设发生时间")
                man.occurred_at = occ.isoformat()
        else:
            if not claimed:
                ref_dir.mkdir(parents=True, exist_ok=True)
            man = Manifest(
                id=ref_id,
                title=_resolve_new_title(title),
                source_class=cls,
                forms=new_forms,
                occurred_at=occ.isoformat() if occ else None,
                added_at=datetime.datetime.now().astimezone().isoformat(),
            )
        self.write_manifest(ref_id, man)
        return ref_id

    def _add_corpus_tree(
        self,
        files: list[Path],
        ref_id: str | None,
        title: str | None,
        source_class: str | None,
    ) -> str:
        """目录指针式 corpus 摄入:整个目录登记为一条 corpus_tree reference。"""
        if len(files) != 1:
            raise AddError("目录摄入仅支持单个目录参数(不与文件混加)")
        d = files[0]
        if (source_class or self.constitution.default_class) != "corpus":
            raise AddError(f"内部错误:非 corpus 目录应走多形态 stream 路径:{d}")
        if ref_id is None:
            ref_id = self._alloc_ref_id(d.name)
        else:
            (self.references_dir() / ref_id).mkdir(parents=True, exist_ok=True)
        man = Manifest(
            id=ref_id,
            title=_resolve_new_title(title),
            source_class="corpus",
            forms=[
                Form(
                    role=corpus.CORPUS_TREE_ROLE,
                    location=str(d),
                    hash=corpus.tree_hash(d),
                    origin="added",
                )
            ],
        )
        self.write_manifest(ref_id, man)
        return ref_id

    def set_title(self, ref_id: str, title: str) -> None:
        """重命名一条 reference 的展示名(title)。title 仅供人读,非身份/非溯源链:
        ref_id、目录、产物来源标记都不依赖它,故改名安全无副作用。空标题拒绝。"""
        title = title.strip()
        if not title:
            raise ValueError("title 不能为空")
        man = self.read_manifest(ref_id)
        man.title = title
        self.write_manifest(ref_id, man)

    def set_occurred(self, ref_id: str, day: datetime.date | None) -> None:
        """手改或清空发生时间。无时间轴资格拒绝。不触发 step。"""
        man = self.read_manifest(ref_id)
        if not is_fold_class(self, man.source_class):
            raise ValueError("fold=false 不能设发生时间")
        man.occurred_at = day.isoformat() if day is not None else None
        self.write_manifest(ref_id, man)

    # ---- constitution / glossary (#69) ----

    def write_constitution(self, con: Constitution) -> None:
        """整表写回 constitution.yaml(pydantic round-trip)。"""
        (self.root / "constitution.yaml").write_text(
            yaml.safe_dump(con.model_dump(), allow_unicode=True, sort_keys=False)
        )

    def add_glossary_entry(
        self,
        name: str,
        note: str = "",
        aka: list[str] | None = None,
        tags: list[str] | None = None,
        *,
        serve_root: Path | None = None,
    ) -> GlossaryEntry:
        """兼容旧 API，但唯一写入 v2 KnowledgeStore，绝不回落 glossary 字段。"""
        from kairo.glossary import GlossaryEntry
        from kairo.glossary import resolve_serve_root
        from kairo.knowledge import (
            KnowledgeAlias,
            effective_entries,
            load_workspace,
            new_entry,
            save_workspace,
            validate_entries,
        )

        root = resolve_serve_root(ws_root=self.root, explicit=serve_root)
        document, _ = load_workspace(self.root)
        entry = new_entry(
            title=name,
            scope="workspace",
            aliases=[KnowledgeAlias(value=value) for value in aka or []],
            description=note,
            tags=tags or [],
        )
        validate_entries([*document.entries, entry], scope="workspace")
        # effective_entries 同时验证 root/workspace 可读；跨 scope alias 冲突由 matcher 局部处理。
        _ = effective_entries(root, self.root)
        document.entries.append(entry)
        save_workspace(self.root, document)
        self.stamp_knowledge_pending()
        return GlossaryEntry(name=entry.title, note=entry.description, aka=[a.value for a in entry.aliases], tags=entry.tags)

    def remove_glossary_entry(self, index: int, *, serve_root: Path | None = None) -> None:
        """兼容旧索引删除，但只改 constitution.knowledge。"""
        from kairo.glossary import resolve_serve_root
        from kairo.knowledge import load_workspace, save_workspace

        # 旧 API 也必须先验证它确实属于传入的 serve root，才允许读写。
        resolve_serve_root(ws_root=self.root, explicit=serve_root)
        document, _ = load_workspace(self.root)
        document.entries.pop(index)
        save_workspace(self.root, document)
        self.stamp_knowledge_pending()

    def stamp_knowledge_pending(self) -> None:
        """旧产物没有 knowledge_hash 时标出待人工重新校正；不触发运行。"""
        state = self.read_state()
        dirty = False
        for ps in state.products.values():
            if ps.knowledge_hash is None:
                ps.knowledge_hash = ""
                dirty = True
        for ts in state.targets.values():
            if ts.knowledge_hash is None:
                ts.knowledge_hash = ""
                dirty = True
        if dirty:
            self.write_state(state)

    def stamp_glossary_pending(self) -> None:
        """#163:已有产物缺 glossary_hash 时标脏,使尚未重新校正可见。不触发 step。"""
        state = self.read_state()
        dirty = False
        for ps in state.products.values():
            if ps.glossary_hash is None:
                ps.glossary_hash = ""
                dirty = True
        for ts in state.targets.values():
            if ts.glossary_hash is None:
                ts.glossary_hash = ""
                dirty = True
        if dirty:
            self.write_state(state)

    def glossary_pending(self, *, serve_root: Path | None = None) -> list[str]:
        """当前生效 hash 与产物记录不一致的文档/digest 路径。"""
        from kairo.glossary import effective_hash, workspace_effective

        current = effective_hash([i.entry for i in workspace_effective(self.root, serve_root=serve_root)])
        state = self.read_state()
        out: list[str] = []
        for key, ps in state.products.items():
            if ps.glossary_hash is not None and ps.glossary_hash != current:
                out.append(key)
        for key, ts in state.targets.items():
            if ts.glossary_hash is not None and ts.glossary_hash != current:
                out.append(key)
        return out

    def glossary_reference(self, *, serve_root: Path | None = None) -> str:
        """root ⊕ workspace 生效真名册渲染注入段(#163)。"""
        from kairo.glossary import format_glossary_reference, merged_glossary_entries, load_workspace_glossary

        entries = merged_glossary_entries(
            load_workspace_glossary(self.root), self.root, serve_root=serve_root
        )
        return format_glossary_reference(entries)

    def read_manifest(self, ref_id: str) -> Manifest:
        path = self.references_dir() / ref_id / "manifest.yaml"
        return Manifest.model_validate(yaml.safe_load(path.read_text()))

    def write_manifest(self, ref_id: str, man: Manifest) -> None:
        path = self.references_dir() / ref_id / "manifest.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            old_added = None
            try:
                old = Manifest.model_validate(yaml.safe_load(path.read_text()) or {})
                old_added = old.added_at if parse_added_at(old.added_at) else None
            except Exception:
                old_added = None
            if old_added is not None:
                man.added_at = old_added
            else:
                man.added_at = datetime.datetime.fromtimestamp(
                    path.stat().st_mtime
                ).astimezone().isoformat()
        elif parse_added_at(man.added_at) is None:
            man.added_at = datetime.datetime.now().astimezone().isoformat()
        payload = yaml.safe_dump(
            man.model_dump(by_alias=True, exclude_none=True),
            allow_unicode=True,
            sort_keys=False,
        )
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)

    def list_reference_ids(self) -> list[str]:
        d = self.references_dir()
        if not d.exists():
            return []
        return sorted(
            p.name for p in d.iterdir() if (p / "manifest.yaml").is_file()
        )
