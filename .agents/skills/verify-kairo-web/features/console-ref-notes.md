# #411 Console Ref notes 盖楼

Drive **Console 真实页面**（Topic 形态预览、Ref 详情、单条只读页）。对照 CLI `kairo notes show --ref` / `show <id>` / `add`。不碰现网 serve root。不触发 LLM / `step` / `compose`。禁止把「请去 CLI」当过线。

## 入口

1. Topic `/w/{slug}` 右侧「形态」→「洞察 notes」预览（只读跳转）
2. `GET /refs/{ref_id}?home=`（notes 在 digest 之上：列表或空态 + 轻量追加表单）
3. `GET /refs/{ref_id}/notes/{note_id}?home=`（单条只读）
4. `POST /refs/{ref_id}/notes`（追加；成功后仍在详情）

**不走：** 富文本编辑器、登录身份、`kairo step` / compose、改 understanding。

## 路径 → 可判定结果

| # | 路径 | 可判定结果 | 依赖 |
|---|---|---|---|
| S1 | 有 notes 的 Ref 打开详情 | notes 在 digest 之上；条数/顺序 = `notes show --ref --json`；可写同页轻量表单；public-read 无表单 | Console |
| S5 | Topic 形态区「洞察 notes」整行 | 与转写/音频同一交互；落点 Topic `#reader` notes 预览（URL 仍在 Topic）；可写画布内能追加且 +1；空态仍可点、不造 Ref；形态表无表单 | Console |
| S6 | Ref 详情与阅读画布 notes 区 | 区头 + 短表在上 + 轻卡片列表；textarea 约 3 行；类型与「追加」同一行、按钮内容宽右对齐；无通栏厚按钮、无居中空矩形、无外套厚卡片；不抄置顶/删除 | Console |
| S7 | Topic 左侧点选 Ref | 右侧形态跟随该 Ref；有进 `/refs/{id}` 详情的链接 | Console |
| S2 | 从列表点开一条 | 正文/作者/时间/类型 = `notes show`；该页无表单、无保存、无追加入口 | Console |
| S3 | 无 notes 的 Ref 详情 | 「尚无洞察 notes」；无假数据；同页仍有表单；不与 digest/fold 空态混用 | Console |
| S4 | 详情提交非空正文（类型四选一或未选） | 提交中不可连点；成功后本页列表 +1；作者为进程本机用户；未选类型为 insight；可与 CLI 核对 | Console |
| S4-fail | 空正文 / 非法类型 / 请求失败 | 错误可判；条数不变；不落半条 | Console |

## 已知不做

- 不 Drive 完整富文本编辑器
- 不把作者做成「点按钮的人」
- 不与 #410 CLI 契约混改
