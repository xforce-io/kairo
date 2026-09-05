# #306 Task 查看、编辑与运行闭环

- Issue：[#306](https://github.com/xforce-io/kairo/issues/306)
- L1：[提案与批准依据](https://github.com/xforce-io/kairo/issues/306#issuecomment-5552242969)；用户于 2026-09-05 回复「Approved」。
- 分支：`feat/306-project-task-workflow`
- 状态：Approved（2026-09-05）
- 日期：2026-09-05

本文件是详细设计唯一事实源。Issue 仅保留不超过 10 行的设计摘要与链接。

## 1. 背景

[#306](https://github.com/xforce-io/kairo/issues/306) 要求补齐查看定义、改版本、运行、失败重试、阅读结果。当前 Task 行只显示名称/版本/schedule，无 prompt 查看编辑；校验失败丢输入；Run 展示原始 status 与空 reason；详情无已运行时长；Artifact 正文与模板各输出来源。

## 2. 名词解释

Task、Run、Artifact 以[名词表](../glossary.md)为准。本设计「Task 页」是 `/projects/{id}/tasks/{tid}`，编辑只影响后续 Run，不改历史 `task_snapshot`。

## 3. 目标与非目标

### 3.1 目标

S1 可查看并编辑适用字段，失败保留输入，版本可见。S2 独立 Run 显示标识、冻结版本、状态、开始时间与已运行时长。S3 失败原因可理解，手动重试新 Run。S4 来源只一处，证据可打开。

### 3.2 非目标

不增加调度、模式转换、取消/恢复、流式控制台。证据有效性归 #304。概览区块归 #305。

## 4. 能力

### 4.1 UI/UX

| 位置 | 内容 | 判定 |
|---|---|---|
| Task 页 | agent：名称与 prompt；旧 Task：名称、数据源、调度。保存靠近字段 | 有效修改 version+1；空 prompt 字段旁错误且输入仍在 |
| Run 列表/详情 | 人话状态；running 显示开始时间与已运行时长；failed 映射内部码 | 不出现单词 None 代替原因；刷新不丢记录 |
| 失败 | 原因 + 手动「运行」创建新 Run | 旧 Run 保留，不自动重试 |
| Artifact | 正文 + 唯一来源列表（版本、读取时间、链接） | 无输入时「本次未读取项目材料」 |

不做聊天、取消/恢复、模式转换界面。

## 5. 思路与折衷

独立 Task 页而不是行内展开。宿主不再把「来源」追加进 Artifact 正文，改由页面单一区域列出已记录输入。放弃覆盖旧 Run 当重试。内部码映射为中英 i18n，默认回退原码而不是空白。

## 6. 架构

HTML 适配调用既有 `edit_task` / `run_task`；时长由 `started_at` 与 now/`finished_at` 计算。失败路径：校验错误回原表单；Run 失败停在详情。主路径：打开 Task → 保存版本 → 运行 → 详情 → Artifact 或手动重试。

## 7. 模块

新模板 `task.html`；`views` 增加 GET/POST Task；`run_status_label` / `run_reason_label` / `run_elapsed_label`；`_execute_agent_run` 不再向正文追加来源节。

## 8. API/CLI

无新公共契约。Web `GET/POST /projects/{id}/tasks/{tid}`。既有 PATCH API 不变。

## 9. 边界

编辑不改历史 snapshot。旧 Task 保持 source_snapshot。不虚构进度百分比。

## 10. 迁移/兼容/回滚

历史 Artifact 若已含「## 来源」正文则仍渲染，页面不再叠加第二份列表时：若正文已有来源标题则模板仍列出可点击证据（允许历史双份）。**新 Run** 正文不再追加来源节，只走模板。回滚还原模板与追加逻辑。

## 11. 测试计划

| 层级 | 判定 |
|---|---|
| E2E/S1 | 编辑 agent/旧 Task：成功版本+1；空 prompt 保留名称 |
| E2E/S2 | 活 PID 的 running Run 显示时长与状态，无 None |
| E2E/S3 | failed 显示映射文案；重试 POST 产生新 run id |
| E2E/S4 | 新成功 Artifact HTML 中来源标题只出现一处；输入链接可打开 |
| Unit | 状态/原因映射；时长格式 |

## 12. 开放问题

无。

## 13. 关联

[#306](https://github.com/xforce-io/kairo/issues/306)；#305 提供入口；#304 证据门禁；#307 调度边界。
