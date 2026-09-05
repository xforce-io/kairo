# #307 Task 仅手动触发

- Issue：[#307](https://github.com/xforce-io/kairo/issues/307)
- L1：[提案与批准依据](https://github.com/xforce-io/kairo/issues/307#issuecomment-5552243433)；用户于 2026-09-05 回复「Approved」。
- 分支：`feat/307-project-manual-trigger`
- 状态：Approved（2026-09-05）
- 日期：2026-09-05

本文件是详细设计唯一事实源。Issue 仅保留不超过 10 行的设计摘要与链接。

## 1. 背景

Web 可选手动外的 interval，但没有间隔输入，也没有消费这些字段的触发器。保存成功会被当成周期已启用。

## 2. 名词解释

Task、Run 见[名词表](../glossary.md)。`once` 表示可手动触发的既有模式，不表示只能运行一次。本设计不把 interval 当已交付能力。

## 3. 目标与非目标

入口与真实能力一致：Web 不提供 interval；CLI/API 新建或切换为 interval 明确失败；历史 interval 标明未自动调度，仍可手动运行。非目标：本轮构建调度、追补、并发协调。

## 4. 能力

### 4.1 UI/UX

创建表单只保留手动。历史 interval Task 显示未启用自动调度，保留手动运行。不出现下次触发时间或调度服务页。

## 5. 思路与折衷

收敛为手动。放弃只藏 Web 而 API 继续接受。不把用户请求的 interval 静默改成 once。不做调度进程。

## 6. 架构

`create_task` / `edit_task` 在写入前拒绝新 interval（`unsupported_schedule`）。已是 interval 的 Task 改名称等字段不改 schedule。主路径：创建手动 Task → 手动 Run。失败：周期请求非成功。

## 7. 模块

`kairo.projects` 校验；Web 去掉 interval 选项；i18n 提示；HTTP 400。

## 8. API/CLI

`POST /api/projects/{id}/tasks` 与 `kairo task create --schedule interval` 返回失败，code `unsupported_schedule`。`PATCH` / `task edit --schedule interval` 对当前非 interval 的 Task 同样失败。合法 `once` 成功。

## 9. 边界

不清理历史 schedule/interval_hours 与 Run。不静默改写。once 仍可多次手动跑。

## 10. 迁移/兼容/回滚

旧 interval 记录可读。旧客户端提交 interval 从成功变为 400；这是有意的能力收缩。回滚即恢复接受 interval 的代码，数据无需迁移。

## 11. 测试计划

| 层级 | 判定 |
|---|---|
| E2E/S1 | Web 创建表单无 interval 选项；保存后可手动跑 |
| Integration/S2 | CLI/API 新建与切换 interval 非成功；once 成功 |
| E2E/S3 | 历史 interval 样例显示未调度提示，仍可手动运行 |

## 12. 开放问题

若改为真实调度，须先改本 Issue 验收，不能将本稿当授权。

## 13. 关联

[#307](https://github.com/xforce-io/kairo/issues/307)；#306 提供手动运行页。
