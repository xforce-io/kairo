# Ref 的 HTML 形态预览

版本 v1 · [Issue #457](https://github.com/xforce-io/kairo/issues/457) · [L1 v1](https://github.com/xforce-io/kairo/issues/457#issuecomment-7035610742)

## 1 背景

在生产版本 `11e44fa`，同一全局 Ref 的图片预览请求返回 200，HTML 预览请求返回 404。列表由 `_ref_forms` 判断预览资格，Topic 阅读区和独立 Ref 抽屉共用 `_form_preview_response`。这两处只接受文本、图片和音频。

## 2 名词解释

N/A。沿用[名词表](../glossary.md)中的 Topic 与 Ref。

## 3 目标与非目标

用既有形态入口预览自包含交互 HTML。保持 #265 对两处预览一致的约定。范围为 Console 内已复制登记的文件，公开只读保持原有读取边界。范围之外包括外部依赖、多文件应用、notes 和加工。

## 4 能力

### 4.1 UI/UX

Topic 的现有形态行在中间阅读区打开 HTML。独立 Ref 页的现有形态按钮在抽屉内打开同一内容。框架可滚动，保留文档内部交互。材料不存在时显示现有不可预览状态。

## 5 思路与折衷

复用形态文件路由及其路径范围。选择独立文档嵌入，放弃把 HTML 片段合并进 Console DOM，以隔离附件样式、脚本和权限。

## 6 架构

预览资格由现有 Console 判断与形态文件读取范围共同决定。外层返回带 `sandbox="allow-scripts"` 的 iframe，仅允许在隔离的文档中运行脚本。内层文件响应独立施加 CSP sandbox、同源嵌入限制和资源限制。允许内联脚本和样式以及 data 图片、字体。表单、子框架、窗口和顶层导航保持受限。文件不可达时返回现有错误。公开只读中的形态行、直接预览和原始文件路由均不暴露这一 HTML 路径。

## 7 模块

Web Console 形态列表、预览片段与文件响应共享 HTML 资格与读取边界。

## 8 API/CLI

复用 Topic 和独立 Ref 的 `/form/{key}`、`/file/{key}`。公开只读请求 HTML 预览和对应文件返回 404。CLI 无改动。

## 9 边界

文件必须为已登记、位于其 home 内的 .html 或 .htm。跨 home Ref 走既有成员权限。仅用稳定 Ref 身份与形态位置解析文件。失效路径或缺失文件沿用 404。

## 10 迁移/兼容/回滚

无存储迁移。已有复制登记的 HTML 可用。回滚代码后恢复旧预览规则。

## 11 测试计划

| 验收 | 取证 |
|---|---|
| S1.A1 | 浏览器从 Topic 打开已有交互 HTML，点击节点，切回 Markdown。 |
| S2.A1 | 浏览器从 Timeline 打开独立 Ref 页，在抽屉操作并关闭。 |
| S3.A1 | 浏览器核对图片、文本与框架属性，Integration 核对文件响应的隔离策略。 |
| S3.A2 | 浏览器核对公开只读形态列表，Integration 核对公开请求与越界路径。 |

真实路径见 [ref-html-preview](../../.agents/skills/verify-kairo-web/features/ref-html-preview.md)。

## 12 开放问题

N/A。外部依赖与多文件形态不在本次范围。

## 13 关联

[Issue #457](https://github.com/xforce-io/kairo/issues/457) · [L1 v1](https://github.com/xforce-io/kairo/issues/457#issuecomment-7035610742) · [#265](https://github.com/xforce-io/kairo/issues/265)
