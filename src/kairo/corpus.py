"""corpus(基线参考层)概念的归属地。

corpus 不 digest、不 ASR —— 它是只读参考层:compose 时拼成「基线前言」(各类
hint + 文件清单/目录树)并经 read_dirs 授 agent 只读,agent 按需 Read。

本模块统一 file(单文件)与 tree(目录指针)两种形态:CorpusRef + collect /
reference_section / read_dirs / stamp。ComposeRule 只委托这些,不再自己懂 corpus。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

CORPUS_TREE_ROLE = "corpus_tree"  # 目录指针 form 的 role


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


# ---- 树助手(tree 形态内部用) ----


def walk_files(root: Path) -> list[Path]:
    """递归列出 root 下的文件,返回相对 root 的路径(排序);跳过隐藏项(名以 . 开头)。"""
    out: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            continue
        out.append(rel)
    return sorted(out)


def tree_hash(root: Path) -> str:
    """全树指纹:hash(sorted [(relpath, bytes-hash)]);文件增/删/改均翻戳,隐藏项不计。"""
    parts = [
        f"{rel}:{hashlib.sha256((root / rel).read_bytes()).hexdigest()[:12]}"
        for rel in walk_files(root)
    ]
    return _hash("\n".join(parts))


def render_tree(root: Path, files: list[Path]) -> str:
    """把相对路径列表渲染成缩进树(每层 2 空格;目录只出一次)。"""
    lines: list[str] = []
    seen: set[tuple[str, ...]] = set()
    for rel in files:
        parts = rel.parts
        for depth, name in enumerate(parts[:-1]):  # 目录
            prefix = parts[: depth + 1]
            if prefix not in seen:
                seen.add(prefix)
                lines.append(f"{'  ' * depth}{name}/")
        lines.append(f"{'  ' * (len(parts) - 1)}{parts[-1]}")  # 文件
    return "\n".join(lines)


# ---- CorpusRef:file / tree 两形态的统一抽象 ----


@dataclass
class CorpusRef:
    ref_id: str
    title: str
    cls: str  # source_class
    path: Path  # file 型=正文文件;tree 型=目录根(均绝对路径)
    kind: str  # "file" | "tree"

    def read_dir(self) -> Path:
        """授予 agent 只读的目录:file→父目录;tree→目录根。"""
        return self.path if self.kind == "tree" else self.path.parent

    def stamp_input(self) -> str:
        """版本戳输入:file→正文文本;tree→全树指纹。"""
        if self.kind == "tree":
            return tree_hash(self.path)
        return self.path.read_text() if self.path.exists() else ""

    def render(self) -> str:
        """基线段一项:file→单行;tree→标题 + 缩进树。"""
        if self.kind == "tree":
            tree = render_tree(self.path, walk_files(self.path))
            body = "\n".join("  " + ln for ln in tree.splitlines())
            return f"- {self.title}/(目录树,按需 Read):\n{body}"
        return f"- {self.title}:{self.path}"


def _resolve(ws, location: str) -> Path:
    loc = Path(location)
    return loc if loc.is_absolute() else ws.root / loc


def _is_fold_class(ws, source_class: str) -> bool:
    """该类源是否折叠(fold=True);fold=False 即 corpus 参考层。未知类按折叠处理。"""
    sc = ws.constitution.source_classes.get(source_class)
    return sc is None or sc.fold


def _body_path(ws, man) -> Path | None:
    """按 body_roles 优先序取该 reference 的正文文件绝对路径(file 型 corpus)。"""
    for role in ws.constitution.body_roles:
        for f in man.forms:
            if f.role == role:
                return _resolve(ws, f.location)
    return None


def collect(ws) -> list[CorpusRef]:
    """扫 references,挑 fold=False 的,识别 file / tree 形态。"""
    refs: list[CorpusRef] = []
    for ref_id in ws.list_reference_ids():
        man = ws.read_manifest(ref_id)
        if _is_fold_class(ws, man.source_class):
            continue
        title = man.title or ref_id
        tree_form = next((f for f in man.forms if f.role == CORPUS_TREE_ROLE), None)
        if tree_form is not None:
            refs.append(
                CorpusRef(ref_id, title, man.source_class, _resolve(ws, tree_form.location), "tree")
            )
            continue
        bp = _body_path(ws, man)
        if bp is not None:
            refs.append(CorpusRef(ref_id, title, man.source_class, bp, "file"))
    return refs


def reference_section(ws, refs: list[CorpusRef]) -> str:
    """组装基线参考前言:各 corpus 类的 hint + 各 ref 渲染(供 agent 按需 Read)。"""
    hint_lines = []
    for cls in sorted({r.cls for r in refs}):
        sc = ws.constitution.source_classes.get(cls)
        if sc:
            hint_lines.append(f"- {sc.label}:{sc.hint}")
    body_lines = [r.render() for r in refs]
    return (
        "\n\n[基线参考资料](权威底座;按需 Read 校正专名/锚定事实,勿照搬原文)\n"
        + "\n".join(hint_lines)
        + "\n文件(按需 Read):\n"
        + "\n".join(body_lines)
    )


def read_dirs(refs: list[CorpusRef]) -> list[Path]:
    return sorted({r.read_dir() for r in refs})


def stamp(refs: list[CorpusRef]) -> str:
    """corpus 参考层粗粒度版本戳;空层 → 稳定常量。供 drift advisory。"""
    return _hash("\n".join(sorted(r.stamp_input() for r in refs)))
