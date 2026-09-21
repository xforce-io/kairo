# 持续有界综合（#408）

## 入口

- CLI：scratch Topic 中普通 `kairo run`、`kairo status`；确定性 provider 注入只用于验证，不调用真实 LLM。
- Web：`/w/<slug>` 主按钮、`/w/<slug>/target?path=understanding.md` 及运行失败摘要。

## Fixture

复用 `tests/test_bounded_understanding.py` 的 `Workspace.init`、`add_digest`、`CompactProvider`。fixture 脚本放 `/tmp`，scratch root 由 mktemp 创建，禁止现网目录与 8787。

`CompactProvider` 实际读取授读文件中的 fixture 事实，并在输出保留对应来源；它只证明批次、预算、保旧和恢复机制，不证明真实模型语义质量。

## 路径 → 可判定结果

| Story | 路径 | 可判定结果 |
|---|---|---|
| S1 | CLI 连续 5 轮新增 digest → run → Web 打开结论 | 累计材料 >20k，每轮正文 12k～20k，全部 fixture 关键事实及来源可读，无 re-step |
| S2 | CLI 至少 3 批，第 2 批超时 → Web 查看保留正文 → run 恢复 | 已完成批次保留，失败批次未误记，恢复后全部 folded，无重复提交已成功批次 |
| S3 | CLI 注入超预算/非法来源/骤缩/手改/超时 → Web 查看保护原因 | 超预算最多修订 1 次，旧字节与 folded 不变；人工保护不显示普通运行可恢复的提示 |
| S4 | CLI 旧超长容量阻塞 → status/run → Web 阅读；另走 Web 容量重试按钮 | CLI 普通 run 恢复到 ≤20k；Web 按钮可用且可完成普通重试；手改不被绕过；正常无 Δ 综合调用 0 次 |

## Evidence

冻结候选后运行 `env -u KAIRO_SERVE_ROOT uv run --all-extras pytest tests/test_bounded_understanding.py tests/test_unified_run.py tests/test_web_tasks.py tests/test_provenance.py -q`，保留完整输出及 SHA。按上表驾驶 CLI 与浏览器，保存每条 S 的 before/after 截图、CLI 输出、状态片段到 `/tmp/kairo-verify/408/<sha>/`。

## Cleanup

关闭本次 scratch server 与浏览器 TaskSpace；保留证据及必要复核 fixture，不触碰现网。
