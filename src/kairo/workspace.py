"""Workspace —— 一个 topic 的自包含目录。"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
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


def _slug(text: str) -> str:
    # 保留中文/字母数字(unicode word),标点/空白 → -;全标点(空)回退内容 hash 保唯一
    s = re.sub(r"[^\w]+", "-", text.lower()).strip("-_")
    return s or hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


class Workspace:
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
    def init(cls, root: Path | str, topic: str = "main") -> "Workspace":
        root = Path(root)
        (root / ".kairo").mkdir(parents=True, exist_ok=True)
        con = Constitution(topic=topic)
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
        """
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        if dest.exists():
            stem, suffix = src.stem, src.suffix
            n = 1
            while dest.exists():
                dest = dest_dir / f"{stem}-{n}{suffix}"
                n += 1
        shutil.copy2(src, dest)
        return dest

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
    ) -> str:
        """#67:目录 → 一条 stream 多形态 reference(夹内文件全部挂 forms)。"""
        members = self._list_stream_dir_files(d)
        if not members:
            raise AddError(
                f"目录内没有可添加为参考的文件:{d}"
                "(仅识别音频/文档/图片/文本等已知扩展名,且不递归子目录)"
            )
        if ref_id is None:
            today = datetime.date.today().isoformat()
            ref_id = f"{today}-{_slug(d.name)}"
        ref_dir = self.references_dir() / ref_id
        ref_dir.mkdir(parents=True, exist_ok=True)
        if copy:
            members = [self._copy_into(f, ref_dir) for f in members]
        # 复用文件 add;copy 已处理
        return self.add(
            members,
            ref_id=ref_id,
            role=role,
            title=title or d.name,
            source_class="stream",
            copy=False,
        )

    def add(
        self,
        files: list[Path | str],
        ref_id: str | None = None,
        role: str | None = None,
        title: str | None = None,
        source_class: str | None = None,
        copy: bool = False,
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

        dirs = [f for f in files if f.is_dir()]
        if dirs:
            if len(files) != 1:
                raise AddError("目录摄入仅支持单个目录参数(不与文件混加)")
            d = dirs[0]
            cls = source_class or self.constitution.default_class
            if cls == "corpus":
                if copy:
                    raise AddError(
                        "基线目录不支持复制整树;请用目录指针(添加基线 / add --corpus,勿勾选复制)"
                    )
                return self._add_corpus_tree(
                    [d], ref_id=ref_id, title=title, source_class="corpus"
                )
            return self._add_stream_dir(
                d, ref_id=ref_id, title=title, role=role, copy=copy
            )

        if copy:
            if ref_id is not None and (self.references_dir() / ref_id).is_dir():
                dest_dir = self.references_dir() / ref_id
            else:
                dest_dir = self.root / ".kairo" / "uploads"
            files = [self._copy_into(f, dest_dir) for f in files]
        if ref_id is None:
            today = datetime.date.today().isoformat()
            ref_id = f"{today}-{_slug(files[0].stem)}"
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
        if existing.is_file():
            # 追加到已有 ref:保留既有 forms,按 location 去重
            man = self.read_manifest(ref_id)
            have = {fm.location for fm in man.forms}
            man.forms.extend(fm for fm in new_forms if fm.location not in have)
        else:
            ref_dir.mkdir(parents=True, exist_ok=True)
            man = Manifest(
                id=ref_id,
                title=title or files[0].stem,
                source_class=source_class or self.constitution.default_class,
                forms=new_forms,
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
            today = datetime.date.today().isoformat()
            ref_id = f"{today}-{_slug(d.name)}"
        ref_dir = self.references_dir() / ref_id
        ref_dir.mkdir(parents=True, exist_ok=True)
        man = Manifest(
            id=ref_id,
            title=title or d.name,
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
    ) -> GlossaryEntry:
        """追加一条 **workspace** 真名册;name 必填;重名拒绝。"""
        from kairo.glossary import add_entry

        con = self.constitution
        con.glossary = add_entry(con.glossary, name, note=note, aka=aka, tags=tags)
        self.write_constitution(con)
        return con.glossary[-1]

    def remove_glossary_entry(self, index: int) -> None:
        """按索引删除一条 **workspace** 真名册。"""
        from kairo.glossary import remove_entry

        con = self.constitution
        con.glossary = remove_entry(con.glossary, index)
        self.write_constitution(con)

    def glossary_reference(self, *, serve_root: Path | None = None) -> str:
        """合并 machine + root + workspace 后渲染注入段(#71)。"""
        from kairo.glossary import format_glossary_reference, merged_glossary_entries

        entries = merged_glossary_entries(
            self.constitution.glossary, self.root, serve_root=serve_root
        )
        return format_glossary_reference(entries)

    def read_manifest(self, ref_id: str) -> Manifest:
        path = self.references_dir() / ref_id / "manifest.yaml"
        return Manifest.model_validate(yaml.safe_load(path.read_text()))

    def write_manifest(self, ref_id: str, man: Manifest) -> None:
        path = self.references_dir() / ref_id / "manifest.yaml"
        path.write_text(
            yaml.safe_dump(
                man.model_dump(by_alias=True), allow_unicode=True, sort_keys=False
            )
        )

    def list_reference_ids(self) -> list[str]:
        d = self.references_dir()
        if not d.exists():
            return []
        return sorted(
            p.name for p in d.iterdir() if (p / "manifest.yaml").is_file()
        )
