# #342 知识处理队列入口

状态：Approved；本会话「go」批准 L1。分支 `feat/342-knowledge-queue-entry`。

## 1 背景

[#342](https://github.com/xforce-io/kairo/issues/342) 要求 S1 分类计数可解释、S2 候选审核首屏可达。

## 2 名词解释

N/A，沿用[名词表](../glossary.md)。

## 3 目标与非目标

目标：直接进入相关队列，分类数量与显示集合一致；处理后反馈保留 Topic。非目标：批量审核、修改候选算法、自动校正、窄屏优化。

## 4 能力

### 4.1 UI/UX

顶部为紧凑 Topic 选择与分类入口。指定 Topic 默认首先展示候选审核，待校正内容、提取失败、已确认知识与公共知识可分别进入；非当前内容默认折叠于其后。候选主路径在 1440×1000 首屏含首条候选与审核动作；长摘录与其它来源可展开，证据入口和短摘录默认可见。空队列提示类别为空并保留切换入口。错误就近显示，成功更新计数并保留当前上下文。

## 5 思路与折衷

重组既有内容，不建立第二份审核存数。采用明确的类别计数，数值表示处理记录而不是唯一材料。同一材料跨类别可重复计数。放弃把公共知识长列表排在所有 Topic 工作之前。已有审核历史和全局审核继续可达。

## 6 架构

现有知识读取/审核层 → 当前 Topic 和队列视图 → 页面主段与折叠辅段。主路径为 GET 指定 Topic/queue、处理候选、重新读取同一范围；失败沿原表单上下文呈现。

## 7 模块

views 解析类别并组合计数，knowledge 模板重组原有各段，Topic 入口指向候选处理。知识存储和审核操作不改。

## 8 API/CLI

GET `/knowledge?workspace={slug}&queue=candidates|drift|errors|local|global`；缺省 candidates（未选 Topic 时 global）。非法 queue 回退默认。既有 filter 保留。POST 表单 action 带当前 queue/filter 查询，处理反馈保留所选 Topic。CLI N/A。

## 9 边界

分类数与当前展示集合一致；总计如展示仅代表该视图中的分类处理项之和，不暗示唯一 Ref 数。只读页面不写入审核结果。知识校正继续按条手工；不增加一键校正全部。公共知识和全局审核不因选择 Topic 消失。

## 10 迁移/兼容/回滚

无数据迁移。旧 workspace/filter 查询有效；回滚仅还原展示。

## 11 测试计划

E2E/S1：三类同时存在，切换类别后主段与计数一致。E2E/S2：桌面首条候选及审核动作可见，采纳/忽略后计数变化且上下文保留。Integration：旧审核/漂移用例回归、错误与无匹配集合。Unit：非法参数回退。

## 12 开放问题

N/A。

## 13 关联

- [Issue](https://github.com/xforce-io/kairo/issues/342)
- [L1](https://github.com/xforce-io/kairo/issues/342#issuecomment-5564010117)
- #198、#215；PR 在交付时回链。
