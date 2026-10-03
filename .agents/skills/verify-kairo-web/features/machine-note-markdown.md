# #440 新的机器 note 用 Markdown 标出主次

Drive Ref 页与 Topic notes 预览。只在 scratch serve root 写入。不改已有生产 notes，不触发真实 LLM / `step` / `compose`。

## 入口

1. Ref 页：`/refs/{id}?home=energy`（卡片内强调与列表）
2. Topic notes 预览：`/w/{slug}/ref/{id}/notes`
3. 机器 note 写入：`append_generated_note` / `ensure_generated_note`（空、超长、无结构、同级列表超过 6 条被拒绝）
4. 人工追加：`notes add` 普通正文仍可写入

## 路径 → 可判定结果

| # | 路径 | 可判定结果 | 依赖 |
|---|---|---|---|
| S1 | 写入一条加粗总判断加 1 个列表主题的机器 note，打开 Ref 页和 Topic notes 预览 | notes 为 N+1；两页各 1 处强调、1 个列表项，不显示原始星号 | Console |
| S2 | 依次提交空正文、超过 800 字符、无 Markdown 结构、同级列表超过 6 条 | 四种都不落盘；notes 增加 0 | Console |
| S3 | 已有 1 条普通人工 note 时再追加一条普通人工 note | 旧正文改写 0 条；人工 note 增加 1 条 | Console |
| S4 | 查看 S1 卡片内的无序列表标记 | list-style 为 disc | Console |

## 已知不做

- 不改写已经落盘的 note
- 不重跑生产材料，不改详备纪要和综合
- 不重启生产 serve
