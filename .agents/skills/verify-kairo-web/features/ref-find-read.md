# #402 / #445 按标题或 `--in` 字段检索并读 digest/transcript（CLI）

无 Console 新页。Drive 只走正式 CLI `kairo ref find` / `kairo ref read`。不碰现网 root，不触发 LLM / `step` / `run` / `re-step`。禁止扫盘当过线证据。`#445` 只加字面 `--in`，无语义检索。

## 入口

1. `kairo ref find [TEXT] [--title] [--in title|digest|notes|transcript|all] [--day] [--topic] [--json] [--root]`（`--in` 默认 title，字面包含）
2. `kairo ref read --id [--home] --form digest|transcript [--json] [--root]`

## 路径 → 可判定结果

| # | 路径 | 可判定结果 | 依赖 |
|---|---|---|---|
| S1 | `--title`、`--day`、`--topic` 各至少一次；0 / 1 / 多条 | 筛选生效；多条 `count>1` 且不自动 read | CLI |
| S2 | 唯一命中 ≤3 次 CLI 取 digest 或 transcript | 含 `content` 与 `source`；覆盖 global 与 Topic home | CLI |
| S3 | 不存在 / 未生成 / 缺失 / 不可读 | `code` 为 `not_found`/`material_unavailable`/`read_failed`/`invalid_request`；无 `content`；形态不替换 | CLI |
| #445 S1 | `--in` 五值与 `--help`；非法值 | 合法枚举恰好 `title|digest|notes|transcript|all`；非法非 0 | CLI |
| #445 S2 | 省略 `--in` 与 `--in title`；既有 `--title` | 标题用例命中 id 集合一致 | CLI |
| #445 S3 | 字段含 S、标题不含 S | `--in title` 不中；对应字段 / `all` 命中 | CLI |
