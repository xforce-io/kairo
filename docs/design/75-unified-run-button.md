# 75 — 运行交互收敛：单一主按钮

- Issue: [#75](https://github.com/xforce-io/kairo/issues/75)
- 分支: `feat/75-unified-run-button`
- 状态: 已实现(TDD)

## 决策
- 主按钮唯一「推进」：`run` = 有 blocked 则先 clear 再 step
- 运行中添加参考：**松** — 允许 + toast「下次运行才处理」
- 运行结束：结果摘要进 `#step-area`，OOB 刷新主按钮（不整页蒸发）

## API
- `POST /w/{slug}/run`、`GET /w/{slug}/run-summary`
- CLI `kairo run`
- `workspace_run_plan` / `run_workspace`
