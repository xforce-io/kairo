# 【Project】数据源只认一页 Notion

- Issue: [#388](https://github.com/xforce-io/kairo/issues/388)
- 分支: `feat/388-notion-project-datasource`
- 状态: Draft
- 最后更新: 2026-09-15
- L1: Draft（本文件 + Issue 评论；待 peng 批准；不自批）

本文件是 #388 的 L1 提案事实源。Issue 只保留摘要与本链接。不自批。

## 1. 背景

Project 今日把企微文档、腾讯文档、邮件检索等分别加成 Data Source，每种平台各有 Reader 与连接（见 `src/kairo/readers.py`、`src/kairo/settings.py` READER_CATALOG）。维护成本高，也和「项目里本来就有一页材料清单」的用法重复。

产品拍板：一个 Project 只需配**一页 Notion** 作为 Data Source；页内外部链接不登记成独立数据源；本地 md 不做数据源类型。现网粘贴 `https://www.notion.so/...` 会因 `infer_source` 抛 `unsupported_reader`（「Notion Reader 尚未接入」）而失败；Settings 目录里已有 `notion` 连接位（`live=False`，`NOTION_TOKEN`）。

关联：#232 / #294 / #299 / #337（后两者本期不再作为**新**数据源主路径）。

## 2. 名词解释

沿用 [docs/glossary.md](../glossary.md) 的 Project、Data Source、Task、Run、Artifact。本票用语：

| 用语 | 本票含义 |
|---|---|
| Notion 页面 URL | 可被 `infer_source` 识别为 Notion 主机的单页 HTTP(S) 链接（`notion.so` / `*.notion.so` / `notion.site` / `*.notion.site`） |
| 新数据源 | 经 Console/API `add_datasource` **新写入** Project 的条目；不含票前已存在的非 Notion 存数 |
| 页内外部链接 | Notion 正文里的企微/腾讯/其它 URL；可读入正文，但不得再加成 Data Source |

## 3. 目标与非目标

### 3.1 目标

1. Settings 可授权 / 撤销 Notion 连接（凭据仍只引用 `NOTION_TOKEN` 环境变量名，不写 token 值）。
2. 粘贴合法 Notion 页面 URL 可添加为 Data Source；Reader 显示为 Notion；读取得到非空正文，可在 Console 打开。
3. 未授权时读取失败原因为权限类（`permission`），且不覆盖已有成功缓存正文。
4. 企微文档链接、腾讯文档表格链接、邮件查询串（`mail://` / `imap://`）、本地 md 路径：**添加为新数据源一律失败**；列表无对应新行；原因可判定（未接入 / 不支持 / 无效链接）；无假成功。
5. Project **仅有**该 Notion Data Source 时，可创建并跑通 1 次 agent Task；succeeded Artifact 可打开，来源含该 Notion 数据源；页内外部链接未配成 Data Source 不阻止成功。

### 3.2 非目标

- 不把本地 md 做成 Data Source 类型。
- 不为企微 / 腾讯 / IMAP 保留「添加为数据源」主路径（既有存数不迁移、不删除）。
- 不承诺页内外部链接的正文级 / 表级证据抽取。
- 不同步整个 Notion 工作区、不向 Notion 写入。
- 不改 Topic 的 digest / fold。
- 不引入 OIDC / #120 文档三档权限。

## 4. 能力与契约（L1）

### 4.1 Settings · Notion 连接

- 目录项 `notion` 从「未接入 / unavailable」变为可授权连接：`authorized` + 环境变量 `NOTION_TOKEN` 齐备时健康为 authorized。
- 撤销授权后：已有 Notion Data Source 的**下一次**读取按权限失败处理；成功缓存正文不被失败读取覆盖（与现行 `read_project_datasource` / cache 语义对齐，实现细节归 L2 / engine）。

### 4.2 添加 Data Source

| 输入 | 结果 |
|---|---|
| 合法 Notion 页面 URL | 添加成功；`reader=notion`；可进入读取 |
| 企微文档 URL | 添加失败；列表无新行；可判定原因 |
| 腾讯文档表格 / 智能表 URL | 同上 |
| `mail://` / `imap://` 检索串 | 同上 |
| 本地 md 路径（含 `file://` 或裸路径） | 同上；永不成为 Data Source 类型 |
| 无法识别的 http(s) | 无效链接失败（保持可判定） |

「拒绝新增强」的产品含义：Console 粘贴框与 `POST .../datasources`（含 API）对上述非 Notion 输入不得成功写入。票前已落盘的非 Notion Data Source **仍可列出 / 读取 / 删除**（Out：不迁移不删除）；只是不能再**新加**。

### 4.3 读取

- 授权且 token 可用：读取 Notion 页面正文为非空文本（允许结构化转 markdown / 纯文本；L2 钉格式）。
- 未授权或缺 token：`permission`；不覆盖旧成功正文。
- 无效 / 无权限页面：`invalid_link` 或 `permission`（与现行 Reader 错误码闭集一致：`permission` / `invalid_link` / `read_failed` / `unsupported_reader`）。

### 4.4 Task / 追溯

- 仅绑定该 Notion Data Source 的 agent Task 可手动触发 Run。
- 成功 Artifact 可打开；来源 / 证据列表含该 Notion 数据源（不要求页内企微/腾讯链接也登记为 Data Source）。
- 不要求改 Topic 材料路径。

### 4.5 Console 文案

- 粘贴提示从「文档链接或 mail:// / imap://」改为引导 Notion 页面 URL（i18n EN/ZH）。
- Reader 标签出现 Notion；拒绝类错误对人可读。

## 5. 思路与折衷

**选择：复用现有 Data Source + Reader + Settings 连接模型，只把「可新加」收敛到 Notion，并实现 Notion 读取。** 放弃并行维护企微/腾讯/邮件为新主路径；放弃 md 数据源。

代价：存量非 Notion 源仍留在磁盘与 UI 列表中，直到用户手删或后续票处理。页内链接不升格为源，Task 证据边界以 Notion 页正文为准。

## 6. 模块边界（所有权）

| 面 | Owner | 变化方向 |
|---|---|---|
| Settings UI / i18n / Project 数据源表单与预览 | Atlas（Web Console） | Notion 可授权；粘贴引导；错误展示；预览打开 |
| `infer_source` / Notion `read_*` / 拒绝新增强策略 / cache 不覆盖 | Forge（engine） | Notion live；非 Notion 新加失败码；读取与权限 |
| agent Task Run 证据绑定 Data Source | Forge | 保证仅 Notion 源可跑通并出现在来源中（若现状已满足则 L2 注明） |
| verify-kairo-web 功能地图 | Atlas | S1–S3 Drive（scratch；不触发不必要 LLM——S3 若必须 Run 则 L2 / verify 手册单列） |

## 7. 开放问题（待 L1 批 / L2）

1. Notion 集成形态：仅官方 API + `NOTION_TOKEN`，还是允许可选 `cmd` 适配器（与企微类似）？L1 默认：**仅 token 环境变量 + 官方 API**。
2. 「一页」是否强制每 Project 最多 1 条 Notion Data Source，还是允许多页但产品文案强调一页？L1 默认：**允许添加多条 Notion URL**（与现 `datasources` 列表模型一致）；验收以「至少一页跑通」为准，不强制唯一性约束。
3. S3 verify：agent Run 是否允许在 scratch 用假 provider / 录制夹具，避免真 LLM？交 verify 手册与 Forge 夹具约定。

## 8. 验收映射

- S1 ← §4.1–4.3
- S2 ← §4.2
- S3 ← §4.4
