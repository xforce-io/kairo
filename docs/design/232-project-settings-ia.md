# #232 Project + Settings 信息架构重思

- Issue: [#232](https://github.com/xforce-io/kairo/issues/232)
- L1: [Approved](https://github.com/xforce-io/kairo/issues/232#issuecomment-5522059651)
- 分支: `feat/232-project-settings-ia`
- 状态: Approved
- 日期: 2026-09-03

本文件取代用户可见的「手填 slug / 手选 spreadsheet」taxonomy。凭据不进 Project、不复制 Workspace、#234 后续，仍有效。

## 1. 背景

Console 已能跑 S1，但 Project 页把 CLI 摊成全宽表单。关联靠 `slug` 文本框；数据源要人选 spreadsheet；Settings 用点路径表单。用户要的是：选已有工作区、贴链接即接资料、连接按 Reader 管理。

## 2. 名词解释

Project、Settings、连接、Data Source、Reader、Task、Run、Artifact 见 [名词表](../glossary.md)。

本设计中 **Reader/平台** 是腾讯文档、企微、Notion 这类连接类别；**不是** spreadsheet/smartsheet。后者仅可为腾讯文档 Reader 的内部形态。

## 3. 目标与非目标

### 目标

- 关联 Workspace：在已有工作区中多选（可过滤），标签可移除，解除不删内容。
- 数据源：粘贴 URL（可选用途）；docs.qq.com 的 `/sheet` 与 `/smartsheet` 推断腾讯文档，无用户可见 kind 下拉。
- Settings：按 Reader 列连接；腾讯文档可授权；企微/Notion 占位。
- 三入口仍完成 S1；Project 存数无凭据。

### 非目标

- #234；企微/Notion 真读/OAuth；凭据进 Project；public-read Project/Settings；重做 Workspaces/Timeline/Knowledge 整页。

## 4. 能力

### 4.1 UI/UX

Project 页是对象画布，不是四张后台表。上：名称。中：工作区选择器（复选已有 topic，不是 slug 文本框）+ 已关联标签；数据源一行贴链接。下：Task/Run。按钮 `width: auto`，禁止全宽绿条当主操作。

Settings 主区是连接卡片（腾讯文档 / 企微 / Notion），健康与授权；General 等分区次之。无某一 Project 的 Task/Artifact。

| 状态 | Project | Settings |
|---|---|---|
| 空 | 无工作区可选 →「先去工作区创建」；无数据源 →「贴链接」 | 腾讯文档未授权可判定 |
| 成 | 一次勾选两个工作区保存；贴 sheet URL 显示腾讯文档 | 授权后 health=authorized |
| 错 | 非 docs.qq.com → 可区分不支持/无效；未授权读取 → permission | 无 Task 行 |
| 不做 | slug 主键入；spreadsheet 下拉 | 点路径当唯一 IA |

## 5. 思路与折衷

连接在 Settings，Project 只存 connection_id + URL + 用途。平台由 URL 推断。放弃 slug 主键入与 spreadsheet 分类。企微/Notion 只占位，避免假成功读取。

## 6. 架构

`infer_source(url)` 纯函数：docs.qq.com sheet/smartsheet → 腾讯文档；notion.so → Notion（unsupported 添加）；企微域名 → 企微（unsupported 添加）；其它 → invalid_link。`set_workspaces` 用已扫描 slug 集合替换关联。Console/CLI/API 调同一函数。

主路径：Settings 授权 → 勾选工作区 → 贴 URL → Task → Artifact。失败路径不静默当 spreadsheet。

## 7. 模块

`kairo.readers.infer_source`；`kairo.projects.set_workspaces` / `add_datasource`（kind 可选，默认推断）；`kairo.settings` 默认三槽连接；web templates + 窄按钮 CSS。

## 8. API/CLI

- `POST /projects/{id}/workspaces`：`workspaces` 多值，替换关联集。
- `POST /projects/{id}/datasources`：`url` + `purpose`，无 kind。
- `GET /api/settings`：connections 含 label / live / health。
- CLI：`project link ID slug [slug…]`；`datasource add ID --url`（`--kind` 可省略）。

内部 DataSource.kind 可仍为 spreadsheet/smartsheet，**不出现在表单**。

## 9. 边界

选择器只含已有 workspace。企微/Notion 连接 live=false，添加其 URL 失败且可判定。凭据仍只本机。

## 10. 迁移

已有 Project 的 kind 字段保留内部使用。无用户迁移步骤。回滚：还原模板与 infer。

## 11. 测试计划

见 pytest：一次 POST 两个工作区；无 `input[name=slug][type=text]`；sheet/smartsheet URL 无 kind 字段；非 docs.qq.com 拒绝；Settings 有 Reader 连接无 Task 行；constitution.yaml 解除后仍在。

## 12. 开放问题

无。

## 13. 关联

#232 新 L1；#233 #237 #235 Console；#234 后续。
