# #343 Artifact 引用定位

状态：Approved；本会话「go」批准 L1。分支 `feat/343-artifact-input-location`。

## 1 背景

[#343](https://github.com/xforce-io/kairo/issues/343)：S1 一次点击定位历史输入，S2 旧引用与非法位置明确回退。

## 2 名词解释

沿用[名词表](../glossary.md)；行号指归档原始文本按换行分隔的、从 1 开始的位置，不是渲染后段落或电子表格原始行号。

## 3 目标与非目标

目标：Task 可获得可引用位置，读者可验证同次 Run 的输入片段。非目标：证明引用语义正确、自动猜测旧引用、修改缓存或输入版本。

## 4 能力

### 4.1 UI/UX

Artifact 引用进入历史输入页，顶部显示选中行及前后各两行上下文，目标行高亮并自动定位。完整原文继续可读，表格保留既有预览。返回链接回同一 Artifact。无位置提示整份输入；非法或越界位置提示定位失败并展示整份输入，不猜测片段。

## 5 思路与折衷

采用归档原文行范围，文本、Markdown 与 CSV 共用规则。放弃依赖易变化的渲染 DOM 和当前在线源。CLI 保留 content，新增 numbered_content 与 line_count；提示词说明引用语法。重复文本依靠显式行号消歧。

## 6 架构

材料读取层归档原文 → CLI 给 Task 带行号副本 → 产物校验引用所属 Run 和范围 → Web 重写同 Run 历史地址 → 输入页按原文显示选中范围。未知 input_id 拒绝 Run 成功；新增非法引用范围拒绝成功；外部构造或旧有非法 URL 则可解释回退。

## 7 模块

共享 input_citations 解析行范围；project_materials 提供只读位置副本；CLI 输出、Task 提示与结果校验贯通；Web 展示与链接转换。

## 8 API/CLI

`project read --run` 增加 `numbered_content`（`行号: 原始行`）和 `line_count`，content/version 不变。引用 `[标题](input:INPUT_ID#L3-L5)`，单行 `#L3`；旧 `input:INPUT_ID` 有效。Web 使用 `?lines=L3-L5#input-location`；无效参数回退整份。

## 9 边界

input_id 必须属于该 Run。1 ≤ 起始 ≤ 结束 ≤ 原文行数；空文件不可指定范围。不使用当前数据源推断历史内容。所有原文经模板转义；位置只证明落点，不代表事实支撑成立。

## 10 迁移/兼容/回滚

不迁移存数，不改归档内容或 hash。旧版本不支持新引用范围，回滚后需重新升级才能定位新引用；原始归档仍完整可读。

## 11 测试计划

E2E/S1：确定性 Task 通过真实 CLI 读取位置并生成引用，浏览器点击文本和表格引用、确认高亮原文与返回路径。E2E/S2：旧链接、非法/越界范围回退，未知输入 404。Integration：新引用校验与现有材料生命周期回归。Unit：行号、边界、转义和 hash 不变。

## 12 开放问题

N/A。

## 13 关联

- [Issue 与 L1](https://github.com/xforce-io/kairo/issues/343#issuecomment-5564010437)
- PR 在交付时回链。
