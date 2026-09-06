# #337 邮件检索 Data Source

- Issue: [#337](https://github.com/xforce-io/kairo/issues/337)
- 状态: Draft
- 日期: 2026-09-06

本文件是详细设计唯一事实源。Issue 只保留摘要与本链接。

## 1 背景

链 [#337](https://github.com/xforce-io/kairo/issues/337)。Project Task 已能读企微/腾讯文档。评审会、TR 结论常在邮件里，不一定是企微邮箱，也可能是普通 IMAP 邮箱。现行 `infer_source` 只认 http(s) 文档链接。

## 2 名词解释

已有 Data Source、Reader、连接见 [名词表](../glossary.md)。本设计新增：

| 规范名 | 一句话定义 | 禁止别称 |
|---|---|---|
| 邮件检索 | Project Data Source 的一种 kind：按查询条件只读检索邮箱，结果为 Markdown。 | 邮箱同步、邮件 Ref |
| 邮件查询串 | 不含密码的检索链接，用于推断 Reader 与查询条件。 | 邮件 URL、邮箱地址 |

## 3 目标与非目标

### 3.1 目标

- 用户能把一条邮件检索条件配成 Data Source。
- 支持两种 Reader：已授权的企微连接；已授权的 IMAP 连接。
- 读取结果为 Markdown（时间、发件人、主题、正文；附件只列名）。
- Agent Task 能按现有材料目录引用该源。
- 失败码与现有 Data Source 一致：permission / invalid_link / read_failed。

### 3.2 非目标

- Gmail / Outlook 专用 OAuth API（可用其 IMAP 入口则走 IMAP）。
- 全量同步邮箱、一封信一个 Data Source。
- 把邮件 fold 进 Topic 或 Timeline 日程。
- 发信、回复、转发、删信、改已读。
- 下载或解析附件正文。
- 凭据写入 Project 或查询串。

## 4 能力

### 4.1 UI/UX

Project 添加数据源输入接受邮件查询串（不再用 HTML `type=url` 挡住非 http 方案）。列表 Reader 文案：企微邮件 / IMAP 邮件。读取预览与表格源相同。空命中展示「无命中邮件」，不算失败。

无页面的 CLI/API 与 Web 同一推断与失败码。

## 5 思路与折衷

保留「链接推断 Reader」，不把查询条件拆成一堆表单字段。查询串不含密码。

放弃：只做企微邮件（评审也可能在普通邮箱）；用 `https://mail.qq.com` 当源（无法表达检索条件且含会话）；IMAP 密码写进 URL userinfo（与现有「链接不得含凭据」冲突）。

企微走 `wecom-cli mail search/get`，IMAP 走标准 IMAP SEARCH + FETCH。二者产出同一 Markdown 形状。

## 6 架构

分层：`readers.infer_source` / `read_datasource` → Project 缓存 → Task 材料目录。

主路径：授权连接 → 粘贴查询串 → 推断 kind=`mail-search` → 检索 → Markdown → 缓存 → Task 引用。

失败路径：未授权或缺密码环境变量 → permission；方案/查询非法或含密码 → invalid_link；检索/拉取失败 → read_failed。空命中成功返回空列表正文。

```mermaid
flowchart TD
  URL[查询串] --> Infer[infer_source]
  Infer -->|mail://wecom| Wecom[wecom Reader]
  Infer -->|imap://user@host| Imap[imap Reader]
  Wecom --> MD[Markdown]
  Imap --> MD
  MD --> Cache[Data Source 缓存]
  Cache --> Task[Agent Task 材料目录]
```

## 7 模块

- `kairo.readers`：推断、校验、企微邮件读取、IMAP 读取、统一 Markdown。
- `kairo.settings`：IMAP 连接条目（`IMAP_PASSWORD`）。
- `kairo.projects`：kind 允许 `mail-search`。
- Web：粘贴框接受查询串；Reader 文案。

## 8 API/CLI

查询串：

- 企微：`mail://wecom/inbox?keywords=评审会,TR1&begin=2026-08-24&only_subject=1&limit=20`
- IMAP：`imap://user@imap.example.com/INBOX?keywords=评审会&begin=2026-08-24&limit=20`

查询参数：`keywords`（逗号分隔）、`begin`/`end`（`YYYY-MM-DD`）、`only_subject`（`1`/`true`）、`limit`（1–50，默认 20）。IMAP 路径为文件夹，缺省 `INBOX`。用户名可出现在 IMAP URL 中；密码禁止出现，只读 `connections.imap.token_env`（默认 `IMAP_PASSWORD`）。

`add_datasource` / 读取 API 不改路径，只扩展推断。public-read 仍无 Project。

## 9 边界

- 只读。读失败不得调用发送或删除。
- 凭据不进 Project JSON。
- 邮件不成为 Topic Ref，不 fold。
- 与表格冲突时由 Task prompt 决定；本设计不规定业务优先级。

## 10 迁移/兼容/回滚

无既有邮件源。回滚：去掉 `mail-search` 推断后，旧查询串变为 invalid_link；既有文档源不受影响。IMAP 连接缺省未授权。

## 11 测试计划

- **Unit**：`infer_source` 识别 `mail://wecom` 与 `imap://user@host`；含密码 userinfo 拒绝且不落盘；非法方案 invalid_link。
- **Unit**：企微 search/get 经 stub runner 产出含主题与正文的 Markdown；零封命中为「无命中邮件」。
- **Unit**：IMAP 经注入 mailbox 产出同样形状；未授权与缺密码为 permission。
- **Integration**：`add_datasource` 写入 kind=`mail-search`；Agent 材料目录可登记该源。

## 12 开放问题

N/A。Gmail 专用 API 明确不做，走 IMAP 即可。

## 13 关联

- [#337](https://github.com/xforce-io/kairo/issues/337)
- [#235](https://github.com/xforce-io/kairo/issues/235)
- [#232](https://github.com/xforce-io/kairo/issues/232)
