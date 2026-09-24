# #431 一条参考只保留一条由人设置的置顶

Drive **Topic notes 预览**（`/w/{slug}/ref/{ref_id}/notes` 换入 `#reader`）。对照 `note-pin.json` 与 `notes.jsonl` 追加序。不碰现网 serve root。不触发 LLM / `step` / `compose`。

## 入口

1. Topic `/w/{slug}` 右侧「形态」→「洞察 notes」预览
2. `POST /w/{slug}/ref/{ref_id}/notes/{note_id}/pin`（可写时替换唯一置顶）
3. 空态或加载失败时的「回到摘要」（回到该参考当前主预览）

**不走：** Ref 详情 `/refs/{id}` 的置顶、单条详情上的置顶、取消置顶、CLI 置顶、票 B 的默认落点。

## 路径 → 可判定结果

| # | 路径 | 可判定结果 | 依赖 |
|---|---|---|---|
| S1 | 两条 note 的预览上先后点击「置顶」，然后刷新 | 只有后一条显示「已置顶」并全文展开；前一条回到卡片 | Console |
| S2 | 多条 note、无 `note-pin.json`，打开预览 | 全文展开的是 `notes.jsonl` 追加序第一条，没有任何「已置顶」 | Console |
| S3 | 已置顶后，机器再追加一条 generated note，再打开预览 | 置顶文件的 `note_id` 不变；新 note 是卡片；原置顶仍全文展开 | Console |
| S4 | 确认删除当前被置顶的 note，再打开预览 | 置顶文件不存在；展开剩余 note 里的追加序第一条；没有「已置顶」 | Console |
| S5 | 打开已确定展开项的预览 | 展开项是 Markdown 全文，无「更多 / 收起」；其余是卡片，长文仍有「更多 / 收起」 | Console |
| S6 | 零条 note 时打开预览，再点「回到摘要」 | 可见「尚无洞察 notes」和「回到摘要」；有 digest 时阅读区变为 digest；无 digest 时变为现有主预览，且不新造摘要文件 | Console |

## 已知不做

- Ref 详情与单条详情不加置顶，仍全部是卡片
- 不提供取消置顶
- public-read 可看到「已置顶」，没有「置顶」按钮，写入被拒绝
