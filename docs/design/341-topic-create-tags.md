# #341 新建 Topic 与 Tag

状态：Approved；L1 经本会话「go」批准。分支 `feat/341-topic-create-tags`。

## 1 背景

[#341](https://github.com/xforce-io/kairo/issues/341) S1–S2 要求新建流程不跳出页面，且失败不留下半成关系。

## 2 名词解释

N/A，沿用[名词表](../glossary.md)。

## 3 目标与非目标

目标：同名 Tag 创建或复用、包含规则有效、失败可修正。非目标：迁移历史 Ref、任意规则编辑、批量创建、改变 CLI 的既有显式 Tag 前置、窄屏适配。

## 4 能力

### 4.1 UI/UX

首页新建名称输入旁明确说明同名 Tag 存在则复用、缺失则创建，命中该 Tag 的资料进入 Topic。提交按钮表示确认该操作。字段失败在原表单就近提示，保留名称；成功进入新 Topic 的空材料状态。已有 Tag 不产生重复项。

## 5 思路与折衷

先在隐藏临时目录准备完整 Topic（含同名包含规则），然后协调目录发布与 catalog 写入。Tag catalog 的读改写使用每个 serve root 的可重入进程锁，避免并发创建和其它 Tag 写入覆盖。放弃先建 Topic 再让用户补 Tag。为中断恢复保留最小事务记录，读取 catalog 时在同一锁内恢复，恢复只清理本次暂存对象。

## 6 架构

Web 校验/确认 → 领域组合创建 → 暂存完整 Topic → 根锁内重验名称和 catalog → 记录恢复信息 → 发布目录及 Tag → 清除记录。失败路径为锁内恢复此前 catalog 和本次目录，返回字段/创建错误；若进程中断，下次 catalog 访问先完成恢复。已有对象不被删除。

## 7 模块

refs 负责 catalog 锁与组合创建/恢复，Workspace 仍负责目录内容，Web 负责用户确认与错误反馈。

## 8 API/CLI

POST `/workspaces` 新增 `confirm_tag=1` 表示同意创建缺失的同名 Tag；缺省保留旧客户端行为，已有 Tag 仍可新建，没有 Tag 则拒绝并提示确认。CLI 既有契约不变。成功仍返回 HX-Redirect，错误仍为明确的 4xx/5xx。

## 9 边界

目的目录不得存在，包括 symlink；名称不得穿越或为隐藏目录。目录发布只使用本次暂存内容。组合事务与现有 Tag、assignment、包含规则变更共用根锁，防止回滚覆盖其它写入。恢复记录只描述根内的本次目录与 catalog；不访问外部路径。文件系统故障导致无法恢复时中止并明确报错，不谎报成功。

## 10 迁移/兼容/回滚

新增锁和短期恢复记录位于 .kairo；成功后无恢复记录残留。运行旧版本前须确保没有未完成事务；已完成 Topic 与 catalog 保持原有格式，旧版可读。升级不迁移既有资料。

## 11 测试计划

E2E/S1：无 Tag 的隔离服务中从首页确认新建，进入包含规则有效的 Topic。E2E/S2：已有 Tag 复用；非法名称和重名后修正成功。Integration/S2：目录发布与 catalog 写入各阶段故障、遗留事务恢复、并发同名/不同名创建，验证无重复、半成关系或误删。Unit：确认参数与名称边界。

## 12 开放问题

N/A。

## 13 关联

- [Issue](https://github.com/xforce-io/kairo/issues/341)
- [L1](https://github.com/xforce-io/kairo/issues/341#issuecomment-5564009802)
- #252、#249；PR 在交付时回链。
