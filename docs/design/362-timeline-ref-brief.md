# #362 — 列表每条 Ref 补一句话 brief

- Issue: [#362](https://github.com/xforce-io/kairo/issues/362)
- L1: [提案](https://github.com/xforce-io/kairo/issues/362#issuecomment-5645317895)（待批）
- 分支: `feat/362-timeline-ref-brief`
- 状态: 待 L1 审批
- 日期: 2026-09-12

本文件是 #362 的详细设计唯一事实源。Issue 只保留摘要与本链接。

## 1. 背景

[#362](https://github.com/xforce-io/kairo/issues/362)。[#287](https://github.com/xforce-io/kairo/issues/287) 把 Timeline 列表定为按发生日分组的一条时间流，每行只有 `REF / 标题 / Tag / 元信息`。行是四列网格且标题列为 `1fr`，短标题在 1024px 窗口下留出约 380px 空白。现网 118 条 Ref 中 96 条已有 digest，但列表上无法区分内容，判断一条资料讲什么必须点进详情。

digest 正文不能直接当概述来源。抽样最近 8 份现网 digest：过程句与 `# ` 标题粘在同一行（例如「先读完全部必读材料，再按议题写纪要。# 数仓 OOM 复盘、业务配合与 OLAP 选型纪要」），`strip_process_preamble` 的 `(?m)^#{1,6}\s+\S` 要求标题行首，因此 8 份全部抽不出标题；退而取首段则长度在 21–143 字之间，且个别首段本身就是过程句。

## 2. 名词解释

brief 已入[名词表](../glossary.md)：一条 Ref 的一句话概述，由 digest 按契约产出并存于该 Ref 的 manifest；缺失即不展示，不从正文派生。Ref、digest、Timeline、manifest 见名词表。本设计不新增其它术语。

## 3. 目标与非目标

### 3.1 目标

1. manifest 可存一条 brief，产出路径唯一，缺失即不展示。
2. Timeline 列表在标题下方独占一行展示 brief，标题与 brief 都不截断。
3. digest 成功后自动为该 Ref 产出 brief；brief 失败不影响已落盘的 digest。
4. 存量有 digest 无 brief 的 Ref 可由一条显式命令补齐，且不改写 digest。

### 3.2 非目标

- 从 digest 正文做启发式派生、位置猜测或任何回退兜底。
- 日历视图右侧 pane、CLI 人读时间轴、Ref 详情页、Dashboard、Topic 侧栏展示 brief。
- brief 的 Web 手改入口。
- 改 digest 正文结构、fold / compose 行为，或 Timeline 的分组与排序。
- 给无 brief 的行补占位行或占位文案。

## 4. 能力

### 4.1 UI/UX

列表行由一行网格改为两行：第一行仍是 `REF / 标题 / Tag / 元信息`，第二行让 brief 从标题列跨到行尾。brief 单行、超长省略号截断、字号 13px、`var(--muted)`。

| 状态 | 列表 | 日历 |
|---|---|---|
| 成 | 标题下第二行显示 brief；标题与 brief 均不截断 | 不显示 brief，与现网一致 |
| 无 brief | 只有标题一行，行高与现网一致（49px），不留占位、不写占位文案 | 同左 |
| 超长 | brief 单行省略号截断，标题不受影响 | N/A |
| 不做 | 占位行、hover 展开、多行 clamp | 给窄 pane 加第二行 |

排版稿与实测：`docs/design/362-timeline-ref-brief/`（`list-brief-2line.html` 为定稿，`list-brief.html` 为被否的同行方案）。1010px 窗口实测：定稿 brief 可用宽度 905px，9 行标题与 brief 截断均为 0（最长 54 字仍完整）；同行方案 brief 只有 301–433px，8 条截 6 条。行高 49px → 68px，一屏条数少约三分之一，已拍板接受有 brief 与无 brief 的行高不齐。

brief 写作契约：一句话、≤50 字、结论放前半句。905px 在 13px 下约容 65 个汉字，留足余量。

窄屏（≤860px）沿用现有单列堆叠，brief 落在标题下方并允许折行。

公开只读面 `/timeline` 与控制台共用同一模板，brief 随之可见，与 digest 在公开面已可见的口径一致。

## 5. 思路与折衷

选择：brief 由一次独立的短 provider 调用产出，输入是该 Ref 已落盘的 digest，输出直接写 manifest。产出与校验只有一条代码路径，digest 后处理与批量补齐共用它。

放弃 A：digest 正文首行契约（要求首行或 H1 后紧跟一行 blockquote），落盘时按位置抽取。现网 8/8 抽样的过程句都与 `# ` 标题同行，位置型契约在真实 provider 输出上不成立；改成扫全文找锚点，等于再养一套启发式解析，与「不猜」相悖。

放弃 B：渲染时从 digest 的 H1 或首段派生，不落存数。抽样里 H1 有的很好用、有的是「记忆纪要」这类空话、有的抽不出来，首段长度 21–143 字不等；要把这些捋平必须堆回退链。

放弃 C：把 brief 挂进 `state.products` 走 step 与 staleness。全局库 Ref 的 digest 依赖包含它的 Topic step，而 Timeline 是 serve root 全域视图，用 Topic 成员关系驱动 brief 会漏掉未被任何 Topic 包含的 Ref。manifest 内的 `brief_hash` 自带过期判定，够用且不扩 state 契约。

放弃 D：brief 与标题同行。见 4.1 实测，宽度不够，两者互相截断。

## 6. 架构

```mermaid
flowchart LR
  Digest[DigestRule 写 digest.md] --> Gen[generate_brief]
  Cmd["kairo brief(命令)"] --> Gen
  Gen --> Agent["_run_agent → brief.txt"]
  Agent --> Check[校验:非空/单行/≤50 字]
  Check --> Man["manifest.brief + brief_hash"]
  Man --> Scan[scan_timeline]
  Scan --> List["Web 列表第二行"]
```

主路径：digest 落盘 → 短调用产出一句话 → 校验 → 写 manifest → Timeline 扫描时随 manifest 一起读出 → 列表渲染第二行。

超长纠正：现网实测模型会无视字数上限（出现 148 字一段的输出）。只对「超长」这一种违约做**一次**确定性纠正——把实测字数与上限回给模型并要求删掉次要议题重写；第二次仍超长即失败。空输出与多行输出不重试。放宽上限不可取：第二行会变成被截断的长句，正是本期要解决的问题。

失败路径：provider 失败、纠正后仍不合契约 → 抛 `BriefError`，不写 manifest，stderr 留可归属诊断；digest 正文不受影响，该行列表只显示标题。命令层逐条记账，失败条数 >0 → 退出码 1，已成功的照常写入。

## 7. 模块

| 模块 | 变更 |
|---|---|
| `kairo.brief`（新） | `BriefError`、`_BRIEF_PERSONA`、`generate_brief(ws, ref_id, *, provider)`、`brief_stale(man, digest_text)` |
| `kairo.models` | `Manifest` 增 `brief: str \| None`、`brief_hash: str \| None` |
| `kairo.rules` | `DigestRule` 写完 digest 后调 `generate_brief`，与既有 `extract_after_digest` 同位置同容错口径 |
| `kairo.timeline` | `TimelineItem` 增 `brief: str = ""`；`scan_timeline` 从已读 manifest 取，无额外 I/O |
| `kairo.cli` | 新增 `kairo brief` |
| `web/templates/timeline.html` | `tl_row` 增 `show_brief` 参数，仅列表视图传真 |
| `web/static/app.css` | `.timeline-list .tl-row` 改两行 `grid-template-areas`，新增 `.tl-brief` |

`generate_brief` 复用 `kairo.rules._run_agent`（与 `glossary_review.provider_extractor` 同一先例：短 prompt、单 artifact 取回），artifact 名 `brief.txt`。

`brief_hash` 为 digest 正文 `sha256[:12]`，与仓库既有 hash 口径一致；digest 变更后 hash 不匹配即视为过期，命令重跑时覆盖。

## 8. API/CLI

| 入口 | 行为 |
|---|---|
| `GET /timeline`（列表） | 有 brief 的行渲染第二行；无 brief 的行不变 |
| `GET /timeline?day=`（日历） | 不渲染 brief |
| `kairo brief` | 补齐 serve root 内全部有 digest、且无 brief 或 brief 过期的 Ref |
| `kairo brief REF_ID [--home H]` | 只处理一条；id 不唯一且未给 `--home` 时报错退出 1 |
| `kairo brief --root PATH` | 指定 serve root；默认 `KAIRO_SERVE_ROOT` 或 cwd |
| `kairo brief --force` | 忽略 `brief_hash` 一致性，强制重算 |
| `kairo brief --limit N` | 最多产出 N 条后停下，用于控成本 |

批量处理顺序按有效发生日新→旧（与 Timeline 列表同序），因此 `--limit` 先补最近的资料。单条约 50s（现网实测），全量 96 条约 80 分钟。
| `kairo brief --json` | 每条一行结果记账（home、id、status、detail）；status ∈ ok / skipped / no-digest / failed |

退出码：全部成功 0；有失败 1；serve root 不存在或参数非法 1。

manifest 写入沿用 `write_manifest` 的 `exclude_none`，因此 brief 为空时 yaml 里不出现该键。

## 9. 边界

只改 brief 的产出与列表呈现。不改 Tag 筛选、发生日与录入时间、区间回顾、digest 正文与 hash、fold / compose、`state.json` 契约、Ref 详情页、公开只读面的可见性判定。

## 10. 迁移/兼容/回滚

manifest 新增两个可选键，旧 manifest 无键即无 brief，无需回填也不阻塞任何路径。存量 96 条 digest 由 `kairo brief` 一次补齐，digest.md 不被改写。回滚：模板停止渲染第二行并移除 CSS，yaml 中残留的 `brief` / `brief_hash` 键被忽略，不需要清理。

## 11. 测试计划

| 层 | 对上 | 可判定 |
|---|---|---|
| E2E S1 | 列表含有 brief、无 brief 两类行 | 有 brief 的行 HTML 含 `.tl-brief` 且文本完整；无 brief 的行不含该节点也无占位文案；`?day=` 的 HTML 不含 `.tl-brief` |
| E2E S2 | digest 成功后自动产出；provider 失败一例 | 成功例 manifest.brief 与调用返回一致；失败例 manifest 无 brief 键、digest.md 内容与 hash 不变 |
| E2E S3 | 存量多条有 digest 无 brief | `kairo brief` 后 manifest.brief 条数等于成功条数；digest.md 哈希 0 变化；再次执行全部跳过且退出码 0 |
| Integration | `generate_brief` 同时被 DigestRule 与命令调用 | 同一 digest 输入两条路径写出同一 brief 与同一 `brief_hash` |
| Unit | 校验与过期判定 | 空 / 多行 / 超 50 字 → `BriefError` 且不写 manifest；`brief_hash` 不匹配 → 判定过期 |
| Unit | 超长纠正 | 首次超长 + 二次合规 → 写入且只调用 2 次；二次仍超长 → 失败且调用停在 2 次；多行不触发重试（调用 1 次） |
| Unit | Timeline 扫描 | `TimelineItem.brief` 取自 manifest；无键时为空串 |

## 12. 开放问题

N/A。排版（第二行）、存数（manifest 字段）、产出方式（独立短调用）、行高不齐（接受）均已拍板。

## 13. 关联

- [#362](https://github.com/xforce-io/kairo/issues/362)
- [#287](https://github.com/xforce-io/kairo/issues/287)：Timeline 列表按发生日分组
- [#242](https://github.com/xforce-io/kairo/issues/242)：Timeline 为 serve root 上中立的时间流
- [#182](https://github.com/xforce-io/kairo/issues/182)：digest 后处理的既有位置与容错口径
