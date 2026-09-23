# #423 单条 run 默认生成一条机器 note

无新 Console 页面（#411 不改）。Drive 走正式 CLI `kairo run --ref`，再用 `kairo notes list` / `show` 核对。不碰现网 serve root。模型轮次用确定性替身，不要求真实供应商。不跑 `kairo step`，不改 `understanding.md`。

## 入口

1. `kairo run --ref <ref_id>`（既有单条入口）
2. `kairo notes list --ref` / `kairo notes show --ref`（既有读取；类型与作者原样可见）

**不走：** 新页面、新按钮、`notes add --type generated`、无选项 `kairo step`、回填其它 Ref。

## 路径 → 可判定结果

| # | 路径 | 可判定结果 | 依赖 |
|---|---|---|---|
| S1 | 一条可处理 Ref 执行 `kairo run --ref`；替身返回不超过 800 个 Unicode 字符的非空正文 | exit 0；该 Ref notes 条数 +1；新条类型 `generated`、作者 `machine`、正文与替身一致且 ≤ 800；详备纪要与执行前成功结果一致；`understanding.md` 与折入计数不变 | CLI |
| S2 | 同一条转写和纪要能完成，替身分别给出失败、空白、超过 800 字符 | 三种情况 exit 都为 0；notes 条数增加 0；不留下半条；详备纪要仍在；不自动再叫一次模型 | CLI |
| S3 | 主题内另有未指定的历史 Ref；只对一条执行 `kairo run --ref` | 只有被 `--ref` 的那一条尝试新增；其余 Ref 的 notes 条数增加 0；understanding 更新次数为 0 | CLI |
| S1-again | 对同一条再执行一次成功的 `kairo run --ref` | 再追加一条 `generated`；已有人工 note 与上一条机器 note 的正文与类型不变 | CLI |
| S1-human | `kairo notes add --type generated` | 非 0；`invalid_request`；notes 条数增加 0 | CLI |

## 已知不做

- 不改 #411 的四类人工类型选择
- 不截断超长正文再落盘
- 不把本条 note 折入 understanding
- 不回填历史 Ref
- 不与 #422 混在同一次验收
