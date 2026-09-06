# #316 概览材料可检索且最近结果留在首屏

- Issue：[#316](https://github.com/xforce-io/kairo/issues/316)
- L1：[提案与批准依据](https://github.com/xforce-io/kairo/issues/316#issuecomment-5555867961)；用户于 2026-09-06 在 Issue 回复「Approved」。
- 分支：`feat/316-project-overview-followup`
- 状态：Approved（2026-09-06）
- 日期：2026-09-06

本文件是详细设计唯一事实源。Issue 仅保留不超过 10 行的设计摘要与链接。

## 1. 背景

[#316](https://github.com/xforce-io/kairo/issues/316) 收尾 [#305](https://github.com/xforce-io/kairo/issues/305)。6f76a56 上 Task 区已前移，但材料搜索的 `hidden` 被 `.obj-row { display:flex }` 盖掉；有 Task 时新建表单仍把最近 Artifact 挤出 1280×720 首屏；无 Task 时首屏只有空表单；改名/移除与 Data Source 常驻编辑表单仍占主路径。Data Source 名称走 #313。

## 2. 名词解释

Project、Topic、Ref、Data Source、Task、Run、Artifact 以[名词表](../glossary.md)为准。本设计「最近结果」指概览上最近成功 Artifact 入口，不是备份「最近结果」。

## 3. 目标与非目标

### 3.1 目标

- S1：不存在的搜索词使不匹配主题资料实际不可见。
- S2：3 个 Task 且有成功 Artifact 时，新建表单默认折叠，最近成功 Artifact 在 Task 列表之后、创建表单之前。
- S3：无 Task 时首屏可见材料数量级与创建下一步。
- S4：改名/移除/解除关联弱于 Task 与运行；Data Source 主路径不再常驻完整编辑表单。

### 3.2 非目标

产品调度、#315 证据协议、#317 未保存运行、给三个源起名、窄屏 gating。

## 4. 能力

### 4.1 UI/UX

自上而下：标题（改名折叠）→ `#project-primary`：材料概况（无 Task 时）或 Task 列表 → `#project-recent` 最近成功 Artifact → 创建 Task 的 `<details>` 默认关闭 → 运行列表 → `#project-materials`。

| 状态 | 主路径看到什么 |
|---|---|
| 无 Task | 材料数量级 + 下一步创建；创建表单折叠 |
| 有 Task/成功 Artifact | Task 行与最近 Artifact 在创建表单之前；表单默认不展开 |
| 搜索无匹配 | 不匹配 `.material-item` 带 `hidden`，CSS 使 `[hidden]` 赢过 `.obj-row` |
| 管理 | 改名、DS 编辑/移除、解除关联在折叠或弱按钮里 |

不做聊天、不做单独材料导航页。

## 5. 思路与折衷

用 `.obj-row[hidden], .material-item[hidden] { display: none !important; }` 盖过 flex。最近结果独立提前；创建表单默认折叠。管理操作放进 `<details>`。放弃顺序铺开全部表单；放弃只加搜索框。

## 6. 架构

仅 Web 模板、CSS 与 i18n。主路径：打开概览 → 看最近结果或空态下一步 → 按需展开创建。失败：表单错误时该 `<details>` 打开并保留输入。

## 7. 模块

`project.html`、`app.css`、`i18n.py`。不改领域发布协议。

## 8. API/CLI

N/A。无新公共契约。

## 9. 边界

不改 live 业务数据测 UI。Data Source 辨识名称不在本 Issue 验收。解除关联仍可达，只是降权。

## 10. 迁移/兼容/回滚

无存数变更。回滚还原模板与 CSS。

## 11. 测试计划

| 层级 / 验收 | 路径与可判定结果 |
|---|---|
| E2E/S1 | TestClient 取 HTML 与 `/static/app.css`：不匹配项使用 `hidden`；CSS 中 `[hidden]` 对 `.obj-row` 为 `display: none !important` |
| E2E/S2 | 3 个 Task + 成功 Artifact：创建 `<details>` 无 `open`；Artifact 入口在创建表单之前 |
| E2E/S3 | 无 Task 页 primary 含材料数量级与创建下一步 |
| E2E/S4 | 改名/DS 完整编辑表单不在未折叠主路径；移除弱于运行按钮 |

## 12. 开放问题

无。

## 13. 关联

- 验收：[#316](https://github.com/xforce-io/kairo/issues/316)
- 前序：[305-project-overview.md](./305-project-overview.md)、#313、#315
