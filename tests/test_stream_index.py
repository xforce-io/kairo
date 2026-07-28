"""#16 references 按 class 自动生成 stream 导航索引。"""

from kairo.engine import step
from kairo.provider import StubProvider
from kairo.stream_index import build_stream_index, write_stream_index
from kairo.workspace import Workspace


def _write(tmp_path, name, content="x"):
    p = tmp_path / name
    p.write_text(content)
    return p


def _make_digest(ws, ref_id, content="纪要内容"):
    d = ws.root / "references" / ref_id / "digest.md"
    d.write_text(content)


def test_index_lists_stream_title_and_digest_link(tmp_path):
    ws = Workspace.init(tmp_path)
    # 显式 title:索引展示人读名;默认 title 见 #103 另测
    rid = ws.add([_write(tmp_path, "会议实录.txt")], title="会议实录")
    _make_digest(ws, rid)

    md = build_stream_index(ws)

    assert "会议实录" in md
    assert f"{rid}/digest.md" in md


def test_index_has_table_header_and_class_label(tmp_path):
    ws = Workspace.init(tmp_path)
    ws.add([_write(tmp_path, "会议实录.txt")])

    md = build_stream_index(ws)

    assert "| 标题 | 类型 | digest |" in md
    assert "|---|---|---|" in md
    assert "观测" in md  # source_classes["stream"].label


def test_index_excludes_corpus(tmp_path):
    ws = Workspace.init(tmp_path)
    stream_rid = ws.add([_write(tmp_path, "会议实录.txt")], title="会议实录")
    corpus_id = ws.add(
        [_write(tmp_path, "业务白皮书.txt")], source_class="corpus", title="业务白皮书"
    )

    md = build_stream_index(ws)

    assert "会议实录" in md
    assert ws.read_manifest(stream_rid).title in md
    assert "业务白皮书" not in md
    assert corpus_id not in md


def test_index_with_no_stream_shows_placeholder(tmp_path):
    ws = Workspace.init(tmp_path)
    ws.add([_write(tmp_path, "业务白皮书.txt")], source_class="corpus", title="业务白皮书")

    md = build_stream_index(ws)

    assert "| 标题 | 类型 | digest |" in md  # 表头仍在
    assert "暂无" in md
    assert "业务白皮书" not in md


def test_write_creates_meetings_file(tmp_path):
    ws = Workspace.init(tmp_path)
    rid = ws.add([_write(tmp_path, "会议实录.txt")], title="会议实录")
    _make_digest(ws, rid)

    path = write_stream_index(ws)

    assert path == ws.references_dir() / "MEETINGS.md"
    assert path.read_text() == build_stream_index(ws)
    assert "会议实录" in path.read_text()


def test_index_default_title_is_yyyymmdd_hh(tmp_path):
    """#103:未指定 title 时索引行用默认时间格式展示名。"""
    import re

    ws = Workspace.init(tmp_path)
    rid = ws.add([_write(tmp_path, "会议实录.txt")])
    title = ws.read_manifest(rid).title
    assert re.fullmatch(r"\d{8}-\d{2}", title)

    md = build_stream_index(ws)

    assert title in md
    assert "会议实录" not in md  # 默认不再用文件 stem


def test_step_generates_stream_index(tmp_path):
    ws = Workspace.init(tmp_path)
    rid = ws.add([_write(tmp_path, "会议实录.txt", "会议内容")], title="会议实录")

    step(ws, StubProvider())

    index = ws.references_dir() / "MEETINGS.md"
    assert index.exists()
    assert "会议实录" in index.read_text()
    assert rid  # 保持 rid 引用,防 unused
