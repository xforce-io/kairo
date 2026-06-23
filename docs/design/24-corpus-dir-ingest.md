# corpus 目录摄入:`add <dir> --corpus` 目录指针 + corpus 能力收拢

- Issue: [#24](https://github.com/xforce-io/kairo/issues/24)
- 分支:`feat/24-corpus-dir-ingest`
- 状态:已实现(TDD;`corpus.py` + `workspace.add` 目录分支 + `ComposeRule` 委托收拢)

## 背景

corpus(基线参考资料)目前只能逐文件 `add`,从用户视角不合理:

1. **逐文件加反直觉** —— 一份基线常是一整个目录。
2. **拍平丢信息** —— 目录树本身携带分组/来源/层级语义,逐文件登记后这层结构没了。
3. `add <dir>` 现状直接吐 `IsADirectoryError` traceback(`Workspace.add` 对每个路径 `read_bytes()`,读目录即崩)。

### 关键洞察:corpus 不走 digest

`DigestRule.discover` 对 `fold=False` 的源分类(corpus)直接跳过 —— corpus **从不被 digest**。它是个**只读参考层**:`ComposeRule` 在 compose 时把每条 corpus 拼成一段「基线参考前言」(各类 `hint` + 文件清单),并通过 `read_dirs` 授予 agent 只读权限,**agent 按需自己 Read**。drift 用一个粗粒度 `corpus_stamp`(全 corpus 正文 hash)做 advisory 探测。

因此「多文件被塞成一条 reference 的 forms、digest 糊在一起」这个坑**对 corpus 根本不存在**。一个目录天然就是 corpus 想要的东西:**文件系统的树就是结构,实时、免维护**。

## 设计

### 1. 数据模型:corpus 引用从「单文件」推广到「单文件 ∪ 目录树」

目录树用一个新 `Form.role = "corpus_tree"` 表示(指针,非正文):

```yaml
# references/2026-06-23-营养师基线/manifest.yaml
id: 2026-06-23-营养师基线
class: corpus
forms:
  - role: corpus_tree        # 目录指针
    location: corpus_docs     # 相对 workspace 根的目录
    hash: <tree_hash>         # 全树指纹(见 §4)
    origin: added
```

- corpus 不 digest(`DigestRule` 跳过 `fold=False`)、不 ASR(role 非 `audio`),所以新 role **不触发任何流水线**,digest/asr/engine 侧零改动。
- `corpus_tree` 不进 `body_roles`,故不会被误当正文。

### 2. `add` 行为(`Workspace.add` + CLI)

- `kairo add <dir> --corpus`:检测到路径是目录 → 建**一条** reference,写单个 `corpus_tree` form,`location` 为相对目录路径,`hash = corpus.tree_hash(dir)`;`id` 默认 `{date}-{slug(目录名)}`,`title` 默认目录名。
- `kairo add <dir>`(无 `--corpus`):**友好报错**并非零退出 ——
  `目录摄入目前仅支持 corpus(加 --corpus);stream 请逐文件 add`。顺手堵掉原 `IsADirectoryError` traceback。
- 单文件 corpus(`add x.md --corpus`)与各 stream 行为**不变**。
- 不支持「目录与文件混在一次 `add`」:若参数含目录,要求单参数 + `--corpus`,否则报错。

实现要点:`Workspace.add` 现按 `f.read_bytes()` 算每个 form 的 hash;对目录分支改走 `corpus.tree_hash(dir)`,不读字节。

### 3. `ComposeRule` 瘦身为纯编排,corpus 知识全部出栈

删除 `ComposeRule` 中的 `_corpus_refs` / `_corpus_stamp` / `_corpus_reference_section` 及 inline 的 `read_dirs` 计算,改为委托 `kairo.corpus`。`_body_path`(当前仅 `_corpus_refs` 使用,纯 corpus 用途)整体迁入 `corpus.py`(file 型 `CorpusRef` 据此定位正文):

```python
refs = corpus.collect(self.ws)
section   = corpus.reference_section(self.ws, refs) if refs else ""
read_dirs = corpus.read_dirs(refs)
# 折叠完记账:
ts.corpus_stamp = corpus.stamp(refs)
```

`corpus_drifted` 亦改用 `corpus.stamp(refs)`。`ComposeRule` 仅保留 fold/compose 编排;**file 与 tree 两种 corpus 走同一条委托路径**,差异封装在 `CorpusRef` 内部。

> `_is_fold_class`(判断某 `source_class` 是否折叠)**留在原处**:它是 constitution 的通用语义(理论上可有其它 `fold=False` 类),不绑死 corpus。`corpus.collect` 消费它,不重新发明。

### 4. 新模块 `kairo/corpus.py` —— corpus 概念的高内聚归属地

corpus 的全部领域能力收进此模块。核心是 `CorpusRef`,两种形态实现同一组能力:

```python
@dataclass
class CorpusRef:
    ref_id: str
    title: str
    cls: str            # source_class
    path: Path          # file 型=正文文件绝对路径;tree 型=目录根绝对路径
    kind: str           # "file" | "tree"

    def read_dir(self) -> Path:        # 授读目录:file→path.parent;tree→path
    def stamp_input(self) -> str:      # 版本戳输入:file→正文文本;tree→tree_hash
    def render(self) -> str:           # 基线段:file→`- {title}: {path}`;tree→标题 + 缩进树
```

模块对外能力(`ComposeRule` 只调这些):

- `collect(ws) -> list[CorpusRef]` —— 扫 references,挑 `fold=False`(经 `_is_fold_class`),按 form 识别 file / tree。
- `reference_section(ws, refs) -> str` —— 整段「基线参考前言」(各类 `hint` + 各 ref `render()`),沿用现有前言措辞。
- `read_dirs(refs) -> list[Path]` —— `sorted({r.read_dir() for r in refs})`。
- `stamp(refs) -> str` —— corpus 版本戳 `hash(sorted(r.stamp_input()))`,供 `corpus_drifted` advisory。

tree 私有助手:

- `walk_files(root) -> list[Path]` —— 递归、排序、**跳过隐藏项**(名以 `.` 开头,如 `.DS_Store` / `.git`),返回相对 `root` 的路径。
- `tree_hash(root) -> str` —— `hash(sorted [(relpath, sha256(bytes)[:12])])`;文件增 / 删 / 改均翻戳。
- `render_tree(root, files) -> str` —— 按相对路径渲染缩进树(目录/文件分层)。

**全列出都授读**:树里所有文件(含 PDF / 图片等读不了的)都列、都授读;agent 自行决定 Read 什么(读不了的自然跳过)。不在此层做扩展名过滤。

### 数据流(目录 corpus)

```
add <dir> --corpus
  └─ Workspace.add: 建 ref + corpus_tree form (hash=tree_hash)

kairo step → ComposeRule.run
  ├─ refs = corpus.collect(ws)              # 含 tree 型 CorpusRef
  ├─ section = corpus.reference_section()   # hint + 缩进树,注入 persona
  ├─ read_dirs = corpus.read_dirs(refs)     # 目录根 → agent 只读授权
  ├─ agent 按需 Read 树内文件,校正专名 / 锚定事实
  └─ ts.corpus_stamp = corpus.stamp(refs)   # tree_hash 计入;改树内文件→drift advisory
```

## 非目标(YAGNI)

- **stream 的目录摄入**:语义不同(每文件应独立 fold 成一条观测事件),不在本 issue;`add <dir>` 无 `--corpus` 报错引导。
- **PDF / docx / xlsx 转文本**:仍是 [#15](https://github.com/xforce-io/kairo/issues/15);这里只「列出 + 授读」,转换不归本 issue。
- **树的深度 / 大小限制、增量 stamp**:文档树规模无需优化,全量 walk。

## 测试

- `tests/test_corpus.py`(新):`walk_files` 跳隐藏 + 排序;`tree_hash` 对增 / 删 / 改敏感;`render_tree` 缩进正确;`CorpusRef` file / tree 两形态的 `read_dir` / `stamp_input` / `render`。
- `tests/test_workspace.py`:`add <dir> --corpus` 建单条 `corpus_tree` 引用且 hash=tree_hash;`add <dir>` 无 corpus → 友好错误(异常 / 非零)。
- `tests/test_rules.py`:有目录 corpus 时 `reference_section` 含缩进树、`read_dirs` 含目录根;改树内文件后 `corpus_drifted` 为真;单文件 corpus 经同一路径仍正常。
- `tests/test_cli.py`:`init → add <dir> --corpus → add 一个 stream 文件 → step`(`KAIRO_STUB`),corpus 树**不产** `digest.md`,understanding/assessment 正常生成。

## 影响面

- 改:`src/kairo/workspace.py`(add 目录分支)、`src/kairo/rules.py`(ComposeRule 瘦身委托)、`src/kairo/cli.py`(add 目录无 corpus 的友好错误)。
- 新:`src/kairo/corpus.py`、`tests/test_corpus.py`。
- 不动:`engine.py` / digest / asr / history / models(除可能为 `corpus_tree` 加注释)。
