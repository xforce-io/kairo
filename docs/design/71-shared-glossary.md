# 71 — 共享真名册（root / machine + workspace 覆盖）

- Issue: [#71](https://github.com/xforce-io/kairo/issues/71)
- 分支: `feat/71-shared-glossary`
- 状态: 已实现(TDD),待合入
- 日期: 2026-07-15
- 关联: #20、#69

## 分层

| 层 | 路径 | 写入 |
|----|------|------|
| machine | `~/.config/kairo/glossary.yaml` | 文件手改 / 预留；注入用 |
| root | `<serve-root>/glossary.yaml` | Web「公共」编辑 |
| workspace | `constitution.yaml` → `glossary` | Web「本工作区」 |

合并注入顺序：**machine → root → workspace**，同名后者覆盖。

CLI：`root` 取 `workspace.parent / glossary.yaml`（多 workspace 根布局）。

## 文件格式

```yaml
- name: 天溯
  note: 公司主体
  aka: []
  tags: [org]
```

亦支持 `{entries: [...]}` 包装。

## Web

真名册面板两段：公共（root）/ 本工作区。POST/DELETE 带 `scope=shared|workspace`。

## 非目标

深分类树、按 tag 过滤注入、自动 re-step、权限。
