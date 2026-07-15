# 64 — 统一参考摄入：本地路径 + 可选 copy；重命名语义自洽

- Issue: [#64](https://github.com/xforce-io/kairo/issues/64)
- 分支: `feat/64-unified-source-ingest`
- 状态: 已实现(TDD),待合入
- 日期: 2026-07-15
- 关联: #48 title 重命名、#24 corpus 目录指针、#44 attach 自包含

## 1. 目标

把「按路径 / 上传」收成统一能力：

```text
选源(路径 | 浏览器文件) → 可选 copy 进 workspace → add → form.location + hash
```

重命名保持 **只改 title**，与 location / ref_id / 副本文件名正交。

## 2. 能力模型

| 标志 | 行为 |
|------|------|
| `copy=False`（默认） | 只登记路径指针 |
| `copy=True` | 先拷到约定目录，再登记**副本**路径 |

| 场景 | copy 目标 |
|------|-----------|
| 新参考 / 无既有 ref_id | `.kairo/uploads/` |
| 追加到已有 `ref_id`（含 attach） | `references/<ref_id>/` |
| 目录 + corpus | **禁止** copy；仅目录指针 |
| 浏览器选文件 | Web 层先写入 uploads/ref 目录（等价 copy） |

不新增 source_class / form role。

## 3. Core API

```text
Workspace._copy_into(src, dest_dir) -> Path   # 同名冲突加 -1/-2
Workspace.add(..., copy: bool = False)
```

`copy=True` 且任一项为目录 → `AddError` 友好中文。

## 4. CLI

```text
kairo add <path> [--copy] [--corpus] [--id] [--role]
```

## 5. Web

- 添加参考对话框：路径表单增加「复制到工作区」checkbox（`name=copy`）
- 选文件表单：仍 multipart，服务端写入 uploads（必 copy）
- attach 路径：`add(..., ref_id=..., copy=True)`（与现自包含行为一致，走统一 API）
- i18n：copy 标签；rename 文案强调「显示名」

## 6. 重命名不变量（#48 强化）

- `set_title` → 仅 `manifest.title`
- 不改 ref_id、目录、forms[].location、uploads 文件名
- copy 使用**源文件名**（冲突后缀），永不取 title
- 测试：copy 后 set_title，location 字节级不变

## 7. 测试

- 单元：指针 / copy 落点；目录+copy 错误；title⊥location
- CLI：`--copy`
- Web：path+copy 表单字段；path copy 后文件在 uploads；rename 不改 location
- 回归：既有 add/attach/corpus/rename

## 8. 非目标

整树 deep copy、改 ref_id、远程 URL、批量迁移旧指针。
