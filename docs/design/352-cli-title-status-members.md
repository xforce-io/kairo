# add/title/status 覆盖 global 成员展示名

- Issue: [#352](https://github.com/xforce-io/kairo/issues/352)
- 分支: `feat/352-cli-title-status-members`
- 状态: Approved
- 最后更新: 2026-09-09

## 1. 背景

链 [#352](https://github.com/xforce-io/kairo/issues/352)。从 serve root `kairo add --topic` 把 Ref 放进 global-home 并打 Tag，但 CLI 未暴露 `--title`，默认展示名为登记时刻 `YYYYMMDD-HH`。`kairo title` 只打开 Topic 仓本地 `references/`；Topic `status` 只遍历本仓 id。操作者只能手改 manifest。

## 2. 名词解释

N/A。Ref / Tag / include_tags / global-home 见 [docs/glossary.md](../glossary.md)。

## 3. 目标与非目标

- **目标**：`add --title`；有文件路径且未给 `--title` 时用 stem；`title` 可改 global-home Topic 成员；Topic `status` 列出 include_tags 成员及展示名。
- **非目标**：ingest；add 后自动 step；改 Tag 语义；改无 stem 且无 `--title` 的时钟默认；Web 改名 UI。

## 4. 能力

1. `kairo add` 增加 `--title`。
2. 未给 `--title` 且有文件/目录路径时，展示名 = 文件 stem 或目录名。
3. `kairo title REF_ID NAME` 在 Topic 上下文中解析 include_tags 成员（含 global-home）。
4. Topic `kairo status` 按 `topic_members` 列出成员，输出含 id 与展示名。

### 4.1 UI/UX

N/A。无新页面。

## 5. 思路与折衷

| 选择 | 放弃 |
|---|---|
| 接通已有 `title=` 参数 | 放弃继续时钟默认盖掉文件名 |
| status 复用 `topic_members` | 放弃 status 只列本仓 home |
| title 按成员解析 home | 放弃只在本地 `list_reference_ids` 查找 |

时钟函数 `default_reference_title` 保留，供无 stem 路径。

## 6. 架构

```
kairo add [--title] FILE --topic --root
        ↓ stem or --title or YYYYMMDD-HH
add_global_ref / Workspace.add(title=)
        ↓ stamp Tag
kairo title REF → topic_members / list_all_refs → resolve_open → set_title
kairo status → topic_members → 打印 id + title
```

主路径：add 文件 → title=stem → status 可见 → title 改名 → status 新名。

失败路径：缺 Tag / Ref 找不到 → 非零退出，不写半成。

## 7. 模块

| 模块 | 变化 |
|---|---|
| `cli.add` | `--title`；默认把路径 stem 传入 |
| `workspace._resolve_new_title` | 无显式 title 时可用 stem |
| `cli.title` | 按 serve catalog 解析成员 home |
| `cli.status` | 列 `topic_members` |

## 8. API/CLI

- `kairo add ... --title TEXT`（可选）
- `kairo title REF_ID NAME [--topic SLUG]`：REF 可以是当前 Topic 的 include_tags 成员
- `kairo status [--topic SLUG]`：列出成员，不限于本仓 `references/`
- 无新 HTTP 路径

## 9. 边界

- `--to` 追加 form 不改已有 title。
- 目录 add 无 `--title` 时用目录名。
- 同 id 多 home：`--topic` 限定；否则可诊断失败。
- 无路径 stem 且无 `--title`：仍 `YYYYMMDD-HH`。

## 10. 迁移/兼容/回滚

- 已有 Ref 的时钟 title 不回写。
- 回滚后 add 恢复时钟默认，title/status 恢复只认本仓。

## 11. 测试计划

- **Unit**：CliRunner：stem；`--title`；global title；status 含成员新名。时钟函数测试保留。改写「add 默认不是 stem」的 #103 用例。
- **E2E**：丢弃 serve+Topic 两次 add/title/status。

对上 S1–S3。

## 12. 开放问题

无。

## 13. 关联

- [#352](https://github.com/xforce-io/kairo/issues/352)
- [#269](https://github.com/xforce-io/kairo/issues/269)
