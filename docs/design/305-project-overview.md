# #305 Project 概览入口

- Issue：[#305](https://github.com/xforce-io/kairo/issues/305)
- L1：[提案与批准依据](https://github.com/xforce-io/kairo/issues/305#issuecomment-5552242504)；用户于 2026-09-05 回复「Approved」。
- 分支：`feat/305-project-overview`
- 状态：Approved（2026-09-05）
- 日期：2026-09-05

本文件是详细设计唯一事实源。Issue 仅保留不超过 10 行的设计摘要与链接。

## 1. 背景

[#305](https://github.com/xforce-io/kairo/issues/305) 要求进入 Project 先能判断状态并进入 Task 或最近结果。当前页按主题、全量 Ref、Data Source、Task、Run 顺序堆叠；21 条资料把创建入口推到首屏之外。Data Source 以平台名和 URL 为主标题，缺少可维护名称。

## 2. 名词解释

Project、Topic、Ref、Data Source、Task、Run、Artifact 以[名词表](../glossary.md)为准。本设计「概览」指 Project 默认页的主入口区块，不是新对象。「Data Source 名称」是用户可维护的显示名，缺省回退用途，再回退 Reader 平台名，不以 URL 当主标题。

## 3. 目标与非目标

### 3.1 目标

- S1：无 Task 样例在 1280×720 首屏可见并进入创建 Task。
- S2：已有 Task/Run 时首屏可进入对应 Task、Run 或最近成功 Artifact；增加 Ref 不推远该入口。
- S3：完整材料列表可搜索、排序并打开目标；Data Source 可通过可维护名称及用途区分。

### 3.2 非目标

不实现 Task 编辑/Run 执行逻辑（#306）、证据链（#304）、调度（#307）、全站导航或 Settings。不虚构总体完成率。

## 4. 能力

### 4.1 UI/UX

默认页自上而下：标题 → 主入口（无 Task 时创建表单；有工作时 Task 列表与最近 Run/Artifact）→ 材料摘要（Topic/Ref/Data Source 计数）→ 完整材料（关联 Topic、可搜索排序的 Ref 列表、带名称的 Data Source）。窄屏同序单列。

| 状态 | 主入口 | 材料 |
|---|---|---|
| 空 Task | 创建表单在 `#project-primary` | 摘要 + 完整列表在 `#project-materials` |
| 有 Task/Run | Task 行与最近 Run/Artifact 链接 | 列表增长不改变 primary 在 DOM 中的位置 |
| Data Source 无名称 | 显示用途或平台名 | URL 仅作辅助/title |
| 材料不可用 | 打开入口仍在，状态沿用缓存文案 | 解除关联不删 Topic/Ref 内容 |

不做：聊天、工作流画布、Project 专属 Settings。

## 5. 思路与折衷

把工作入口从资料堆底部提前，完整列表保留检索而不是删掉。放弃多栏工作台。名称是可选存数，旧记录无 `name` 时回退 `purpose` 再 Reader 标签，避免依赖外部 Reader 才能进页。

## 6. 架构

Web 模板调整区块顺序；`DataSource.name` 写入 Project JSON；领域函数提供显示名回退。主路径：打开概览 → 主入口操作或进入材料检索。失败路径：表单错误仍在本页；材料打开失败沿用既有正文页。

## 7. 模块

`kairo.projects.DataSource` 增 `name`；`datasource_label` 回退；`add_datasource` / `edit_datasource`；`project.html` 重排；i18n 文案。

## 8. API/CLI

`POST /projects/{id}/datasources` 增加可选 `name`。`POST /projects/{id}/datasources/{ds}/edit` 保存名称/用途。CLI `datasource add --name` 可选。无新公开 JSON 字段强制；序列化带 `name`，缺省空字符串。

## 9. 边界

保留直达 Topic/Ref/Run 链接。改名与解除关联不删除内容。名称不要求授权即可展示。

## 10. 迁移/兼容/回滚

旧 Data Source 无 `name` 时按空字符串读取。回退显示不改存数。回滚还原模板与字段忽略多余 `name`（`extra=forbid` 前须保证旧代码不读新文件，或回滚同时恢复 JSON）。新字段对旧代码：Pydantic forbid 会拒读。发布需与 Web 同发；回滚同时恢复 `project.json` 备份或从 name 字段剥离。为降低风险，`model_config extra` 保持 forbid，升级后的 Project 文件含 name；回滚程序需能忽略未知字段或同步回滚数据。本实现写入 name 默认 `""`，旧文件无该键时 Pydantic 用默认空串，**旧代码读新文件会因 extra=forbid 失败若旧模型无 name 字段**。因此回滚必须配对代码与数据，或接受暂时不能用旧二进制打开已写 name 的 Project。

选择：新字段有默认值，旧文件可读；新文件含 name，旧二进制会因 extra key 拒读。回滚时恢复代码前先不写新 name 或接受该窗口。文档如实记录。

## 11. 测试计划

| 层级 / 验收 | 路径与可判定结果 |
|---|---|
| E2E/S1 | TestClient：无 Task 页 `#project-primary` 含创建表单且位于 `#project-materials` 之前 |
| E2E/S2 | 有 Task 与成功 Run 时 primary 含 Task/Artifact 链接，且仍在 materials 之前 |
| E2E/S3 | 材料列表含搜索框；保存名称后刷新仍显示该名称，URL 不是主标题 |
| Integration | 旧无 name 记录回退显示；解除关联不删 Ref |

浏览器 1280×720 作为补充观察，不以截图替代 TestClient 顺序断言。

## 12. 开放问题

无。Task 详情页字段归 #306。

## 13. 关联

- 验收：[#305](https://github.com/xforce-io/kairo/issues/305)
- 前序：[299-project-context-task.md](./299-project-context-task.md)、[232-project-settings-ia.md](./232-project-settings-ia.md)
- 同轮：#303、#304、#306、#307
