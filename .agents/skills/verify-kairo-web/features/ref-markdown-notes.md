# #410 Ref markdown notes 盖楼（CLI）

本票无 Console 新页（界面归 #411）。Drive **只走正式 CLI** `kairo notes add` / `list` / `show`。不碰现网 serve root，不触发 LLM / `step` / `run` / `re-step`。禁止扫整个 serve root 当过线证据。

## 入口

1. `kairo notes add <ref_id> --content - [--type] [--home] [--json] [--root]`
2. `kairo notes list (--ref | --topic) [--home] [--since] [--json] [--root]`
3. `kairo notes show <note_stable_id> | --ref REF_ID [--home] [--json] [--root]`

**不走：** Console、`kairo step` / `run` / `re-step`、改写 `understanding.md`。

## 路径 → 可判定结果

| # | 路径 | 可判定结果 | 依赖 |
|---|---|---|---|
| S1 | 已有 Ref（可无 digest）`notes add`，再 `list --ref` / `show` | list 条数 +1；show 正文、作者、时间、类型、稳定键一致；未给类型则为 insight | CLI |
| S2 | 三子命令 `--help` 与成功路径；失败：Ref 不存在 / 空正文 / 非法类型 / 缺 `--ref` 与 `--topic`；`--ref` 与 `--topic` 同时给 | 均有帮助；合法空 list 退出 0；错非 0、stderr 一句原因、不落半条；同时给为 `invalid_request` | CLI |
| S2-topic | `notes list --topic SLUG`（成员 Ref 上已有 note） | 只含该 Topic 成员 Ref 的 notes；非成员不出现 | CLI |
| S3 | 刚写入后 `list --ref --since`；无 notes 的 Ref 再 list | 有则至少 1 个 `note:` 稳定键；无则成功空 | CLI |
| S3-default | 省略 `--since` 的 `list --ref`（固定时钟：窗内一条、窗外一条） | 默认近 48h：只列出窗内；窗外不出现 | CLI |
| S4 | add 前后 `kairo status` 读该 Ref 已融入计数；`list --json` 的 `provenance`；无 notes 再读 | 已融入计数不变；有则 `provenance` 含 `kind=note` 与稳定键；无则数组空且字段不含 `note:`；`understanding.md` 不变；未自动综合 | CLI |

## 已知不做

- 不 Drive Console（#411）
- 不跑真实 compose / 不改 understanding
- 不把 notes 计为已 fold
- 不与 #401 / #402 / #404 混提交
