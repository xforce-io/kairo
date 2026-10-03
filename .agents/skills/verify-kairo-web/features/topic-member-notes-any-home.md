# #438 Topic 成员不论 home 在哪都能追加和置顶 note

Drive **Topic notes 预览**与该 Ref 自己的页面。对照该 Ref 实际 home 下的 `notes.jsonl` 与 `note-pin.json`。不在 Topic 目录为这份 Ref 另建 notes。不碰现网 serve root。不触发 LLM / `step` / `compose`。

## 入口

1. 本目录成员：`/w/{slug}/ref/{local_id}/notes`（「新增」「追加 note」）
2. home 不在该 Topic 的成员：`/w/{slug}/ref/{gid}/notes?home=global`（同一表单、追加、置顶）
3. 该 Ref 自己的页面：`/refs/{gid}?home=global`（看见同一条正文）
4. 本目录与非本目录成员的 Topic 页：`/w/{slug}/ref/{id}` 与 `?home=global`（改标题、公开锁定、在系统中打开）
5. 公开只读下的同一 Topic notes 预览（无表单，写入被拒绝）

## 路径 → 可判定结果

| # | 路径 | 可判定结果 | 依赖 |
|---|---|---|---|
| S1 | 打开本目录成员与非本目录成员的 Topic notes 预览 | 两页各有 1 个追加表单，文案含「新增」和「追加 note」 | Console |
| S2 | 在非本目录成员的预览提交一条 insight，再打开该预览和该 Ref 自己的页面 | 该 Ref 自己的 notes 为 N+1，两处看见同一正文；Topic 目录为该 Ref 新增的 notes 文件数为 0 | Console |
| S3 | 该 Ref 已有 2 条 note 时，在 Topic 预览置顶后写的那一条，再打开预览 | 置顶 1 条；展开的是后写的那一条 | Console |
| S4 | 公开只读打开同一预览，再尝试提交 | 追加表单 0 个；写入被拒绝；notes 条数变化为 0 | Console |
| S5 | 打开本目录成员与非本目录成员的 Topic 页（两份都不能在页内预览） | 本目录页可改标题、公开锁定、在系统中打开；非本目录页这三项控件数为 0 | Console |

## 已知不做

- 非成员仍不可打开
- 不把改标题、公开锁定、在系统中打开开放到 home 不在该 Topic 的页面
- 不在 Topic 之间搬家，不改已有 note 正文
