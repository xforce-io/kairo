"""Workspace —— 一个 topic 的自包含目录。"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
from pathlib import Path

import yaml

from kairo.models import Constitution, Form, Manifest, State

AUDIO_EXTS = {".m4a", ".wav", ".mp3", ".aac", ".flac", ".ogg"}


def guess_role(path: Path) -> str:
    """按扩展名猜 role;此后以 manifest 为准(可 --role 覆盖)。"""
    if path.suffix.lower() in AUDIO_EXTS:
        return "audio"
    # M0:其余文本默认当转写稿正文;资料 source_text 用 --role 覆盖。
    return "transcript"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

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

    def add(
        self,
        files: list[Path | str],
        ref_id: str | None = None,
        role: str | None = None,
        title: str | None = None,
    ) -> str:
        files = [Path(f) for f in files]
        if ref_id is None:
            today = datetime.date.today().isoformat()
            ref_id = f"{today}-{_slug(files[0].stem)}"
        ref_dir = self.references_dir() / ref_id
        ref_dir.mkdir(parents=True, exist_ok=True)
        forms = [
            Form(
                role=role or guess_role(f),
                location=str(f),
                hash=hashlib.sha256(f.read_bytes()).hexdigest()[:12],
                origin="added",
            )
            for f in files
        ]
        man = Manifest(id=ref_id, title=title or files[0].stem, forms=forms)
        (ref_dir / "manifest.yaml").write_text(
            yaml.safe_dump(man.model_dump(), allow_unicode=True, sort_keys=False)
        )
        return ref_id

    def read_manifest(self, ref_id: str) -> Manifest:
        path = self.references_dir() / ref_id / "manifest.yaml"
        return Manifest.model_validate(yaml.safe_load(path.read_text()))

    def write_manifest(self, ref_id: str, man: Manifest) -> None:
        path = self.references_dir() / ref_id / "manifest.yaml"
        path.write_text(
            yaml.safe_dump(man.model_dump(), allow_unicode=True, sort_keys=False)
        )

    def list_reference_ids(self) -> list[str]:
        d = self.references_dir()
        if not d.exists():
            return []
        return sorted(
            p.name for p in d.iterdir() if (p / "manifest.yaml").is_file()
        )
