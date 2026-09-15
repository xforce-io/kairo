# 【Project】数据源只认一页 Notion

- Issue: [#388](https://github.com/xforce-io/kairo/issues/388)
- 分支: `feat/388-notion-project-datasource`
- 状态: Draft
- 最后更新: 2026-09-15（折入 Forge keel-how / S2 假说）
- L1: Draft（本文件 + Issue 评论；待 peng 批准；不自批）

本文件是 #388 的 L1 提案事实源。Issue 只保留摘要与本链接。不自批。

## 1. 背景

Project 今日把企微文档、腾讯文档、邮件检索等分别加成 Data Source，每种平台各有 Reader 与连接。维护成本高，也和「项目里本来就有一页材料清单」的用法重复。现网机制见 §2。

产品拍板：一个 Project 只需配**一页 Notion** 作为 Data Source；页内外部链接不登记成独立数据源；本地 md 不做数据源类型。

关联：#232 / #294 / #299 / #337（后两者本期不再作为**新**数据源主路径）。

## 2. 现有机制（keel-how · Forge）

对照 `main` tip `0bb3f1a8`（契约边界引用，非实现清单）：

- `src/kairo/projects.py` — `DataSource`；`add_datasource` 先 `infer_source`，仅当返回 `live=False` 才抛 `unsupported_reader`（「尚未接入」）再落盘；`run_task` 的 agent 路径走 `_execute_agent_run`。
- `src/kairo/readers.py` — `infer_source`（纯函数、不访问网络）；`READER_NOTION` 为 stub。Notion 主机（`notion.so` / `*.notion.so` / `notion.site` / `*.notion.site`）**直接** `unsupported_reader`「Notion Reader 尚未接入」，不返回 `InferredSource`。`read_datasource` 无 Notion 分支（未知 Reader → `read_failed`）。
- `src/kairo/project_materials.py` — `read_cached_datasource`：权限失败在 `write_cache` 之前抛出，不覆盖已有成功缓存；`list_context` 把 datasource 放目录最前；`finalize_inputs` 归档 Run 输入证据。
- `src/kairo/settings.py` — `READER_CATALOG`：tencent / wecom / imap `live=True`；`CONNECTION_NOTION` `live=False`（健康面 `unavailable`）；凭据只引 `NOTION_TOKEN` 名。
- `src/kairo/cli.py` — `datasource_add` / `datasource_read` / `datasource_rm`；`settings_show` / `settings_set`。

今日事实：企微 / 腾讯 / 邮件 **仍可 add+read live**。本地 md（`file://` 或裸路径）已经是 `invalid_link`（「不是有效链接」），不是 Data Source 类型。Notion 新加失败是「尚未接入」stub，**不是**「只允许 Notion」白名单。只把 Notion 接上而不关掉另三条新加路径，**过不了 S2**。

## 3. 名词解释

沿用 [docs/glossary.md](../glossary.md) 的 Project、Data Source、Task、Run、Artifact。本票用语：

| 用语 | 本票含义 |
|---|---|
| Notion 页面 URL | 可被 `infer_source` 识别为 Notion 主机的单页 HTTP(S) 链接（`notion.so` / `*.notion.so` / `notion.site` / `*.notion.site`） |
| 新数据源 | 经 Console/API `add_datasource` **新写入** Project 的条目；不含票前已存在的非 Notion 存数 |
| 页内外部链接 | Notion 正文里的企微/腾讯/其它 URL；可读入正文，但不得再加成 Data Source |

## 4. 目标与非目标

### 4.1 目标

1. Settings 可授权 / 撤销 Notion 连接（凭据仍只引用 `NOTION_TOKEN` 环境变量名，不写 token 值）。
2. 粘贴合法 Notion 页面 URL 可添加为 Data Source；Reader 显示为 Notion；读取得到非空正文，可在 Console 打开。
3. 未授权时读取失败原因为权限类（`permission`），且不覆盖已有成功缓存正文。
4. 企微文档链接、腾讯文档表格链接、邮件查询串（`mail://` / `imap://`）、本地 md 路径：**添加为新数据源一律失败**；列表无对应新行；原因可判定（未接入 / 不支持 / 无效链接）；无假成功。
5. Project **仅有**该 Notion Data Source 时，可创建并跑通 1 次 agent Task；succeeded Artifact 可打开，来源含该 Notion 数据源；页内外部链接未配成 Data Source 不阻止成功。

### 4.2 非目标

- 不把本地 md 做成 Data Source 类型。
- 不为企微 / 腾讯 / IMAP 保留「添加为数据源」主路径（既有存数不迁移、不删除）。
- 不承诺页内外部链接的正文级 / 表级证据抽取。
- 不同步整个 Notion 工作区、不向 Notion 写入。
- 不改 Topic 的 digest / fold。
- 不引入 OIDC / #120 文档三档权限。

## 5. 能力与契约（L1）

### 5.1 Settings · Notion 连接

- 目录项 `notion` 从「未接入 / unavailable」变为可授权连接：`authorized` + 环境变量 `NOTION_TOKEN` 齐备时健康为 authorized。
- 撤销授权后：已有 Notion Data Source 的**下一次**读取按权限失败处理；成功缓存正文不被失败读取覆盖（与现行 `read_cached_datasource` / cache 语义对齐，实现细节归 L2 / engine）。

### 5.2 添加 Data Source

| 输入 | 结果 |
|---|---|
| 合法 Notion 页面 URL | 添加成功；`reader=notion`；可进入读取 |
| 企微文档 URL | 添加失败；列表无新行；可判定原因 |
| 腾讯文档表格 / 智能表 URL | 同上 |
| `mail://` / `imap://` 检索串 | 同上 |
| 本地 md 路径（含 `file://` 或裸路径） | 同上；永不成为 Data Source 类型 |
| 无法识别的 http(s) | 无效链接失败（保持可判定） |

「拒绝新增强」的产品含义：Console 粘贴框与 `POST .../datasources`（含 API）对上述非 Notion 输入不得成功写入。票前已落盘的非 Notion Data Source **仍可列出 / 读取 / 删除**（Out：不迁移不删除）；只是不能再**新加**。

**S2 策略（Forge 假说，待 L1 批，不自批）：**

1. **白名单落在 `readers.infer_source`**：新推断只有 Notion 保持 live；企微 / 腾讯 / `mail://` / `imap://` 对新输入不再返回 `live=True`（失败码可判定：`unsupported_reader` / `invalid_link`）。
2. **`add_datasource` 仍是写门**：推断失败或非 live 不得落盘；Console / CLI / API 共用此门。
3. **Settings 目录翻转**：`CONNECTION_NOTION` `live=True`；wecom / tencent / imap `live=False`（可添加 UX / 健康面 unavailable）。存量行仍可列出 / 读取 / 删除。

单独把 Notion Reader 接上不够 S2——今日企微 / 腾讯 / 邮件 add 会成功，必须新拒绝。

### 5.3 读取

- 授权且 token 可用：读取 Notion 页面正文为非空文本（允许结构化转 markdown / 纯文本；L2 钉格式）。
- 未授权或缺 token：`permission`；不覆盖旧成功正文。
- 无效 / 无权限页面：`invalid_link` 或 `permission`（与现行 Reader 错误码闭集一致：`permission` / `invalid_link` / `read_failed` / `unsupported_reader`）。

### 5.4 Task / 追溯

- 仅绑定该 Notion Data Source 的 agent Task 可手动触发 Run。
- 成功 Artifact 可打开；来源 / 证据列表含该 Notion 数据源（不要求页内企微/腾讯链接也登记为 Data Source）。
- 不要求改 Topic 材料路径。

### 5.5 Console 文案

- 粘贴提示从「文档链接或 mail:// / imap://」改为引导 Notion 页面 URL（i18n EN/ZH）。
- Reader 标签出现 Notion；拒绝类错误对人可读。

## 6. 思路与折衷

**选择：复用现有 Data Source + Reader + Settings 连接模型，只把「可新加」收敛到 Notion，并实现 Notion 读取。** 放弃并行维护企微/腾讯/邮件为新主路径；放弃 md 数据源。

S2 不靠「接上 Notion 后其它路径自然消失」。Forge 假说（待批）：`infer_source` 对**新**推断做 Notion-only 白名单 + `add_datasource` 写门 + Settings 目录 `live` 翻转（Notion 开、wecom/tencent/imap 关可添加 UX）。存量非 Notion 行不迁移、不删除，读路径可继续服务已落盘行。

代价：存量非 Notion 源仍留在磁盘与 UI 列表中，直到用户手删或后续票处理。页内链接不升格为源，Task 证据边界以 Notion 页正文为准。只接线 Notion、不关今日仍 live 的三条 add 路径，S2 失败。

## 7. 模块边界（所有权）

| 面 | Owner | 变化方向 |
|---|---|---|
| Settings UI / i18n / Project 数据源表单与预览 | Atlas（Web Console） | Notion 可授权；粘贴引导；错误展示；预览打开 |
| `infer_source` / Notion `read_*` / 拒绝新增强策略 / cache 不覆盖 | Forge（engine） | Notion live；非 Notion 新加失败码；读取与权限 |
| agent Task Run 证据绑定 Data Source | Forge | 保证仅 Notion 源可跑通并出现在来源中（若现状已满足则 L2 注明） |
| verify-kairo-web 功能地图 | Atlas | S1–S3 Drive（scratch；不触发不必要 LLM——S3 若必须 Run 则 L2 / verify 手册单列） |

## 8. 开放问题（待 L1 批 / L2）

1. Notion 集成形态：仅官方 API + `NOTION_TOKEN`，还是允许可选 `cmd` 适配器（与企微类似）？L1 默认：**仅 token 环境变量 + 官方 API**。
2. 「一页」是否强制每 Project 最多 1 条 Notion Data Source，还是允许多页但产品文案强调一页？L1 默认：**允许添加多条 Notion URL**（与现 `datasources` 列表模型一致）；验收以「至少一页跑通」为准，不强制唯一性约束。
3. S3 verify：agent Run 是否允许在 scratch 用假 provider / 录制夹具，避免真 LLM？交 verify 手册与 Forge 夹具约定。

## 9. 验收映射

- S1 ← §5.1–5.3
- S2 ← §5.2
- S3 ← §5.4
