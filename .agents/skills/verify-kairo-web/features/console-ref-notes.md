# #411 Console Ref notes 盖楼

Drive **Console 真实页面**（Ref 详情与单条只读页）。对照 CLI `kairo notes show --ref` / `show <id>` / `add`。不碰现网 serve root。不触发 LLM / `step` / `compose`。禁止把「请去 CLI」当过线。

## 入口

1. `GET /refs/{ref_id}?home=`（Ref 详情：列表或空态 + 轻量追加表单）
2. `GET /refs/{ref_id}/notes/{note_id}?home=`（单条只读）
3. `POST /refs/{ref_id}/notes`（追加；成功后仍在详情）

**不走：** 富文本编辑器、登录身份、`kairo step` / compose、改 understanding。

## 路径 → 可判定结果

| # | 路径 | 可判定结果 | 依赖 |
|---|---|---|---|
| S1 | 有 notes 的 Ref 打开详情 | 列表条数/顺序 = `notes show --ref --json`；同页轻量表单；无富文本工具栏 | Console |
| S2 | 从列表点开一条 | 正文/作者/时间/类型 = `notes show`；该页无表单、无保存、无追加入口 | Console |
| S3 | 无 notes 的 Ref 详情 | 「尚无洞察 notes」；无假数据；同页仍有表单；不与 digest/fold 空态混用 | Console |
| S4 | 详情提交非空正文（类型四选一或未选） | 提交中不可连点；成功后本页列表 +1；作者为进程本机用户；未选类型为 insight；可与 CLI 核对 | Console |
| S4-fail | 空正文 / 非法类型 / 请求失败 | 错误可判；条数不变；不落半条 | Console |

## 已知不做

- 不 Drive 完整富文本编辑器
- 不把作者做成「点按钮的人」
- 不与 #410 CLI 契约混改
