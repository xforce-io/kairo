# #411 notes 样式与确认删除

状态：Approved（2026-09-22，会话中用户以“go”批准 L1）；关联 [Issue #411](https://github.com/xforce-io/kairo/issues/411)。本文件是本次续改的 L2 事实源；原 411-console-ref-notes.md 中禁止删除的边界由本文件替代，其余行为沿用。

## 1 背景

#415 已合并，#416 已更新缓存键。阅读画布仍继承正文标题和列表样式；用户要求修正，并在单条 note 详情增加确认删除。

## 2 名词解释

沿用 [名词表](../glossary.md)。note 是 Ref 上用户记录的一条判断。

## 3 目标与非目标

统一两处 notes 观感；可写会话可确认删除一条 note。不做编辑、批量删除、回收站、新登录身份或 CLI 删除命令。

## 4 能力

### 4.1 UI/UX

S1–S7 沿用 #411；S2 调整为正文不可编辑、可写模式允许删除。S6 标题/字体统一，列表与输入框对齐，输入框占满内容列，按钮内容宽右对齐；窄屏区头可换行。

S8：用户打开单条 note → 元数据区右侧“删除” → 模态确认“确定删除这条 note？删除后不可恢复。”；取消默认聚焦，取消/Escape 回原页不改数据。确认提交时不可连点；成功返回所属 Ref notes 区、提示已删除、计数减一；最后一条删除后为空态。失败返回详情、提示错误且能重试。public-read 不展示控件，服务端拒绝写入。无新增独立页面。

## 5 思路与折衷

复用现有 Ref 存储和原子文件写入，直接移除指定记录，放弃回收站以保持范围小且确认后结果明确。删除后旧稳定键不得指向后来新增的记录：新 note id 在时间前缀后带随机唯一后缀；旧键继续可读。用户界面显示结果，不显示存储细节。

## 6 架构

模板承担呈现与确认；Web 路由校验确认和可写模式、组织成功/失败落点；notes 数据层按稳定键定位记录，并在追加共用锁内原子移除。正常路径：确认 → 校验 → 锁内写入 → 303 返回 Ref。失败路径：未确认 400；不存在 404；写入失败保留旧数据并返回可重试详情；public-read 沿用统一拒绝策略。

## 7 模块

notes.py 维护记录删除及不复用身份；views.py 承接 HTTP；global_note.html 承接确认；共用 notes 模板与 CSS 统一展示。

## 8 API/CLI

新增 POST /refs/{ref_id}/notes/{note_id}/delete，表单 home 精确定位 Ref、confirmed=yes 表达确认。成功 303 到 Ref 的 #notes；失败见 §6。无 CLI 命令/flag 变更，稳定键语法不变且视为不透明标识。

## 9 边界

只删指定 Ref 的指定 note，不删 Ref、digest、fold 或其它 note。不访问现网数据做删除验收。与追加并发不得丢失新记录；重复删除返回不存在。

## 10 迁移/兼容/回滚

notes.jsonl 格式不变，无迁移。旧键继续可读，新键满足原解析器字符约束。回退代码不能恢复已删除内容。

部署沿用 [#354 §10](354-topic-member-preview.md)：合并后更新服务加载的代码、launchd 重启、健康验证；失败恢复记录的代码版本并重启。实际 launchd 配置定位到 /Users/xupeng/dev/github/kairo-prod，数据根 /Users/xupeng/kairo，端口 8787；发布前记录旧 SHA，不回滚资料。

## 11 测试计划

E2E：更新 `.agents/skills/verify-kairo-web/features/console-ref-notes.md`，覆盖 S1–S8。浏览器在 scratch root 验证详情、Topic 画布、空/有数据、确认/取消、末条删除与 public-read；读取计算样式及元素边界证明 S6。
Integration：确认参数缺失、成功/重复删除、写入失败重试、public-read 拒绝；并发追加/删除后新条保留。
Unit：同一时间新增不复用已删除稳定键；现有 notes 契约测试回归。

## 12 开放问题

N/A。设计已获批准；部署配置已核实，使用现有回滚约定。

## 13 关联

[Issue #411](https://github.com/xforce-io/kairo/issues/411)、[PR #415](https://github.com/xforce-io/kairo/pull/415)、[PR #416](https://github.com/xforce-io/kairo/pull/416)。后续 PR 链接由交付记录补充。
