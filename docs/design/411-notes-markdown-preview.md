# #411 notes Markdown 多行预览

状态：Approved（2026-09-22 用户在会话回复“go”批准）。本次预览的唯一 L2 事实源；删除等其它行为沿用 [411-notes-delete-confirm.md](411-notes-delete-confirm.md)。

## 1 背景

现有卡片仅展示首行 80 字纯文本，无法阅读多段判断，Markdown 标记直接暴露。用户要求更多正文、更多按钮及 Markdown 渲染。

## 2 名词解释

沿用 [名词表](../glossary.md) 的 Ref、note、Topic，无新增领域术语。

## 3 目标与非目标

在卡片内读多行 Markdown，长内容按需展开。无编辑器、分页、新存数、API 或 CLI 契约变更；不扩展 Markdown 语法。

## 4 能力

### 4.1 UI/UX

S6 扩充：Ref 详情与 Topic 阅读画布共用卡片；标题、段落、加粗、列表、引用、代码和链接按现有 Markdown 渲染。默认预览约六行正文等高区域；仅实际溢出时显示“更多”。点击在当前卡片展开全文并变为“收起”，再次点击恢复预览。短内容不出现切换按钮。正文中的链接独立可点击，不再整张卡片跳转。卡片底部另有“查看详情”。键盘能操作切换，展开状态可读；焦点进入正文链接时展开，避免隐藏焦点。

无 JavaScript 时完整正文可读并保留详情链接。读取失败/无 note 沿用现有错误/空态。追加后新卡片自动获得同样行为，窄屏及内容尺寸变化后重新判断溢出。public-read 同样能展开，只读限制不变。

## 5 思路与折衷

完整 Markdown 先渲染，再按显示高度折叠，避免按字符截断破坏 Markdown 结构。放弃固定字符摘要与强制跳转详情；代价是列表传输完整正文，当前单 Ref 全楼范围可接受。只在实际展示卡片时生成 HTML，侧栏计数不重复渲染。

## 6 架构

Web 展示数据调用现有 render_markdown（原始 HTML 关闭）→ 共用卡片模板 → CSS 限高与浏览器溢出检测。HTMX 插入新画布后初始化，ResizeObserver 更新尺寸状态；已移除的画布释放观察对象。无脚本回退完整正文。

## 7 模块

views.py 仅为卡片路径生成正文 HTML；_notes_panel.html 分离正文、切换与详情链接；notes_preview.js 管理展示状态；app.css 隔离正文样式以避免 .doc 继承污染。

## 8 API/CLI

N/A：接口、CLI 和稳定键不变。

## 9 边界

不修改 note 内容；原始 HTML 不执行，危险链接沿用现有渲染器约束；保留现有 public-read 写保护和确认删除。

## 10 迁移/兼容/回滚

无数据迁移。刷新静态资源版本。按 scripts/serve deploy 发布到现有 kairo-prod；失败按记录的部署前 SHA 回退代码、重启并复验，不回滚用户资料。

## 11 测试计划

E2E：`.agents/skills/verify-kairo-web/features/console-ref-notes.md` 对应 S1–S8，重点 S6 的长短正文、两处入口、更多/收起、Markdown 链接与窄屏；S4/S5 追加后初始化；S2/S8 详情及确认删除回归；public-read 无写控件但可展开。
Integration：详情与画布 HTML 包含完整渲染正文、独立详情链接，原始脚本被转义，列表和删除原测试通过。
Unit：沿用现有 Markdown 渲染器测试，不另造渲染器。

## 12 开放问题

N/A：用户已批准上述交互。

## 13 关联

[Issue #411](https://github.com/xforce-io/kairo/issues/411)、[PR #417](https://github.com/xforce-io/kairo/pull/417)。后续 PR 在交付记录关联。
