# 功能地图

| 文件 | 功能 | 入口 |
|---|---|---|
| `knowledge-candidates.md` | Knowledge 页候选审核：待审核队列、目击队列、采纳 / 忽略 / 合并 / 编辑 | `/knowledge?workspace=<slug>&queue=candidates`、`&queue=sighted`；队列导航按钮 |
| `knowledge-drift.md` | 知识漂移只指真受影响产物；Topic 页「N 个产物基于旧知识 · 重算受影响」 | `/w/<slug>` 右侧面板；`/knowledge?workspace=<slug>&queue=drift` |

新增用户可见功能时在此加一行并补功能文件；验收 Story 对不上任何一行即 BLOCKED。
