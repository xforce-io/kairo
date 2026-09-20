# #402 按标题／发生日／Topic 检索并读 digest/transcript（CLI）

无 Console 新页。Drive 只走正式 CLI `kairo ref find` / `kairo ref read`。不碰现网 root，不触发 LLM / `step` / `run` / `re-step`。禁止扫盘当过线证据。

## 入口

1. `kairo ref find [--title] [--day] [--topic] [--json] [--root]`
2. `kairo ref read --id [--home] --form digest|transcript [--json] [--root]`

## 路径 → 可判定结果

| # | 路径 | 可判定结果 | 依赖 |
|---|---|---|---|
| S1 | `--title`、`--day`、`--topic` 各至少一次；0 / 1 / 多条 | 筛选生效；多条 `count>1` 且不自动 read | CLI |
| S2 | 唯一命中 ≤3 次 CLI 取 digest 或 transcript | 含 `content` 与 `source`；覆盖 global 与 Topic home | CLI |
| S3 | 不存在 / 未生成 / 缺失 / 不可读 | `code` 为 `not_found`/`material_unavailable`/`read_failed`/`invalid_request`；无 `content`；形态不替换 | CLI |
