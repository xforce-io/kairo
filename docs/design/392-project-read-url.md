# #392 — Project CLI 按 URL 读材料（方案 B · 跟读）

- Issue: [#392](https://github.com/xforce-io/kairo/issues/392)
- 分支: `feat/392-project-read-url`
- 状态: Approved
- 最后更新: 2026-09-16（L1 Approved · peng；§8.1–8.5 全部默认 A 已锁定）
- L1: Approved（peng 2026-09-16；defaults A locked；范围仍以 §§5.1–5.6 为准，不扩产品范围）

本文件是 #392 的 L1 事实源。Issue 只保留摘要与本链接。不自批、不合并。

## 1. 背景

[#392](https://github.com/xforce-io/kairo/issues/392)。#388 后 Project **新**数据源只认 Notion。能源「Notion」页是索引：正文多为企微表格 / 智能表、墨刀、内网 ShowDoc 外链。`project read` 只接受目录里的 `source_id`；Skill「Project 运行」写明未出现在目录中的来源不可读。因此 Project agent（含 #390 后的 Grok）即使用户期望跟读页内链接，也只能复述 URL，拉不到企微正文。

引擎里企微等 Reader 仍可调用（#388 只把它们的 `infer_source.live` / 目录 `live` 翻成 False，挡住 **add**）。缺的是给 agent 用的正式 CLI 门口，且不能违反「用户新增永久数据源仅 Notion」。

用户已选定 **方案 B**：不靠读 Notion 时引擎自动展开外链（方案 A），而由 Project agent + Skill 指引跟读；阅读能力做成 `kairo project …` CLI，复用现有 Reader；正文只进 **本 Run** inputs / scratch。

发现现场：Project `prj-5749223dc8d3` 能源团队管理，sole DS = Notion「能源」；Task `tsk-2bb0f68dacda` run-99c6ea870609 succeeded（provider=grok），inputs 仅 Notion 页 ~1.5KB URL 列表；Artifact 写明墨刀 / ShowDoc / 企微正文未拉取。

## 2. 现有机制（keel-how · Forge）

对照 `feat/392-project-read-url` tip `ebd38ebb`（= `main` after #390 / #391；契约边界引用，非实现清单）：

- `src/kairo/cli.py` — `project context` → `list_context`；`project read PROJECT SOURCE_ID --run --root` → `read_material(source_id)`，成功 JSON 含 `ok/source_id/content/version/input_id`（有 `--run` 时另加 `numbered_content` / `line_count`）。失败走 `_cli_fail`：`{ok:false,code,error}`，退出 1。**无**按 URL 读的子命令。
- `src/kairo/data/SKILL.md` § Project 运行 — 先 `project context`，`type=datasource` 必须先读；再 `project read … SOURCE_ID`；「未出现在该 Project 目录里的来源不可读」。禁止 step / re-step / accept / 写 Topic。
- `src/kairo/projects.py` — `_execute_agent_run` 注入的 prompt 只教 `project context` / `project read`，不提 URL。发布前：未知 `input_id` → `invalid_input_ref`；`validate_recorded_inputs`；若 `scope_datasources` 非空且没有任何 `type=datasource` 或 `source_id` 以 `datasource:` 开头的输入 → `datasource_unread`；再 `finalize_inputs`。
- `src/kairo/project_materials.py` — `parse_source_id` 只认 `topic:{slug}:understanding` / `topic:{slug}:digest:{home}:{ref_id}` / `datasource:{ds_id}`。`read_material` 校验来源在本 Run 冻结范围内。`record_run_input` 写入本 Run scratch：`input_id`（`inp-{12 hex}`）、`source_id`、`type`、`title`、`url`（仅当 `type==datasource` 时写成 `result.source_id`，否则 `null`）、`version`、`body`。同 `source_id`+`version` 去重。`_source_in_scope` / `validate_recorded_inputs` / `finalize_inputs`：**不认**任何 URL 形态的 `source_id` → 今日若把 `url:…` 写进 scratch，发布会 `evidence_failed`「输入来源越界」。
- `src/kairo/readers.py` — `infer_source` 纯函数、不访问网络。企微主机 `doc.weixin.qq.com` / `work.weixin.qq.com` / `page.weixin.qq.com`，kind 闭集 `document` (`/doc/`) / `spreadsheet` (`/sheet/`) / `smartsheet` / `smartpage`，返回 `InferredSource(..., live=False)`。腾讯 `docs.qq.com` `/sheet/` `/smartsheet/` 亦 `live=False`。`mail://` / `imap://` `live=False`。Notion 主机（含 `app.notion.com`）`live=True`。无法识别（墨刀 / ShowDoc 等）→ `invalid_link`「无法识别的资料平台」。`read_wecom_docs` / `read_tencent_docs` / `read_wecom_mail` / `read_imap_mail` / `read_notion_page` 仍由 `read_datasource` 分发；**读路径不看 `live`**，看 `connection.authorized`（及 Notion 的 `NOTION_TOKEN`、腾讯的 `cmd`）。
- `src/kairo/projects.py` `add_datasource` — `infer_source` 后 **仅当 `live=False` 抛 `unsupported_reader`**（「尚未接入」）再落盘。#388 后企微 / 腾讯 / 邮件 **新加**失败；存量行仍可列出 / 读取 / 删除。
- `src/kairo/settings.py` `READER_CATALOG` — wecom / tencent / imap `live=False`（健康面 `unavailable`）；notion `live=True`。企微无 `token_env`；真读凭据是本机 `wecom-cli` 会话（或 stub `cmd`）。`authorized` 开关与目录 `live` **独立**：健康显示 unavailable **不**等于 `connection.authorized is False`。

今日事实：agent 只能 `project read` 已登记源。Skill 禁止读目录外 URL。Reader 对企微四种 URL 仍可调用，但没有 CLI 把门。把 URL 正文偷偷写进 scratch 过不了 `_source_in_scope`。方案 A（引擎读 Notion 时自动展开）与永久 `add_datasource` 显式 Out。

## 3. 名词解释

沿用 [docs/glossary.md](../glossary.md) 的 Project、Data Source、Reader、连接、Task、Run、Artifact、材料身份。本票用语：

| 用语 | 本票含义 |
|---|---|
| 方案 B | agent + Skill 跟读页内外链；引擎不在 `read_notion` / `project read` 时自动展开 |
| 跟读 / `read-url` | 正式 CLI：对一条 URL 调现有 Reader，把正文记入**本 Run** scratch / 终态 inputs，返回 `input_id` |
| URL 材料 | 本 Run 已记账的跟读结果；`type=url`；**不是** Data Source，不出现在 Project 数据源列表，不写 `cache/{ds_id}` |
| 索引页 | 已登记 Data Source（典型：#388 的一页 Notion），正文含外链 |
| 一跳 | 只跟读**已读登记源**正文里的链接；不跟读跟读结果里的再链 |
| 无 Reader | `infer_source` 无法识别，或识别后 `read_datasource` 无分发。本票含墨刀 / ShowDoc |

禁止把 URL 材料叫成「临时数据源」或「自动挂上的企微源」。

## 4. 目标与非目标

### 4.1 目标

1. **S1**：存在正式 CLI（L1 默认名 `project read-url`），对至少企微四种 URL 复用现有 Reader；`--run` 指向进行中的本 Project Run；成功则本 Run scratch 有非空正文 + 稳定 `input_id`；JSON 含 `ok` / `input_id` / `title` / 失败 `code`。**永不**调用 `add_datasource`；Project 数据源列表前后字节级不变（无新行）。
2. **S2**：Skill「Project 运行」要求：读完登记 datasource（索引页）后，对可识别且有 Reader 的外链调用 S1 CLI；无 Reader 跳过并在 Artifact 中说明；单条跟读失败不强制整 Run 失败。能源小样类 Task 的 Artifact 至少一处 `[…](input:…)` 指向跟读得到的企微输入，而不是只复述 URL。
3. Artifact `input:` / `#L…` 校验与 #299 / #343 / #315 对齐：跟读 `input_id` 与登记源同一套引用语法；`datasource_unread` **只**看登记 Data Source，跟读成功不能冒充「已读登记源」。

### 4.2 非目标

- 方案 A：引擎在 `read_notion` / `read_cached_datasource` / `project read` 时自动展开外链。
- 永久自动 `add_datasource`（企微或任何非 Notion）。#388 对**新**永久 DS 仍然 Notion-only。
- 为本票实现墨刀 / ShowDoc / 其它无 Reader 平台。
- 改 Topic `step` / digest / fold 协议。
- 嵌套无限递归；默认一跳，更深见 §5.5 / §8。
- 新 Console 页面或粘贴跟读 UX（本票无用户可见 Console 故事）。
- 新 HTTP API（agent 只走 `python -m kairo`）。L2 若要对称 API，另批，不阻塞 S1/S2。
- 把 Settings 企微 / 腾讯目录 `live` 翻回 True，或重开「添加为数据源」主路径。
- 跨 Run 的 URL 正文缓存（不写 `cache/{ds_id}`，不发明第二套 Project 缓存）。

## 5. 能力与契约（L1）

### 5.1 CLI 合同

**L1 默认名（名称可在实现时保持此字面量，避免并行别名）：**

```
kairo project read-url PROJECT_ID --run RUN_ID --root SERVE_ROOT URL
```

| 项 | 契约 |
|---|---|
| 入口 | 挂在现有 `project` 应用下，与 `context` / `read` / `input` 并列。不是独立脚本；不是 `datasource add` / `datasource read`。 |
| `--run` | **必填**。Run 必须属于该 Project 且 `status=running`。缺省 / 终态 / 错 Project → `invalid_request` 或 `run_closed` / `not_found`（与 `project read` 同一套码）。无 Run 的人工跟读不进任何 Task 证据。 |
| URL | 位置参数；http(s) 企微链为 S1。两端空白 strip；不得含 userinfo 凭据（沿用 `infer_source`）。 |
| 成功退出 | 0；JSON `{ok:true,input_id,title,source_id,type,url,reader,kind,content,version,fetched_at}`。有 `--run` 时与 `project read` 一样附 `numbered_content` / `line_count`（#343）。`type` 恒为 `"url"`。`fetched_at` 为本次拉取时刻；`expires_at` 为 `null`（无 Project 缓存 TTL）。 |
| 失败退出 | 1；JSON `{ok:false,code,error}`。**不**返回成功正文，**不**分配 `input_id`，**不**写 scratch 正文。 |
| 失败码 | 闭集沿用 Reader / 材料层：`permission` / `invalid_link` / `read_failed` / `unsupported_reader` / `not_found` / `invalid_request` / `run_closed` / `material_too_large` / `evidence_failed`。`unsupported_reader` **不得**仅因 `infer_source.live=False` 触发（那是 add 门，见 §5.2）。无法识别平台用 `invalid_link`。 |
| 去重 | 同一 Run、同一 `source_id`、同一 `version` → 返回已有 `input_id`，`read_count+1`（复用 `record_run_input`）。 |
| 禁令 | 实现路径上 **零次** `add_datasource` / `save_project` 写 `datasources`。S1 集成须断言 Project `datasources` id 列表不变。不写 `cache/{ds_id}`。 |

`project read` 行为不变：仍只接受目录 `source_id`，仍禁止把裸 URL 当 `SOURCE_ID`。

### 5.2 复用哪些 Reader

`live` 只约束 **新加永久 DS**（#388）。`read-url` 的门是：`infer_source` 能识别 **且** `read_datasource` 对该 `reader` 有分发。

| URL | `infer_source` 今日 | `read-url` |
|---|---|---|
| 企微 `/doc/` `/sheet/` `/smartsheet/` `/smartpage/`（含 `page.weixin.qq.com`） | wecom + kind，`live=False` | **S1 必须成功路径**（授权 + Reader 可用时） |
| 腾讯 `docs.qq.com` `/sheet/` `/smartsheet/` | tencent，`live=False` | **同一 CLI 可走**（授权且 `cmd` 已配）；**不是** S1 定量项 |
| Notion 页面 URL | notion，`live=True` | 同一 CLI 可走（不 `add_datasource`）；**不是** S1 定量项。已在目录中的同一页应继续用 `project read datasource:…` |
| `mail://` / `imap://` | wecom/imap mail，`live=False` | **S1 不做**。L1 默认：CLI **拒绝**（`invalid_link` 或明确 `unsupported_reader`「不是页外链」），避免把检索串当索引外链。若 peng 改口，再开可选 |
| 墨刀 / ShowDoc / 其它无法识别 http(s) | `invalid_link` | 失败码可判定；Skill 跳过（§5.4） |
| 本地路径 / `file://` | `invalid_link` | 拒绝；永不升格为 DS 或 URL 材料 |

S1 定量：至少 1 个企微 URL（sheet 或 smartsheet 即可代表；四种 kind 单测锁推断 + 分发）在集成或 live 路径得到非空 body + 稳定 `input_id`。无 `wecom-cli` 的 CI 用 stub `cmd` / runner，与 #294 相同；不得用 MCP 代过。

### 5.3 Run inputs 里怎么记账（`source_id` / `type` / 引用 / `datasource_unread`）

**L1 默认假说（待 §8.1 批）：**

| 字段 | URL 材料 | 登记 Data Source |
|---|---|---|
| `type` | `url`（新闭集成员；与 `understanding` / `digest` / `datasource` 并列） | `datasource` |
| `source_id` | `url:` + strip 后的原文字面 URL（`https://…`） | `datasource:{ds_id}` |
| `url` | 该 http(s) URL | 沿用现网（今日写成 `datasource:{id}`；本票不修历史行） |
| `title` | 非空；优先正文首个 Markdown ATX 标题，否则 Reader 标签 + URL 尾段 | DS name / purpose / URL |
| 缓存 | 只在本 Run scratch → 终态 `inputs/{run}` | Project `cache/{ds_id}` + 本 Run 记账 |

因此：

1. Artifact 引用仍是 `[标题](input:INPUT_ID)` 或 `#L3-L5`。宿主只校验 `input_id ∈ 本 Run 已登记 inputs`（#299 / #343）。跟读与登记源同一语法；**不要**发明 `url:` 引用 scheme。
2. `datasource_unread` **保持现网谓词**：只认 `type==datasource` 或 `source_id` 以 `datasource:` 开头。只跟读企微、不 `project read` 索引 Notion → 仍 `datasource_unread`。S2 必须先读登记源。
3. `_source_in_scope` / `validate_recorded_inputs` / `finalize_inputs` **必须**承认 `type=url` 且 `source_id` 匹配 `^url:https?://…$`。否则 S1 写入 scratch 后发布必 `evidence_failed`。URL 材料**不**要求落入 `scope_datasources` id 集（它本来就没有 ds id）。
4. 不把 URL 材料伪装成 `datasource:{合成id}`：那会让 `datasource_unread` 误过，并污染材料身份（#315）。
5. 材料身份（名词表）在本票扩展为：登记 DS、Topic 事实层 / digest、**或本 Run 跟读 URL**。不改 Topic 身份规则。
6. #315 防篡改边界不变：不承诺 agent 不改自己的 scratch；只保证官方 CLI 写下的形状能过发布门，且 `body` 仍落在本 Run 历史目录内。

`project context` **不**列出 URL 材料（目录仍是登记源 + Topic）。跟读不是目录项；Skill 用新口令，而不是把 URL 塞进 `source_id` 再走 `project read`。

### 5.4 Skill 与宿主 prompt

改 `src/kairo/data/SKILL.md` § Project 运行（及 `_execute_agent_run` 注入的那一段，二者必须同语义）：

1. 目录纪律不变：`type=datasource` 仍须先 `project read`。未出现在目录中的来源 **仍不可** `project read`。
2. **新增例外（仅此一口）：** 已读登记源正文里的外链，可用 `project read-url PROJECT --run RUN --root … URL`。不要对目录外 URL 调用 `project read`。
3. 对每个外链：`infer` 能识别且有 Reader（至少企微四种）→ 调用 `read-url`；引用返回的 `input_id`。墨刀 / ShowDoc / `invalid_link` / 无 Reader → **跳过**，在 Artifact 用一句话注明平台与 URL，**不要**编造正文。
4. **单条跟读失败策略（钉死）：** `permission` / `read_failed` / `invalid_link` / `material_too_large` 等只使**该次 CLI** 失败。agent 继续其它链，把失败码与 URL 写进 Artifact。宿主 **不得**因为某次 `read-url` 非零退出而把整 Run 标 failed。整 Run 仍只在现网门失败：`empty_artifact` / `invalid_input_ref` / `invalid_input_location` / `datasource_unread` / `evidence_failed` / `provider_*`。
5. 禁止：为跟读去 `datasource add`；为跟读去 step；跟读失败后用未登记的假 `input_id`（会 `invalid_input_ref`）。

S2 定量：夹具或 live 的能源小样 Artifact 至少一处 `input:` 的 `input_id` 对应 `type=url` 且 URL 为企微。只复述 `https://doc.weixin.qq.com/…` 不算过。

### 5.5 递归

| 层 | 规则 |
|---|---|
| 引擎 `read-url` | **只读这一条 URL**。不解析返回正文、不自动再拉。无「展开」参数。 |
| Skill | **默认一跳**：只跟读已读 **登记 datasource**（及若已读的 Topic 材料）正文中的链接。禁止跟读 `type=url` 材料里的再链。 |
| 更深 | 本票 Out。不要在 Skill 写「可再跟一层」。若产品以后要两跳，另票 + 另批，并先回答 §8.2。 |
| 回路 | agent 对许多 URL 循环调用：引擎不设 hop 计数器（每次调用独立）。体量仍受每份 2 MiB 与 Run 超时（#299 默认 600s）。**不**为防无限递归去改 Topic 协议。 |

方案 A（读 Notion 时引擎展开）会把递归与失败策略做进 Reader，本票明确不做。

### 5.6 授权与权限

- S1 前置：Settings 企微连接 `authorized=True`。真读还要本机 `wecom-cli` 会话或测试 stub `cmd`。**不要**把目录健康 `unavailable`（`live=False`）当成未授权——那是 #388 的 add 门。
- `connection.authorized is False` → `permission`；不写 scratch、不覆盖任何登记源缓存（跟读本来就不写该缓存）。
- 企微 401/403 / 权限失效文案 → 已有 `_PERMISSION_MARKERS` → `permission`。无效路径 / 404 → `invalid_link`。其它非零 / 空正文 → `read_failed`。
- 缺 `wecom-cli` 且未配 stub → `read_failed`（或适配器已有映射），不是假成功。
- 不把凭据写入 Project JSON、Run JSON、inputs 元数据或 Artifact。
- 腾讯可选路径：未授权或缺 `cmd` 与现网 Reader 相同（`permission` / `read_failed`），同样不 `add_datasource`。

## 6. 思路与折衷

**选择：方案 B — 新 CLI 门口 + Skill 一跳跟读 + 本 Run `type=url` 记账；Reader 与 #388 add 门不动。**

| 选择 | 放弃 |
|---|---|
| agent 显式 `read-url` | 放弃方案 A（引擎读 Notion 时展开）。展开把失败 / 递归 / 无 Reader 策略藏进 Reader，难与 `datasource_unread` 和解，且 Out 已写死 |
| 本 Run inputs only | 放弃自动 `add_datasource`。否则破坏 #388 S2（新加仅 Notion） |
| `type=url` + `source_id=url:https://…` | 放弃伪装 `datasource:`（会骗过 `datasource_unread`）；放弃不改 `_source_in_scope`（发布必炸） |
| `live` 只挡 add | 放弃把 `live=False` 当成「不能读」。否则 S1 对企微永远 `unsupported_reader` |
| Skill 一跳 + 单条失败不翻整 Run | 放弃「跟读失败即 Task failed」；放弃引擎硬解析 Notion 白名单 URL |
| 无 Console | 放弃本票 Atlas 故事 |

代价：跟读范围靠 Skill，不靠引擎解析索引页。恶意 / 糊涂的 agent 仍可对任意可识别 URL 调 `read-url`（本机已授权的连接范围内）。#299 已承认「通用 shell ≠ OS 隔离」。只改 Skill、不改 `_source_in_scope`，S1 写入后 S2 发布失败。只翻 CLI、不改 Skill 第 6 条「目录外不可读」，Grok/Codex 不会跟读。

**证明边界：** S1/S2 只认 kairo CLI（及由其驱动的 agent Run）。MCP / 手工 `wecom-cli` 演示不算过线。默认 CI 不打真企微网（#294）。

## 7. 模块边界（所有权）

| 面 | Owner | 变化方向 |
|---|---|---|
| `project read-url`；`read_url_material`（名以实现为准）；`record_run_input` 的 `type=url`；`parse_source_id` / `_source_in_scope` / `finalize_inputs` | Forge（CLI / engine） | 新门口 + 发布门认 URL 材料；零 `add_datasource`；不写 DS cache |
| Skill § Project 运行；`_execute_agent_run` prompt | Forge | 一跳跟读 + 跳过无 Reader + 单条失败不翻 Run |
| `infer_source` `live` 标志；`add_datasource` 写门；Settings 目录 live | Forge | **不改**（#388 仍有效） |
| `read_wecom_docs` 等 Reader 本体 | Forge | **不改协议**；S1 只接线调用 |
| Console 粘贴框 / 数据源表 / i18n | Atlas | **无本票 UX**。不要为跟读加「添加企微源」入口 |
| verify-kairo-web | Atlas | 无新 Console Story；勿对 live root 触发 LLM Run |

## 8. 开放问题（待 peng 批；不自批）

实现前请批下列默认。禁止实现时并行 fallback。

### 8.1 `source_id` 形状

| 案 | 做法 | 风险 |
|---|---|---|
| **A（L1 默认假说）** | `url:` + strip 后的原文字面 http(s) URL | 长 URL；查询串差异视为不同源（fail-fast，不静默规范化） |
| B | `url:` + URL 的 SHA-256；`url` 字段存原文 | 更不透明，对齐 #299「原样回传」；调试要对照 `url` 字段 |
| C | 复用 `datasource:{合成id}` 或把跟读写进 `datasources` | 破坏 #388 与 `datasource_unread`。禁止 |

**已锁定：A。** C 已否。

### 8.2 一跳是否引擎强制

| 案 | 做法 |
|---|---|
| **A（L1 默认假说）** | 引擎不解析「该 URL 是否出现在已读登记源正文」。Skill 写死一跳。引擎只保证单次 `read-url` 不展开 |
| B | 引擎维护本 Run「已读登记源正文」URL 白名单，`read-url` 不在名单则 `invalid_request` |

B 更严，但要在材料层解析 Markdown / 裸 URL，漏链则假失败。**已锁定：A。**

### 8.3 每 Run 跟读条数上限

| 案 | 做法 |
|---|---|
| **A（L1 默认假说）** | 无条数硬顶；靠 2 MiB / Run 超时。Skill 一跳通常远小于超时 |
| B | 硬顶 N（例如 20）；超出 `invalid_request`，已成功的跟读保留 |

**已锁定：A。** 不要默默截断。

### 8.4 腾讯 / Notion / 邮件是否同一 CLI

§5.2 默认：企微 = S1；腾讯与 Notion 页 = 同一命令可走、非 S1 定量；`mail://` / `imap://` = 拒绝。

**已锁定：A（确认默认）。** 企微 = S1；腾讯与 Notion 页 = 同一命令可走、非 S1 定量；`mail://` / `imap://` = 拒绝。

### 8.5 S2 证明：live 企微 vs 夹具

| 案 | 做法 |
|---|---|
| **A（L1 默认假说）** | CI：stub Reader + 确定性 provider，锁 CLI JSON、`datasources` 不变、`type=url` 过 `finalize_inputs`、Skill 字面量含 `read-url`、Artifact 含跟读 `input:`。产品 S2：本机企微已授权时至少 1 次 live（或能源小样等价）。默认 CI **不**打真企微网 |
| B | 只靠夹具声称 S2 产品过线 |

**已锁定：A。** CI stub；产品 live 企微可在无会话的 CI 中跳过并写明原因。

## 9. 验收映射

- S1 ← §5.1 + §5.2 企微行 + §5.3 + §5.6（CLI、Reader、本 Run 记账、不 `add_datasource`、授权）
- S2 ← §5.4 + §5.5（Skill 一跳、跳过无 Reader、单条失败策略、Artifact 引用跟读 `input:`）

`datasource_unread` 回归（只读跟读、不读登记 Notion → 仍失败）是 S1/S2 的门，不是第三条 Story。

S1/S2 只认 kairo CLI（及适用的 agent Run）。L1 已批（peng；defaults A）。实现归 Forge engine / CLI / Skill；无 Atlas Console。无 keel-dev。

## 10. 关联

- Issue [#392](https://github.com/xforce-io/kairo/issues/392)
- [#388](388-notion-project-datasource.md) 新永久 DS 仅 Notion；`live` 是 add 门
- [#390](390-grok-project-agent.md) Grok Project agent（`supports_project_cli`）
- [#294](294-wecom-docs-reader.md) 企微四种 kind 与 Reader
- [#299](299-project-context-task.md) Project CLI、`input_id`、Skill
- [#315](315-run-input-evidence-bound.md) `_source_in_scope` / 证据路径
- [#343](343-artifact-input-location.md) `input:ID#L…`
- 发现现场：能源 Notion 索引 E2E（#388 / #390）

## 11. 验收表

Frozen SHA: `45e3210ff7c7f05e7bc17794729a29031c14bd9b`（CLI / Skill / 记账）+ 本分支 tip（S2 夹具）。实现归 Forge；无 Console。

| Story | 结果 | 证据 |
|---|---|---|
| S1 CLI 企微 stub：成功 JSON、`type=url`、`input_id`、四种 kind；`live=False` 不是 `unsupported_reader` | **PASS** | `tests/test_project_read_url.py::test_cli_wecom_read_url_success_and_datasources_unchanged`、`test_cli_wecom_four_kinds_live_false_is_not_unsupported` |
| S1 不 `add_datasource` / `datasources` 不变 / 不写 `cache/{ds_id}` | **PASS** | 同上；`read_url_material` 源码无 `add_datasource` / `write_cache` / `save_project` |
| S1 `finalize_inputs` 承认 `type=url`；邮件拒绝；墨刀/ShowDoc `invalid_link`；失败无 `input_id`、无 scratch 正文 | **PASS** | `test_finalize_inputs_accepts_type_url`、`test_mail_and_modao_rejected_without_scratch_body` |
| S1 门：只跟读、不读登记 DS → 仍 `datasource_unread` | **PASS** | `test_datasource_unread_still_fails_if_only_url_inputs` |
| S2 Skill / `_execute_agent_run` prompt 含 `read-url`、一跳、跳过无 Reader、单条失败不翻 Run | **PASS** | `src/kairo/data/SKILL.md`；`test_skill_and_prompt_contain_read_url`；`tests/test_skill_kairo.py::test_skill_covers_project_run_cli` |
| S2 夹具 Artifact 含跟读 `input:`（`type=url`、企微 URL） | **PASS** | `test_s2_fixture_artifact_cites_url_input`（stub Reader + 确定性 provider） |
| S2 产品 live 企微 | **SKIP（CI）** | §8.5 A：默认 CI 不打真企微网。本环境无已授权 `wecom-cli` 会话，不能声称产品 live 过线。 |
