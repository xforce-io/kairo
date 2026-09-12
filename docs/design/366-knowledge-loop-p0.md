# 【学习闭环】P0 修复：provider 产物纯净、确认知识不再回流、constitution 解析缓存

- Issue: [#366](https://github.com/xforce-io/kairo/issues/366)
- 状态: Implemented
- 最后更新: 2026-09-12

## 1. 背景

对学习闭环的端到端评审（浏览器走查现网 + scratch root 用 grok 真跑一次 Run）暴露出四个可以直接修的缺陷；它们分别落在 provider 出口、候选入口、prompt 注入与页面读路径，彼此独立，但都在把闭环的吞吐压到零：产物被过程叙述污染、已确认的专名被反复提名、注入 prompt 的知识行无信息量、每次刷新都要等 5 秒。

## 2. 名词解释

| 术语 | 定义 |
|---|---|
| **最终轮文本** | grok headless 模式最后一条 assistant 消息的正文；`streaming-messages-json` 的终止 `result` 行的 `result` 字段。 |
| **再次目击（re-sighting）** | 提取器提出的 draft，其标题与全部别名都归一到同一条 confirmed 知识；它不携带新词，不构成新的审核事项。 |
| **别名提案** | draft 的标题未被任何 confirmed 条目认领，但某个别名被认领（典型：ASR 误听「西端」配别名「C 端」）；它是可审事项。 |
| **幻影 stale** | 产物内容与输入都没变，仅因指纹算法改动导致 `input_hash` 不匹配而被判待重算。 |

## 3. 设计目标与非目标

- **目标**：
  - Provider 只把最终轮文本写入产物；拿不到就报错，不做正则剥离。
  - 再次目击不产生候选，而是成为 confirmed 条目的出处；别名提案保持可审。
  - Prompt 里的知识上下文把 confirmed 专名声明为已核实写法，并给出别名与说明。
  - `Workspace.constitution` 在文件未变时不重复解析 YAML。
  - 一次性回写现网因指纹格式变化而误判 stale 的 `input_hash`。
- **非目标**：
  - 不改提取 prompt、候选门槛（`qualifies_for_review`）或 sighted 的浮出策略。
  - 不把知识层的自动出处追加扩展为自动合并说明/别名；语义变更仍由人审。
  - 不在 `DigestRule` 内为旧指纹格式保留兼容分支。

## 4. 能力与功能设计

### 4.1 GrokProvider 出口

`grok --output-format json` 的 `text` 是所有 assistant 轮次的拼接；多轮 Read 时前几轮是「先读常驻技能和必读材料…」之类的叙述。改为 `--output-format streaming-messages-json`（NDJSON），逐行解析：

- 遇 `type == "error"` 立即抛 `RuntimeError`；
- 取最后一条 `type == "result"`：`is_error` 或 `subtype != "success"` 抛错；`result` 非空字符串即最终轮文本；
- 没有 `result` 行（进程中途死亡）抛错，产物文件不落盘。

`review.py` 中既有的 `strip_process_preamble` 保留不动，本次不再增加任何事后剥离。

### 4.2 候选入口：再次目击 → 出处

`ingest_candidates` 对每条 draft 先问 matcher：`suggest([title, *aliases])` 的结论若只有一个且为 `merge:<id>`，即再次目击：

- 新 draft：直接以 `status="merged", merged_into=<id>` 记入 review（可追溯），并把该 digest 摘录作为 `KnowledgeSource` 追加到条目（先找 workspace 权威文件，再找 global）；`_append_source` 保证幂等。
- 已存在的开放候选（sighted/pending）在其词被确认之后再次目击，同样翻为 merged。
- 找不到条目即抛 `KnowledgeError`（由 `extract_after_success` 的旁路语义记为提取错误）。

别名提案（标题 unknown、别名 merge）不满足「只有一个结论」，照旧走门槛。

### 4.3 知识上下文注入

Header 改为：confirmed 专名按条目标题书写、视为已核实、不必再标 ⚠️；条目说明仅作背景，与材料冲突时保留材料说法。每行格式 `- 标题（命中：X；别名：…；说明）`，不再输出 scope 与出处路径。相应地 `semantic_hash` 去掉 `source_paths`：出处已不进 prompt，自动追加出处不应被判为知识漂移。

### 4.4 constitution 缓存

`Workspace.constitution` 以 `constitution.yaml` 的绝对路径为键缓存 `((mtime_ns, size), 解析后的 dict)`；每次调用仍 `model_validate` 出新实例，调用方可自由修改返回值。`write_constitution`、`save_workspace` 等任何写入都会改变 mtime/size 而自然失效。

### 4.5 一次性迁移

脚本（仓库外）对每个 Topic 用当前 `DigestRule.discover` 找出 stale 的 ok digest，按旧格式 `# {文件名}\n\n{text}` 重算指纹；与存储值相等者视为幻影 stale，回写新 hash 并在 state.json 旁留备份；不相等者原样保留并列出。

## 5. 验收

见 PR 的 S1–S5 表。

## 6. 影响与风险

- 出处自动追加会写 `glossary.yaml` / `constitution.yaml`；与人工在 Web 侧同时编辑存在极小的 last-writer-wins 窗口，写入本身是原子的。
- 现网既有已污染产物不会自动重写；需要时由人触发重算。
