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

新增用户可见功能时在此加一行并补功能文件；验收 Story 对不上任何一行即 BLOCKED。
