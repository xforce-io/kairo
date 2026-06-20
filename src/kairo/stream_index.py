"""#16 references 按 class 生成 stream(观测)导航索引。"""

from __future__ import annotations

from pathlib import Path

INDEX_NAME = "MEETINGS.md"


def _is_fold_class(ws, source_class: str) -> bool:
    """该类源是否折叠进 target(fold=True=stream/观测);fold=False 为 corpus 参考层。"""
    sc = ws.constitution.source_classes.get(source_class)
    return sc is None or sc.fold


def build_stream_index(ws) -> str:
    con = ws.constitution
    lines = [
        "# 观测纪要索引（stream）",
        "",
        "> 自动生成（#16）：按 source class 列出 stream（观测）类来源，"
        "与 corpus（基线）分开导航。",
        "",
        "| 标题 | 类型 | digest |",
        "|---|---|---|",
    ]
    rows = 0
    for ref_id in ws.list_reference_ids():
        man = ws.read_manifest(ref_id)
        if not _is_fold_class(ws, man.source_class):
            continue
        sc = con.source_classes.get(man.source_class)
        label = sc.label if sc else man.source_class
        digest = ws.references_dir() / ref_id / "digest.md"
        link = f"[digest](./{ref_id}/digest.md)" if digest.exists() else "—"
        lines.append(f"| {man.title} | {label} | {link} |")
        rows += 1
    if rows == 0:
        lines.append("| （暂无 stream 来源） | | |")
    return "\n".join(lines) + "\n"


def write_stream_index(ws) -> Path:
    """把 stream 索引写到 references/MEETINGS.md,返回其路径。"""
    path = ws.references_dir() / INDEX_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_stream_index(ws))
    return path
