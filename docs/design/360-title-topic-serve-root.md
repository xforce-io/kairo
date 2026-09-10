# title --topic 按 serve root 解析全局库成员

- Issue: [#360](https://github.com/xforce-io/kairo/issues/360)
- 分支: `feat/360-title-topic-serve-root`
- 状态: Approved
- 最后更新: 2026-09-10

## 1. 背景

链 [#360](https://github.com/xforce-io/kairo/issues/360)。`_open_ws(--topic)` 在未设 `KAIRO_SERVE_ROOT` 时把 cwd 当 serve root。cwd 已是 Topic 目录时变成 `<cwd>/<slug>`，报「不是 kairo Topic」。serve-root `add --topic` 写入全局库后立刻 `title --topic` 也可能找不到刚 stamp 的成员。

## 2. 名词解释

N/A。Topic / Ref / 包含规则 / global-home 见 [docs/glossary.md](../glossary.md)。

## 3. 目标与非目标

- **目标**：`--topic` 相对 serve root 打开 Topic；`title --topic` 能改该 Topic 成员（含全局库 home）的展示名。
- **非目标**：Web 改名 UI；改变 `add` 的 home 规则；删 Ref；ingest 技能。

## 4. 能力

1. `_open_ws` / `_topic_dir`：env → 当前 Topic 的 `serve_root_of` → cwd 当 serve。
2. `title` 用当场 `list_all_refs` 作为 `topic_members` catalog。

### 4.1 UI/UX

N/A。无页面。

## 5. 思路与折衷

| 选择 | 放弃 |
|---|---|
| cwd 是 Topic 时上溯 serve root | 放弃要求调用方必须 `cd` serve root 或必须设 env |
| title 每次现扫 catalog | 放弃依赖可能过期的 bound catalog |

## 6. 架构

```
title --topic SLUG
  → serve = env | serve_root_of(cwd Topic) | cwd
  → open serve/SLUG
  → topic_members(fresh list_all_refs)
  → resolve_open(home, id) → set_title
```

主路径：全局库成员 + cwd=Topic + `--topic` → 改全局库 manifest title。

失败路径：slug 不存在 → 不是 kairo Topic（路径为 `serve/slug`）；id 非成员 → reference 不存在。不写半成。

## 7. 模块

| 模块 | 变化 |
|---|---|
| `cli._open_ws` / `_topic_dir` | serve root 解析 |
| `cli.title` | 传入 fresh catalog |

同用 `_open_ws` 的 step/rm-ref 等一并受益。

## 8. API/CLI

- `kairo title REF_ID NAME [--topic SLUG]`：`--topic` 相对 serve root，不相对 cwd。
- 无新 HTTP 路径。

## 9. 边界

- 同 id 多 home：`--topic` 限定到该 Topic 成员；多于一条仍报不唯一。
- 不设 `--topic` 且 cwd 非 Topic：仍按 `list_all_refs` 全库 id（不唯一则失败）。

## 10. 迁移/兼容/回滚

- 原先 cwd=Topic 且 `--topic` 的调用从必失败变为成功。
- 已设 `KAIRO_SERVE_ROOT` 的调用语义不变。
- 回滚即恢复「cwd/slug」。

## 11. 测试计划

- **Unit**：cwd=Topic、无 env、`--topic` 改 tagged 全局库 title；serve-root `add --topic` 后立刻 `title --topic` 成功且单 home。
- **E2E**：N/A。CLI 单测覆盖 S1/S2。

## 12. 开放问题

无。

## 13. 关联

- [#360](https://github.com/xforce-io/kairo/issues/360)
- [#352](https://github.com/xforce-io/kairo/issues/352)
- [xforce-io/alfred#225](https://github.com/xforce-io/alfred/issues/225)
