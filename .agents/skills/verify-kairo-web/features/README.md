# 功能地图

| 文件 | 功能 | 入口 |
|---|---|---|
| `knowledge-candidates.md` | Knowledge 页候选审核：待审核队列、目击队列、采纳 / 忽略 / 合并 / 编辑 | `/knowledge?workspace=<slug>&queue=candidates`、`&queue=sighted`；队列导航按钮 |
| `knowledge-drift.md` | 知识漂移只指真受影响产物；Topic 页「N 个产物基于旧知识 · 重算受影响」 | `/w/<slug>` 右侧面板；`/knowledge?workspace=<slug>&queue=drift` |
| `ref-run-runtime.md` | 单 Ref 一次推进墙钟下降 ≥40%；digest 提取与 compose 重叠、同文只提取一次 | `KAIRO_STUB=1 kairo run`；`pytest tests/test_ref_run_runtime_378.py` |
| `preview-mermaid.md` | Topic / Ref digest / Artifact 预览把 mermaid 画成图；非法 mermaid 可见失败 | `/w/<slug>`、`/w/<slug>/ref/<id>`、`/projects/<id>/runs/<run>` |
| `notion-project-datasource.md` | #388 Project 新数据源只认 Notion 页：Settings 文案、粘贴提示、Reader 标签、添加/读取错误码 | `/settings`；`/projects/<id>` 添加框；`/projects/<id>/datasources/<ds>`。S3 agent Run **skip**（需 provider，不在此触发 LLM） |
| `timeline-view-run.md` | #396 Timeline 当前视图一次确认推进：未加工标记、确认数字、Web/CLI `run-view` | `/timeline?day=`、`/timeline/run-preview`、`POST /timeline/run`、`kairo run-view`。真 ASR/LLM **skip** |
| `interactive-project-read.md` | #400 交互式读取 Project 登记源及一跳外链：临时读、引用记录、前置失败指引 | 正式 CLI `project context/read/read-url`、`project record *`。无 Console；真网 Reader / LLM **skip** |
| `continuous-compose.md` | #408 连续新增材料自动整理、分批恢复、失败保护与旧容量恢复 | CLI `run/status`；Topic 主按钮、产物与运行摘要；scratch 确定性 provider，无真实 LLM |
| `status-fold-progress.md` | #401 status 待处理／已融入／增量／blocked 口径与按 Ref 核当前 digest | 正式 CLI `kairo status`、`--json`、`--ref`。无 Console；LLM **skip** |
| `ref-find-read.md` | #402 按标题／发生日／Topic 检索并直接读 digest 或 transcript | 正式 CLI `kairo ref find`、`kairo ref read`。无 Console；LLM / step **skip** |
| `ref-markdown-notes.md` | #410 Ref 级 markdown notes 盖楼：CLI list/add/show、近窗稳定键、provenance 样例 | 正式 CLI `kairo notes`。无 Console（#411）；LLM / step / compose **skip** |
| `console-ref-notes.md` | #411 Console：Ref 详情 notes（digest 之上）列表/只读/空态/轻量追加；阅读画布与详情同一套文档批注观感；Topic 形态整行进 `#reader` | `/w/{slug}` 形态与 `#reader`、`/refs/{id}`、`/refs/{id}/notes/{note_id}`。LLM / step / compose **skip** |

新增用户可见功能时在此加一行并补功能文件；验收 Story 对不上任何一行即 BLOCKED。
