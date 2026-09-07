# #340 Topic 加工状态与影响

状态：Approved。L1 经本会话「go」批准；分支 `feat/340-topic-processing-status`。

## 1 背景

[#340](https://github.com/xforce-io/kairo/issues/340) 要求分别解释内容存在性、加工状态与恢复路径。

## 2 名词解释

N/A，沿用[名词表](../glossary.md)。

## 3 目标与非目标

目标为 S1–S2：已有内容不等于最新；影响可定位；适用恢复方式明确。非目标：改引擎分类、自动重试、provider 修复、窄屏适配。

## 4 能力

### 4.1 UI/UX

Topic 操作区显示尚待加工数量与失败影响对象，每个对象可打开，按现有计划说明可重试或需人工处理。正文元信息分别显示已有正文可读/尚未生成与本次加工状态；已有结论在存在 pending 或 blocked 时不宣称最新。Ref 失败提示说明旧派生产物可能仍可读，每条故障给可理解原因。原始原因、provider、stage、路径放进默认折叠的技术详情。知识校正保留可稍后提示与知识页入口。无失败时不出现失败列表；状态未知时明确未知。

## 5 思路与折衷

复用 workspace_run_plan 的 blocked_refs/blocked_targets 和 retryable 标志，避免再造恢复规则。放弃把可读和最新合成一个状态。当前计划无法证明历史输入覆盖，因此明确加工未完成，不反推某条材料必定未进入旧结论。

## 6 架构

领域层现有计划 → Web 状态与原因映射 → 模板。主路径为打开 Topic、读状态、进入影响对象、按现有门禁处理；失败路径沿用现有重试或人工修复入口，不暗中解锁。

## 7 模块

views 组合只读状态，i18n 提供中英文原因说明，workspace/ref/target 模板呈现。引擎只作为事实源，不修改状态机。

## 8 API/CLI

N/A，无新公开接口；GET 状态读取不得触发加工。

## 9 边界

跨 Topic Ref 用全局身份解析链接；不泄漏 private 内容到 public-read。manual-edit、预算及 digest 降级保护不得伪装成普通重试；手工恢复说明指出对应操作的后果。未知原因只提供检查详情路径，不承诺重试有效。

## 10 迁移/兼容/回滚

无存数变更。恢复旧代码仅还原呈现，不影响正文或故障记录。

## 11 测试计划

E2E/S1：旧正文与后续 provider 失败并存，正文可读且影响链接打开正确对象。E2E/S2：provider、manual-edit/保护类、知识校正三种场景的入口与说明分别可判定；详情默认收起、展开不加工。Integration：已有门禁及跨 home Ref 解析回归。Unit：未知状态、原因映射与正文不存在组合。

## 12 开放问题

N/A。

## 13 关联

- [Issue](https://github.com/xforce-io/kairo/issues/340)
- [L1](https://github.com/xforce-io/kairo/issues/340#issuecomment-5564009380)
- #283、#215、#157；PR 在交付时回链。
