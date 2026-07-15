# 69 — Web 编辑领域真名册（glossary）

- Issue: [#69](https://github.com/xforce-io/kairo/issues/69)
- 分支: `feat/69-web-glossary-ui`
- 状态: 已实现(TDD),待合入
- 日期: 2026-07-15
- 关联: #20 glossary

## 目标

右栏 Step 下「真名册」入口：列表 / 追加 / 删除；写回 `constitution.yaml` 的 `glossary`。

## API

- `GET /w/{slug}/glossary` → `#meta` 片段
- `POST /w/{slug}/glossary` form: name, note?, aka?（逗号分隔）
- `POST /w/{slug}/glossary/{index}/delete`

## Core

`Workspace.write_constitution` / `add_glossary_entry` / `remove_glossary_entry`

## 非目标

编辑已有条目就地改、自动 re-step、全局机器级 glossary。
