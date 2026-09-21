# #411 — Console：Ref markdown notes 只读入口与盖楼展示

- Issue: [#411](https://github.com/xforce-io/kairo/issues/411)
- 分支: `feat/411-console-ref-notes`
- 状态: Draft（L2 · 待负责人 review 交互）
- L1: [Approved](https://github.com/xforce-io/kairo/issues/411)（issue `## 设计`，2026-09-21）
- 本文件是 #411 的 **L2 事实源**。Issue 只保留摘要与本链接。宣布 L2 设计通过之前禁止功能代码与合入。数据与稳定键认 [#410](https://github.com/xforce-io/kairo/issues/410) / `docs/design/410-ref-markdown-notes.md`。

## 1. 背景

[#411](https://github.com/xforce-io/kairo/issues/411)。#410 已合入 `kairo notes` 与旁路存数。Console 现有 Ref 详情（`/refs/{id}?home=`）展示 digest / Tag / 相关 Topic / 来源形态，没有 notes 盖楼。本票只补该页的可见路径与轻量追加。

## 2. 名词解释

沿用 [docs/glossary.md](../glossary.md) 的 Ref、Ref 身份键、notes、digest、fold。本票不新增名词。

页面用语：

| 用语 | 含义 |
|---|---|
| Ref 详情 | 现网 `/refs/{id}?home=` |
| 单条只读页 | 从列表点进的一条 note，无追加入口 |
| 轻量追加表单 | 纯文本/markdown 源 + 类型四选一，非富文本编辑器 |

## 3. 目标与非目标

### 3.1 目标

与 L1 / 票面 S1–S4：Ref 详情看见列表；点开只读正文；空/错可判；同页追加一条后列表 +1。数据、稳定键、add 语义认 #410。

### 3.2 非目标

完整富文本编辑器、实时预览、附件、协作光标、编辑/删除已有 note、登录身份、「点按钮的人」、替代 digest/fold、重做 CLI、本窗口发版。public-read 不开放写入。

## 4. 能力

### 4.1 UI/UX

**信息架构**

```mermaid
flowchart LR
  tl["Timeline / Topic / Project"] --> ref["Ref 详情 /refs/{id}"]
  ref --> list["notes 区：列表或空态"]
  ref --> form["轻量追加表单"]
  list --> one["单条只读页"]
  one --> ref
```

- 入口：现有 Ref 详情。不新增顶栏导航。
- notes 区是详情页上独立一节，位于 digest 节之后、与 Tag 等并列。标题即「洞察 notes」，不复用 digest 空态文案。
- 列表行：类型、作者、时间、首行/标题；点击进入单条只读页。
- 单条只读页：返回 Ref 详情；展示类型、作者、时间、稳定键、markdown 正文。无表单、无保存。
- 追加入口：仅 Ref 详情。空态与有列表时都在。public-read 不出现。

**入口线框（审查面）**

Topic 页本票不改：仍是 Ref 列表。点某一 Ref 打开 `/refs/{id}`。Topic 页不加 notes 预览、不加「添加 note」。

Ref 详情（唯一入口），从上到下：

```
[ 返回 ]
[ Ref 标题 / 元数据 ]
[ digest …… ]

── 洞察 notes ──
  insight   作者  时间  首行摘要……     ← 点击进只读页
  decision  作者  时间  另一条……

  类型 [ insight ▼ ]     ← 四选一，未选当 insight
  [ markdown 正文，纯文本框，无工具栏、无预览 ]
  [ 追加 note ]           ← 仅可写会话；public-read 无此段
```

空态：digest 下仍是「洞察 notes」标题 +「尚无洞察 notes」+ 同一表单。无「去 CLI」。

单条只读页：类型 / 作者 / 时间 / 正文。无表单。

**全状态**

| 状态 | 用户看见 | 判定 |
|---|---|---|
| 列表 | notes 区列出该 Ref 全部 notes | 条数 = `kairo notes show --ref --json` 的 `count`；顺序与之相同 |
| 空 | 「尚无洞察 notes」+ 追加入口 | 不出现 digest 空文案、不出现 fold 未融入文案、无假行 |
| 只读 | 单条正文与元数据 | 与 `kairo notes show <stable_id>` 一致 |
| 加载失败 | notes 区错误，不是空态 | 与「尚无洞察 notes」可分 |
| 表单默认 | textarea 空；类型未选，提交按 insight | 四选项可见 |
| 提交中 | 按钮不可用，可见进行中 | 连点不产生第二条 |
| 追加成功 | 仍在该 Ref 详情，列表 +1，新条可见 | 可与 CLI list/show 对上 |
| 追加失败 | 留在表单；错误可判；列表条数不变 | 空正文、非法类型、请求失败分得开；不落半条 |
| public-read | 列表/空/只读可见；无表单 | POST 被拒绝 |

**布局与交互**

- 列表在上、表单在下（空态：空文案在上、表单在下）。
- 表单：类型 `<select>` 四项（insight / decision / open-question / correction），未选提交为 insight；正文 `<textarea>`（markdown 源，无工具栏、无预览）；提交按钮。
- 空正文：前端拦截 + 服务端再拒（#410 `invalid_request`）。列表不变。
- 成功：PRG 回到同一 Ref 详情，不进单条页。
- 单条页失败：note 不存在或不可读 → 错误态，不是空列表。
- 不做：WYSIWYG、拖放上传、在只读页追加、把用户送去 CLI。

### 4.2 数据

只调用 #410 `list_notes` / `show_notes` / `add_note`。作者为 Console 运行进程本机用户。不新增存数。

列表展示 **全楼**（`show --ref`），不是 CLI `list` 默认近 48h。近窗发现仍走 CLI。

## 5. 思路与折衷

采纳：在现有 Ref 详情加一节，而不是新的 Notes 顶栏。只读拆页，满足 S2「该页无写入控件」，详情页保留 S4 入口。

放弃：入口只提示 CLI；富文本编辑器；把 Console 列表做成近 48h 切片（盖楼会缺楼，故列表对齐 `show --ref` 全量）。放弃为本票引入登录用户。

## 6. 架构

```mermaid
flowchart TD
  getRef["GET /refs/{id}"] --> load["notes.show --ref"]
  load -->|ok 0| empty["空态 + 表单"]
  load -->|ok n| rows["列表 + 表单"]
  load -->|fail| loadErr["加载失败"]
  post["POST /refs/{id}/notes"] --> add["notes.add"]
  add -->|ok| redir["303 回详情，列表 +1"]
  add -->|fail| stay["详情 + 提交失败"]
  getOne["GET /refs/{id}/notes/{note_id}"] --> show["notes.show 稳定键"]
  show -->|ok| read["只读正文"]
  show -->|fail| oneErr["只读页错误"]
```

主路径：打开详情 → 见列表或空 → 填表提交 → 仍在详情且 +1 → 点开一条只读。失败路径不写半条、不与空态混用。不调用 `step` / `compose`。

## 7. 模块

| 面 | 变化 |
|---|---|
| Ref 详情模板 | 增加 notes 区与表单 |
| 单条只读页 | 新页，无表单 |
| web 路由 | GET 详情带 notes；GET 单条；POST 追加 |
| `kairo.notes` | **不改契约**；页面调用现有函数 |
| public-read | 隐藏表单、拒绝 POST |
| CLI / 存数 / digest / fold | **不改** |

## 8. API/CLI

无新 CLI。无新公共 HTTP JSON。页面路由：

```
GET  /refs/{ref_id}?home=
GET  /refs/{ref_id}/notes/{note_id}?home=
POST /refs/{ref_id}/notes    字段：home, content, type（可空→insight）
```

POST 成功 303 至该 Ref 详情。失败 200 再渲染详情并带提交错误（或 4xx 后回到表单）。public-read 或校验失败不写盘。`home` 语义与现网 Ref 详情相同（`global` → 空字符串）。

## 9. 边界

In/Out 同 issue `## 设计`。无 digest 的 Ref 仍可追加。#410 近 48h 过滤不进本页列表。本窗口不发版。

## 10. 迁移 / 兼容 / 回滚

无存数迁移。回滚即去掉本票页面与路由；notes 文件仍由 #410 保留。public-read 行为与现网其它写入口一致。

## 11. 测试计划

功能地图：`.agents/skills/verify-kairo-web/features/console-ref-notes.md`。Drive **Console 真实页面**。不碰现网 serve root。不跑 step/compose。

| 层级 / 验收 | 路径 | 可判定结果 |
|---|---|---|
| E2E S1 | 有 notes 的 Ref 打开 `/refs/{id}` | 列表可见；条数/顺序 = `notes show --ref --json`；同页有轻量表单；无富文本工具栏 |
| E2E S2 | 从列表点进一条 | 正文与元数据 = `notes show`；该页无表单、无保存 |
| E2E S3 | 无 notes 的 Ref 详情 | 「尚无洞察 notes」；无假行；同页有表单；与 digest 空文案不同 |
| E2E S4 | 详情提交非空正文 | 提交中按钮不可用；成功后仍本页、列表 +1；CLI 对得上；作者为进程用户；未选类型为 insight |
| E2E S4 失败 | 空正文 / 非法类型 / 请求失败 | 错误可判；条数不变；无新 `notes.jsonl` 行 |
| Integration | public-read 打开同一 Ref | 可见列表或空；无表单；POST 不落盘 |
| Unit | 空态文案键不与 digest/fold 共用；类型闭集 | 不跑网络 |

## 12. 开放问题

无。入口只在 Ref 详情；Topic 页不加预览/追加。列表全楼已由产品点头。本节线框供负责人审「入口长什么样」。

## 13. 关联

- [#411](https://github.com/xforce-io/kairo/issues/411)
- [#410](https://github.com/xforce-io/kairo/issues/410) / `docs/design/410-ref-markdown-notes.md`
- 现网 Ref 详情 `/refs/{id}`
