# Ref 进展与 Timeline 归日

版本 v1 · [Issue #455](https://github.com/xforce-io/kairo/issues/455) · [L1 v1 与验收](https://github.com/xforce-io/kairo/issues/455#issuecomment-7033612481)。产品基线来自会话中对「一条持续讨论，多条进展记录」方案的端到端交付请求。

## 1 背景

同一 Ref 可持续记录讨论。当前 `timeline.scan_timeline` 从 manifest 读取发生日期，`web.views.timeline_view` 据此计数和筛选。历史回顾与运行当前视图也使用该扫描入口。Ref 详情由 `web.views.global_ref_view` 渲染，可从 Timeline 和 Topic 中打开。

## 2 名词解释

新增「进展」「最近进展日期」，见[名词表](../glossary.md)。

## 3 目标与非目标

满足 L1 v1 的 S1–S3，保留 Ref 身份和发生日期。既有 Topic 分组、历史回顾窗口和综合材料契约照常工作。进展增量独立于 note 和加工产物。

## 4 能力

### 4.1 UI/UX

Ref 详情保留发生日期，在正文下方提供进展区。日期和正文为必填，日期默认 Console 当天，可补录。提交成功重新展示同一详情和进展，提交失败在同一表单显示反馈并保留输入。公开只读视图隐藏追加入口。Timeline 的日历、区间和最近列表使用最近进展日期，旧 Ref 使用现行发生日期。交互路径及空态见 L1 v1。

## 5 思路与折衷

进展使用每条 Ref 独立文件，避免与重命名、加工等 manifest 写入竞争。最近进展日期从进展推导，避免维护第二份索引。采用单条 Ref 归日，历史通过详情回看。文件修改时间可能受加工、同步与恢复影响，放弃用它推断进展。

## 6 架构

- Web 层解析 Ref home 和 id，沿用详情访问边界。写入成功后重定向详情。输入错误返回表单，存储读写失败展示错误。
- 存储层按 Ref 路径读写 `progress.json`。一个进展由公历日期与非空纯文本正文构成。文件缺失表示尚无进展。每条 Ref 使用文件锁串行追加，锁内读后以同目录原子替换写回。读取顺序为日期升序，同日按记录写入顺序。
- 查询层对用户 Timeline 和运行当前视图使用进展日期投影，历史回顾使用原发生日期。详情同时读取原发生日期与进展。Project 事件及 Artifact 保持现有规则。

## 7 模块

Ref 进展存储、Timeline 日期投影和 Web 详情分别承担持久化、归日和录入展示职责。展示和运行当前视图共用日期投影。

## 8 API/CLI

Console 表单使用 `POST /refs/{ref_id}/progress`，提交 `home`、`back`、`day`、`content`，成功跳回同一 Ref 详情。公开请求遵循 Console 写入限制。新增进展只通过 Console。CLI Timeline 与历史回顾沿用原发生日期规则。

## 9 边界

无进展时沿用现有日期解析和未知日期分组。补录以其指定日期排序。加工、重命名及修改其它内容均不写进展记录。损坏或暂不可读的进展文件可按原日期找到 Ref，详情显示读取错误与重试入口。正文以纯文本呈现。

## 10 迁移/兼容/回滚

旧 Ref 缺少进展文件时无需迁移。回退版本仍按原日期展示，已保存的进展文件留在 Ref 目录供恢复版本读取。

## 11 测试计划

| 验收 | 验证对象 |
|---|---|
| S1.A1、S2.A1、S3.A1 | E2E 从 Timeline 进入 Ref 详情，保存与补录，重开详情并切换日期。见 [Web 验证路径](../../.agents/skills/verify-kairo-web/features/ref-progress-timeline.md)。 |
| S2.A2 | E2E 重命名与回看日期，Integration 使用持久化路径更新正文并以确定性 provider 重生成 digest 后回看日期。 |
| S1.A1、S3.A1 | Integration 验证无效提交、公开只读与旧 Ref，Unit 覆盖追加及日期推导。 |

## 12 开放问题

N/A。可在后续 Issue 独立讨论进展修订和其他入口。

## 13 关联

[Issue #455](https://github.com/xforce-io/kairo/issues/455) · [L1 v1](https://github.com/xforce-io/kairo/issues/455#issuecomment-7033612481)
