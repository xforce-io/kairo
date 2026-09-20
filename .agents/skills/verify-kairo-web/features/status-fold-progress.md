# #401 status 综合进度与单条融入（CLI）

本票无 Console 新页。Drive **只走正式 CLI** `kairo status`。不碰现网 serve root，不触发 LLM / `step` / `run`。

## 入口（全部要走）

1. `kairo status [--topic SLUG]`
2. `kairo status --json`
3. `kairo status --ref ID [--home HOME] [--target PATH] [--json]`

**不走：** Topic Web 页、Timeline、`kairo run`。

## Fixture

scratch Topic。S1：已融入 6、`last_major_folded` 对应 2、待处理 0、blocked 0。S2：另备 pending>0 与 blocked>0。S3：同一 Ref×活 target 准备四态。

## 路径 → 可判定结果

| # | 路径 | 可判定结果 | 依赖 |
|---|---|---|---|
| S1 | `kairo status` | 人读同时含「已融入 6」「全量综合后已增量融入 4」「待处理 0」；无未解释「A」；4 不出现在待处理位置 | CLI |
| S2 | 三类 Topic 的 `status --json` | `pending`/`folded`/`incremental_after_full_compose`/`blocked`/`blocked_reasons` 与同学人人读一致 | CLI |
| S3 | `--ref` 四态 | `state` 为 `folded_current`/`not_folded`/`folded_stale`/`digest_missing`；人读字面量 1:1；旧哈希不得报当前已融入 | CLI |

## 已知不做

- 不 Drive Console
- 不改 fold 算法
- 不与 #402 抢 CLI 主路径
