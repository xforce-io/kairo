# #436 统一自动 note

基线：[L2 v1](../../../../docs/design/436-unified-generated-notes.md) §11；产品确认来自 2026-10-01 用户「按此范围新建 Issue，继续端到端」。本文件取代 #423 重复追加/失败 exit 0 和 #433 所有 Ref 一律落 notes 的旧规则。保留 #431 置顶与 #411 人工 notes 的其它行为。

## Fixture

仅 scratch serve root。用 `Workspace.init`、`create_tag/set_include_tags` 建 energy Topic；用 `Workspace.add`、`add_tag` 建本地/global stream，另建 corpus、无 digest、blocked digest。digest 用确定性 StubProvider 的正式处理生成；人工 notes 用 `add_note`，置顶用 `pin_note`。模型替身只在命令 fixture 注入，不调用真实供应商。失败/空/801 字符替身及并发由 `tests/test_unified_generated_notes_436.py` 的正式 CLI 驾驶和 Integration 覆盖。浏览器服务不配置真实 Run，浏览器只走阅读入口。

## 入口与可判定结果

| ID | 入口、动作 | 必需结果 |
|---|---|---|
| S1.A1 | 正式 CLI run --ref、step、run-view --yes、retry-ref；本地与 global，再执行 | 成功各确保一条 generated≤800；重复0条；actual home 正确，旧 notes/置顶不改 |
| S2.A1 | notes status --ref/--topic；失败后 notes generate REF --home | 失败非零、持久化且脱敏、默认文本可读；generate 不合格材料非零、无半条；成功仅写 note 与生成状态，其它产物不变 |
| S2.A2 | 同 Ref 并发与 running 中断恢复 | 最多1条；已落 note 但状态未落可恢复；Windows换行 digest 同 hash 失败收敛；不可读状态保留诊断并等待显式重试；不重复模型调用 |
| S3.A1 | /w/energy 左侧点击本地/global 无 note Ref、有 note Ref；显式 notes 空态；损坏 notes | 每 Ref 无 note 落 digest；有 note 落原 pin 预览；错误可见不伪装空；Ref 详情能读诊断 |
| S3.A1-public | 同路径 public-read 服务 | 无追加、置顶、重试入口，无内部诊断；GET 不写文件 |
| S4.A1 | backfill 无 apply、apply 单项失败、恢复、再次 apply | 默认0写0模型；仅合格缺失；失败继续且非零；批量共享 Ref 在 clean 分支仍报告既存失败；再次0条；旧notes/置顶不改 |

S3 关联 #431 已置顶全文与追加后保持原置顶、#411 人工 notes 详情/列表阅读。必须真实浏览器点击，HTTP测试只作辅助。证据按 handbook 保存 before/after PNG、snapshot 与原始 CLI 输出。

边界回归：正式 `step --understanding-only` 仅报告综合结果；历史 note 失败不改变成功退出码，不调用 note 模型、不改生成状态；综合失败仍非零并保留旧正文。由 `tests/test_run_single_material_419.py` 的成功、无效溯源、超时三种确定性 CLI 用例证明。

重试回归：`retry-ref A` 内部调和产生的其它 Ref 新 note 失败必须非零；未变化的无关历史失败不误判。覆盖首次失败、历史失败不变、历史失败在新 digest 上再次失败，目标 A 的 note 保持成功且无重复。

## 发布后闭环（不冒充预发布证明）

S5.A1：合入并取得部署批准后，用现有 scripts/serve deploy 和同 checkout 的 uv tool install 更新，120秒内核对模块路径/哈希/实际 SHA、页面 GET 与浏览器。
S6.A1：真实能源梳理先备份与 backfill 预览，apply 后缺失0/失败0、旧notes不变；刚总页面可读；仅 PID 已死且 run lock 可独占才清残留记录。
S7.A1：备份旧 understanding/state，正式 re-step understanding.md 后验证 provenance、无阻塞和合格未融入0；失败保留旧正文。

这三项必须在真实发布/数据环境留下单独结果，部署前为 not_run。不得把 scratch 或模型替身当成通过；Issue 保持未完成直至真实结果通过。
