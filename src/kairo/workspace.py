"""Workspace —— 一个 topic 的自包含目录。"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
from pathlib import Path

import yaml

from kairo import corpus
from kairo.models import Constitution, Form, Manifest, State


class AddError(Exception):
    """add 的输入不合法(如目录摄入未加 --corpus);CLI 转友好提示。"""


class WorkspaceNotFound(Exception):
    """当前目录不是 kairo 工作区(无 .kairo/state.json)。"""


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
        """按扩展名猜 role(读 constitution.roles_by_ext);此后以 manifest 为准(可 --role 覆盖)。"""
        return self.constitution.roles_by_ext.get(
            path.suffix.lower(), self.constitution.default_role
        )

    def add(
        self,
        files: list[Path | str],
        ref_id: str | None = None,
        role: str | None = None,
        title: str | None = None,
        source_class: str | None = None,
    ) -> str:
        files = [Path(f) for f in files]
        if any(f.is_dir() for f in files):
            return self._add_corpus_tree(
                files, ref_id=ref_id, title=title, source_class=source_class
            )
        if ref_id is None:
            today = datetime.date.today().isoformat()
            ref_id = f"{today}-{_slug(files[0].stem)}"
        ref_dir = self.references_dir() / ref_id
        ref_dir.mkdir(parents=True, exist_ok=True)
        forms = [
            Form(
                role=role or self.guess_role(f),
                location=str(f),
                hash=hashlib.sha256(f.read_bytes()).hexdigest()[:12],
                origin="added",
            )
            for f in files
        ]
        man = Manifest(
            id=ref_id,
            title=title or files[0].stem,
            source_class=source_class or self.constitution.default_class,
            forms=forms,
        )
        (ref_dir / "manifest.yaml").write_text(
            yaml.safe_dump(
                man.model_dump(by_alias=True), allow_unicode=True, sort_keys=False
            )
        )
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
            raise AddError(
                f"目录摄入目前仅支持 corpus(加 --corpus);stream 请逐文件 add:{d}"
            )
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
