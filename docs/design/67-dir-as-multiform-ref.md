# 67 — 添加参考：目录 → 一条多形态 stream reference

- Issue: [#67](https://github.com/xforce-io/kairo/issues/67)
- 分支: `feat/67-dir-as-multiform-ref`
- 状态: 已实现(TDD),待合入
- 日期: 2026-07-15
- 关联: #44 多形态 digest、#64 路径+copy、#24 corpus 目录指针

## 1. 决策

**模式 B**：`添加参考` + 目录路径 → **1 条** stream ref，夹内合格文件全部挂为 forms（非一文件一条 ref）。

## 2. 行为

```text
add([dir], source_class≠corpus, copy=?)
  → title 默认=目录名
  → 一层扫描合格文件（roles 可识别扩展名）
  → copy=True：逐文件 copy 进 references/<id>/
  → copy=False：location=外置绝对路径
  → forms 按文件名排序；role=guess_role
```

| 入口 | 目录行为 |
|------|----------|
| stream `add <dir>` | 本 issue：多形态一条 |
| corpus `add <dir> --corpus` | 不变：corpus_tree 指针 |
| copy + corpus 目录 | 仍拒绝整树 copy |

## 3. 过滤

- 仅文件、非隐藏、非 `.DS_Store`
- 扩展名在 `roles_by_ext` ∪ 内置默认映射中
- 无合格文件 → AddError
- 不递归

## 4. Web / CLI

- Web 添加参考填目录：走同一 `add`
- CLI `kairo add <dir>` 成功建多形态 ref（原报错 → 有用行为）
- copy checkbox：逐文件物化进 ref 目录

## 5. 测试

- 目录含 m4a+png → 1 ref，多 forms
- copy 落 ref 目录；set_title 不改 location
- corpus 目录不走本路径
- 空目录错误
- CLI / Web 路径
