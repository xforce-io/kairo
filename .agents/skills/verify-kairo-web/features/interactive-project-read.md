# #400 交互式 Project 读取（CLI）

本票无 Console 页面。Drive **只走正式 CLI**，不打开 Web，不碰现网 `~/kairo`，不触发 provider / LLM Run。S1–S3 与 issue 验收 1:1。

## 入口（全部要走）

1. `kairo project context PROJECT --root ROOT`
2. `kairo project read PROJECT SOURCE --root ROOT`
3. `kairo project read-url PROJECT --root ROOT URL`
4. `kairo project record create|resume|end|show|input …`
5. 带 `--run` 的 `read-url`（#392 对照，不替代本票 S1）

**不走：** 任何 `/projects/…` Console 页；`kairo task run`；Topic `step` / `run` / `re-step`。

## Fixture

scratch serve root。1 个 Project、1 个可读登记 Data Source（正文含 1 条 stub 可识别一跳 URL）。Reader 用可计数 stub，禁止真网。进行中 Task Run 仅用于 S2 对照与 S3 已结束样本。

## 路径 → 可判定结果

| # | 路径 | 可判定结果 | 依赖 |
|---|---|---|---|
| S1 | 无 `--run`/`--record`：读 1 个登记源，再 `read-url` 其正文中 1 个受支持一跳外链 | 两份 JSON `ok`；均有 `content`、来源、`version`；`input_id` 为 `null`；Task Run 列表条数不变；过程无 `step`/`run`/`re-step` | CLI + stub Reader |
| S2 | `record create` → `--record` 读取 → 中断 → `resume` 同一 `rec-` → `end` → `show`；另对进行中 Task Run `read-url --run` | `show` 含已记录 `input_id`；中断前后同一 `record_id`；未借用历史 Task Run；该 Task Run 冻结输入仍有效 | CLI |
| S3 | 分别传入不存在、已结束、不属于当前 Project 的 `--run` 或 `--record` | 失败 JSON 有 `code`、`retryable`、`next`；无 `content`、无新 `input_id`；按 `next` 能走通 S1 或 S2 | CLI |
| S3-I | 同上失败路径（Integration） | 外部 Reader 调用次数为 0；不作为 CLI 观察点 | stub 计数 |
| 回归 | 进行中 Task Run 的 `read-url --run` | 与 #392 一致：记账 `inp-`、不 `add_datasource` | CLI |

## 已知不做

- 不 Drive Console
- 不为 S1–S3 触发 LLM / Task agent
- 不把内部目录或私有 Reader 当过线证据
