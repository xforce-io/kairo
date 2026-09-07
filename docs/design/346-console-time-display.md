# #346 Console 时间显示

状态：Approved；本会话「go」批准 L1。分支 `feat/346-console-time-display`。

## 1 背景

[#346](https://github.com/xforce-io/kairo/issues/346)：Project、Artifact 与 Timeline 显示和跨日分组应遵循同一时区。

## 2 名词解释

N/A，沿用[名词表](../glossary.md)。

## 3 目标与非目标

目标：时间戳在服务本机时区统一显示，跨日分组一致。非目标：修改存数、浏览器时区偏好、将纯日期补成时刻。

## 4 能力

### 4.1 UI/UX

有时区时间戳显示本机日期、时分与 UTC 偏移。纯日期原样显示。旧无时区时间显示原值的日期/时分，不猜 UTC。Project、Artifact、Timeline 的同一 Run 使用 started_at，缺失时用 created_at。归档输入读取时间遵循相同规则；原始值保留在 title 属性便于核对。

## 5 思路与折衷

使用统一格式函数及服务本机时区，放弃每浏览器独立换算，以保持服务端 Timeline 分组一致。旧无时区字段继续视为本地日历值，不迁移存数。

## 6 架构

原始 ISO 数据 → 共用时间格式函数 → 模板；Timeline 有时区事件先转本机时区再取分组日期。解析失败保持原值，避免错误猜测。

## 7 模块

共用 time_display 模块、Web 渲染上下文、Timeline Project/Artifact 事件派生。

## 8 API/CLI

N/A，无公共接口变更，仅展示与 Timeline 分组纠错。

## 9 边界

发生日期与时间戳不同：Ref 的显式发生日期不受时区换算影响。旧无时区字段不添加 UTC 标签。服务进程时区按系统配置确定。

## 10 迁移/兼容/回滚

无存数迁移；回滚代码恢复旧展示，原始时间戳不变。

## 11 测试计划

E2E/S1：同一 Run 三页显示相同时区、时刻。E2E/S2：UTC 夜间时间转换后落入正确本地日期；纯日期不造时刻。Integration：Timeline、Project、Artifact 回归。Unit：UTC、正负偏移、无时区、纯日期与非法输入。

## 12 开放问题

N/A。

## 13 关联

- [Issue 与 L1](https://github.com/xforce-io/kairo/issues/346#issuecomment-5564011535)
- PR 在交付时回链。
