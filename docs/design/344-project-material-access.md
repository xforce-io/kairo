# #344 Project 材料入口

状态：Approved；本会话「go」批准 L1。分支 `feat/344-project-material-access`。

## 1 背景

[#344](https://github.com/xforce-io/kairo/issues/344)：成员 Ref 增长不应推远数据源入口，查找与返回需保留上下文。

## 2 名词解释

N/A，沿用[名词表](../glossary.md)。

## 3 目标与非目标

目标：数据源优先可达，完整 Ref 可搜索且返回不丢查询。非目标：材料存储、成员规则、Task 行为或窄屏优化。

## 4 能力

### 4.1 UI/UX

材料区依次为概要、关联 Topic、数据源、折叠成员 Ref。成员摘要显示总数；展开后保留现有搜索与排序。搜索无匹配明确提示；链接携带返回地址，返回自动展开并恢复查询与排序。空成员与空数据源保留原说明及新增入口。

## 5 思路与折衷

重用现有搜索，URL 保存材料查询、排序与展开状态。放弃截断成员列表和新增分页存数，默认布局高度不随 Ref 数增长。

## 6 架构

Project 读取现有成员集合 → 模板重排并折叠 → 浏览器按 URL 初始化查询 → Ref 返回链接经过本地 Project 路径校验回原上下文。失败沿用既有错误区；无匹配不视作读失败。

## 7 模块

Project 模板与已有 Ref 返回地址验证。数据模型 N/A。

## 8 API/CLI

Project GET 增加展示用 `materials=1&q=...&sort=title|time`。Ref `back` 允许带查询的合法本地 Project 路径，不允许外站。

## 9 边界

完整列表仍可访问，数据源前置独立于成员数。公开阅读权限不变。

## 10 迁移/兼容/回滚

无存数变更；旧 Project 和 Ref 链接有效；回滚展示即可。

## 11 测试计划

E2E/S1：30 与 100 成员时数据源入口纵坐标不变。E2E/S2：搜索、打开 Ref、返回恢复查询，全部成员仍可访问，无匹配提示。Integration：返回地址与公读边界。

## 12 开放问题

N/A。

## 13 关联

- [Issue 与 L1](https://github.com/xforce-io/kairo/issues/344#issuecomment-5564010773)
- PR 在交付时回链。
