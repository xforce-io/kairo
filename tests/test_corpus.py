"""#24:corpus 概念归属地 kairo/corpus.py 的单元测试。

分两层:
- 树助手:walk_files / tree_hash / render_tree(纯函数,目录树)。
- CorpusRef + collect/reference_section/read_dirs/stamp(file/tree 统一抽象)。
"""

from pathlib import Path

from kairo import corpus
from kairo.models import Form, Manifest
from kairo.workspace import Workspace


def _tree(root: Path) -> None:
    """造一棵带隐藏项的目录树。"""
    (root / "平台" / "灵犀系统").mkdir(parents=True)
    (root / "平台" / "灵犀系统" / "术语表.md").write_text("灵犀系统")
    (root / "平台" / "灵犀系统" / "接口说明.md").write_text("接口")
    (root / "方法论").mkdir()
    (root / "方法论" / "营养评估流程.md").write_text("评估")
    (root / ".DS_Store").write_text("junk")  # 隐藏文件
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref")  # 隐藏目录下


# ---- 树助手 ----


def test_walk_files_sorted_relative_skips_hidden(tmp_path):
    _tree(tmp_path)
    files = corpus.walk_files(tmp_path)
    assert files == sorted(files)  # 排序
    assert all(not p.is_absolute() for p in files)  # 相对 root
    names = {str(p) for p in files}
    assert "方法论/营养评估流程.md" in names
    assert "平台/灵犀系统/术语表.md" in names
    # 隐藏项跳过
    assert not any(".DS_Store" in str(p) or ".git" in str(p) for p in files)


def test_tree_hash_sensitive_to_edit_add_remove(tmp_path):
    _tree(tmp_path)
    h0 = corpus.tree_hash(tmp_path)
    # 改:改一个文件内容 → 翻戳
    (tmp_path / "方法论" / "营养评估流程.md").write_text("评估v2")
    h_edit = corpus.tree_hash(tmp_path)
    assert h_edit != h0
    # 增:加一个文件 → 翻戳
    (tmp_path / "方法论" / "新增.md").write_text("新")
    h_add = corpus.tree_hash(tmp_path)
    assert h_add != h_edit
    # 删:删一个文件 → 翻戳
    (tmp_path / "方法论" / "新增.md").unlink()
    assert corpus.tree_hash(tmp_path) == h_edit  # 删回到增之前


def test_tree_hash_ignores_hidden(tmp_path):
    _tree(tmp_path)
    h0 = corpus.tree_hash(tmp_path)
    (tmp_path / ".DS_Store").write_text("changed junk")  # 改隐藏项不应翻戳
    assert corpus.tree_hash(tmp_path) == h0


def test_render_tree_indented(tmp_path):
    _tree(tmp_path)
    out = corpus.render_tree(tmp_path, corpus.walk_files(tmp_path))
    assert "术语表.md" in out and "营养评估流程.md" in out
    # 嵌套文件缩进比顶层目录深
    line_term = next(ln for ln in out.splitlines() if "术语表.md" in ln)
    line_top = next(ln for ln in out.splitlines() if ln.strip() == "平台/")
    assert len(line_term) - len(line_term.lstrip()) > len(line_top) - len(
        line_top.lstrip()
    )


# ---- CorpusRef + collect / reference_section / read_dirs / stamp ----


def _add_file_corpus(ws: Workspace, tmp_path: Path, name="wp.md", body="白皮书"):
    p = tmp_path / name
    p.write_text(body)
    return ws.add([p], source_class="corpus")


def _add_tree_corpus(ws: Workspace, dir_name="corpus_docs"):
    """直接写一条 corpus_tree manifest(模拟 add <dir> --corpus 的产物)。"""
    root = ws.root / dir_name
    _tree(root)
    ref_id = f"2026-06-23-{dir_name}"
    (ws.references_dir() / ref_id).mkdir(parents=True)
    man = Manifest(
        id=ref_id,
        title=dir_name,
        source_class="corpus",
        forms=[Form(role="corpus_tree", location=dir_name, hash=corpus.tree_hash(root))],
    )
    ws.write_manifest(ref_id, man)
    return ref_id, root


def test_collect_distinguishes_file_and_tree(tmp_path):
    ws = Workspace.init(tmp_path)
    rf = _add_file_corpus(ws, tmp_path)
    rt, _ = _add_tree_corpus(ws)
    refs = {r.ref_id: r for r in corpus.collect(ws)}
    assert refs[rf].kind == "file"
    assert refs[rt].kind == "tree"


def test_collect_skips_fold_class_stream(tmp_path):
    ws = Workspace.init(tmp_path)
    s = tmp_path / "m.txt"
    s.write_text("会议")
    rs = ws.add([s])  # stream(默认 fold)
    assert rs not in {r.ref_id for r in corpus.collect(ws)}


def test_read_dirs_file_parent_tree_root(tmp_path):
    ws = Workspace.init(tmp_path)
    _add_file_corpus(ws, tmp_path)  # 文件在 tmp_path 下
    _, root = _add_tree_corpus(ws)
    dirs = set(corpus.read_dirs(corpus.collect(ws)))
    assert tmp_path in dirs  # file 型授读其父目录
    assert root in dirs  # tree 型授读目录根


def test_stamp_changes_when_tree_file_edited(tmp_path):
    ws = Workspace.init(tmp_path)
    _, root = _add_tree_corpus(ws)
    s0 = corpus.stamp(corpus.collect(ws))
    (root / "方法论" / "营养评估流程.md").write_text("评估改了")
    assert corpus.stamp(corpus.collect(ws)) != s0


def test_reference_section_has_hint_and_tree(tmp_path):
    ws = Workspace.init(tmp_path)
    _add_tree_corpus(ws)
    section = corpus.reference_section(ws, corpus.collect(ws))
    assert "基线" in section  # 类 label/hint
    assert "术语表.md" in section  # 树里嵌套文件可见
    assert "营养评估流程.md" in section


def test_corpusref_file_render(tmp_path):
    ws = Workspace.init(tmp_path)
    rf = _add_file_corpus(ws, tmp_path, name="wp.md")
    ref = next(r for r in corpus.collect(ws) if r.ref_id == rf)
    out = ref.render()
    assert out.startswith("- ") and "wp.md" in out


def test_collect_includes_document_binary_pointer(tmp_path):
    """#88:纯 document 基线(无 source_text)也进 collect,路径为原件。"""
    fixtures = Path(__file__).parent / "fixtures"
    ws = Workspace.init(tmp_path)
    dst = ws.root / "bp.pptx"
    dst.write_bytes((fixtures / "sample.pptx").read_bytes())
    rid = ws.add([dst], source_class="corpus")
    refs = {r.ref_id: r for r in corpus.collect(ws)}
    assert rid in refs
    assert refs[rid].kind == "file"
    assert refs[rid].path.resolve() == dst.resolve()
    # 字节 hash stamp,不因二进制崩
    assert corpus.stamp([refs[rid]])
