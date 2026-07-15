# 60 — 参考项一键「生成可读文稿」(prose)

- Issue: [#60](https://github.com/xforce-io/kairo/issues/60)
- 分支: `feat/60-ref-generate-prose`
- 状态: 已实现(TDD),待合入
- 日期: 2026-07-15
- 关联: [#30](https://github.com/xforce-io/kairo/issues/30) normalize→prose、[#33](https://github.com/xforce-io/kairo/issues/33) digest 恒从 transcript、[#52](https://github.com/xforce-io/kairo/issues/52) 产物 re-step 按钮 UI 模式、[#58](https://github.com/xforce-io/kairo/issues/58) 高密度 digest（正交）

## 1. 背景与目标

### 1.1 现状

- `digest.md`：reference 的记忆纪要，进 compose。
- `prose.md`：可选**人读全文档案**（ASR 誊录规范化：补标点、分段、纠错、去口水）。
- 由 `NormalizeRule` 产出；受 `constitution.pipeline.normalize.enabled` 控制，**默认关**。
- **#33 契约**：prose 只给人读、**不进 digest 路径**；digest 恒从 raw `transcript`（信息上界）。
- Web Console 已能预览 `role.prose`（i18n：文稿 / Prose），但**没有触发入口**；用户只能手改 constitution 再 `step`，几乎不可发现。

### 1.2 目标

在 Web 上为**单条参考**提供一键「生成可读文稿」：

1. 默认仍不生成 prose（constitution 默认关不变）。
2. 用户对**某一条**已有机器 transcript 的 stream 参考，可按需生成 `prose.md`。
3. **不**把 prose 绑到全局 Step checkbox；**不**改磁盘上的 `normalize.enabled`。
4. 生成后可在形态表预览；不触发 digest/compose 重算。

### 1.3 非目标（v1）

- Step 按钮下的「本轮生成 prose」checkbox（方案 C）。
- UI 长期开关写入 `constitution.yaml`（方案 B，可后续）。
- 把 prose 并入 digest 输入。
- corpus / 人给原文（`origin=added`）规范化。
- v1 强制「重新生成文稿」与批量全库 prose。
- 为 prose 单独引入新的持久任务协议（复用现有 step 任务区即可）。

## 2. 方案选择

| 方案 | 形态 | 结论 |
|------|------|------|
| **A. 参考项动作** | 选中 ref → 右栏元信息按钮 | **采用** |
| B. workspace 设置 | UI 改 `normalize.enabled` | 与 constitution 一致，但不够「本条按需」；可后续 |
| C. Step checkbox | Step 下本轮开启 normalize | 拒绝：策略与 run 参数混淆；一次 step 可能扫全库更贵；要扩散 CLI/engine 标志 |

**关键语义**：按钮生成的 prose **会落盘留下**，不是临时输出。文案是「生成可读文稿」，不是「临时启用 normalize」。

## 3. 交互设计

### 3.1 入口位置

右侧 **元信息面板 `#meta`**（`_ref_meta.html`），与「添加素材」并列；**不是**全局 Step 下方。

```text
┌──────────────────────────────────────────────────────────────────────────┐
│  workspace                                                   [返回总览]  │
├──────────────┬─────────────────────────────┬─────────────────────────────┤
│ 左栏 NAV     │ 中栏 READER                 │ 右栏 PANEL                  │
│ 产物         │  预览                       │  [▶ Step]                   │
│ 参考(观测)   │                             │  元信息                     │
│  · 会议A  ───┼────────────────────────────▶│  · 改名                     │
│  · 会议B     │                             │  · [+ 添加素材]  (已有)     │
│ 基线         │                             │  · [生成可读文稿] ★ 新入口  │
│              │                             │  · 形态表                   │
└──────────────┴─────────────────────────────┴─────────────────────────────┘
```

选中「有 transcript、无 prose」的音频参考时：

```text
参考 · 观测
[标题可改名]
[+ 添加素材]
[生成可读文稿]     ← 次要 ghost 按钮，风格对齐产物「重新生成」(btn-regen)

形态
  音频     📋
  誊录     📋 预览
  纪要     📋 预览
```

生成成功后形态表多一行「文稿」，可预览；**v1 按钮隐藏**（已有 prose 即不显示）。「重新生成文稿」非 v1 必做。

### 3.2 显示条件（v1）

**显示当且仅当**全部满足：

| # | 条件 | 依据 |
|---|------|------|
| 1 | ref 存在 | manifest 可读 |
| 2 | stream（fold=True） | `source_classes[class].fold`；corpus 跳过 |
| 3 | 有机器 transcript | `forms` 中存在 `role=transcript` 且 `origin != "added"` |
| 4 | 尚无 prose | 无 `role=prose` form，且无 `references/<id>/prose.md`（双检防漂移） |

**不显示**：纯文档 ref（仅 source_text）、corpus、无 transcript、已有 prose、人给的 transcript-only 文本源。

### 3.3 点击后行为

1. 若该 workspace 已有 step/prose 任务在跑 → 返回 busy（与 step 串行锁共用）。
2. 启动后台任务：仅对该 ref 跑 normalize，产出 `references/<id>/prose.md` + manifest form。
3. 进度可进现有 `#step-area`（SSE 日志），避免再造一套任务 UI。
4. 成功：刷新 `#meta`（形态表出现文稿）；可选 OOB 刷新 reader 若当前在预览该 ref。
5. 失败：日志/区域可见错误；不写假 prose；不改 constitution。
6. **不**跑 DigestRule / ComposeRule；既有 digest 的 `products` 记录保持可收敛。

## 4. 核心设计决策

| 维度 | 决策 |
|------|------|
| 触发粒度 | **单 ref**，非全 workspace |
| 是否改 constitution | **否**。一次性意图不写回 `normalize.enabled` |
| 与全局 normalize 关系 | constitution `enabled=true` 时，普通 `step` 仍会为所有合格 ref 产 prose；本按钮是 enabled=false 时的按需旁路 |
| 引擎入口 | core 新增可复用函数（见 §5），CLI 可选薄封装；Web 与 CLI 共用 |
| 执行方式 | 与 step 一致：子进程 + TaskRegistry 串行；或同进程调 core（若子进程无合适 CLI）。**优先** core 函数 + 现有 registry 能承载的调用方式，避免为 prose 复制任务栈 |
| provider | `select_provider()`，与 step 相同 |
| #33 契约 | 不变：prose 不进 `body_roles` / digest 输入 |

## 5. 模块与 API

### 5.1 Core（必要抽函数）

在 `engine.py`（或 `rules.py` 旁的薄封装）新增：

```text
generate_prose(ws, provider, ref_id: str) -> str
  """为单条 ref 生成 prose.md。

  前置不满足 → 抛明确错误(或返回结构化错误码):
    unknown-ref / not-stream / no-machine-transcript / prose-exists
  成功 → 返回 prose 相对路径(references/<id>/prose.md)
  副作用:写 prose.md、append form、更新 state.products 中该 key
  不跑 digest/compose,不改 constitution。
  """
```

实现策略（选最薄的一种，实现时二选一，推荐 R1）：

- **R1（推荐）**：抽出 `NormalizeRule` 对单 ref 的 discover/run 路径；`generate_prose` 构造 rule，只处理 `ref_id` 对应 work item。可临时在内存视 `enabled=True`，**不写盘**。
- **R2**：短时 monkeypatch `ws.constitution.pipeline.normalize.enabled` 仅在调用栈内——易踩并发/缓存，不推荐。

`NormalizeRule` 现有行为保持：默认 discover 在 `enabled=False` 时返回 `[]`；`generate_prose` 是显式旁路，不改变 `step()` 语义。

### 5.2 CLI（可选，建议做，成本低）

```text
kairo prose <ref_id>
```

- 打开 cwd workspace，`select_provider()`，调 `generate_prose`。
- 成功 echo 路径；失败非零退出 + 友好中文错误。
- 便于 Web 子进程复用：`python -m kairo prose <ref_id>`，与 `step` / `re-step` 同一模式。

若 v1 时间紧，Web 可同进程直接调 core；但 **CLI 对称是优选**（与 #52 re-step 从 UI 进 CLI 一致）。

### 5.3 Web

| 项 | 设计 |
|----|------|
| 路由 | `POST /w/{slug}/ref/{ref_id}/prose` |
| 成功响应 | 与 step 类似：填入 `#step-area` 的任务片段；或 200 后直接返回刷新后的 `_ref_meta`（若同步足够快）。**推荐异步任务 + SSE**，因 LLM 可能数十秒 |
| 显示标志 | 渲染 `_ref_meta` 时计算 `can_generate_prose: bool`（§3.2） |
| 按钮 | `btn btn-ghost btn-regen` 风格；`hx-post` → 任务区；可选 `hx-confirm`（v1 可省略，操作可重、但无破坏性） |
| i18n | `prose.gen_btn`、`prose.gen_running`、`prose.err_*`（中英） |
| 忙锁 | 复用 `TaskRegistry.is_running(slug)`，与 step 互斥 |

伪流程：

```text
POST /w/{slug}/ref/{id}/prose
  → registry.start(slug, cwd, [python, -m, kairo, prose, id])
  → render _step.html (SSE)
  → 完成后用户再点 ref 或 OOB 刷新 meta 见 prose 行
```

完成后续刷新：SSE `done` 后现有 step UI 会提示；可在 done 时 `hx-trigger` 拉一次 `GET /w/{slug}/ref/{id}` 进 `#meta`（若实现成本低则做，否则文档写明「完成后请再点该参考」——**优先自动刷新 meta**）。

### 5.4 模板改动点

- `templates/_ref_meta.html`：在 attach 按钮下增加条件按钮。
- `views.py`：ref 详情 context 增加 `can_generate_prose`；新 POST 路由。
- `i18n.py`：文案键。
- 无新静态构建链。

## 6. 数据与契约

### 6.1 落盘

```text
references/<ref_id>/
  manifest.yaml   # forms 追加 role=prose, location=..., origin=normalize-from:<hash>
  prose.md        # 生成正文
  transcript...   # 不变
  digest.md       # 不变
```

`state.json`：`products["references/<id>/prose.md"]` 记账（与 NormalizeRule.run 一致），便于日后若开启全局 normalize 时 `is_stale` 收敛。

### 6.2 不变式

1. `body_roles` 仍为 `transcript` / `source_text`，**不含** prose。
2. `generate_prose` 成功后 `DigestRule.is_stale` 对已有 digest **不**因 prose 出现而变 true。
3. 不修改 `constitution.yaml`。
4. corpus / `origin=added` 拒绝生成，与 NormalizeRule 一致。

## 7. 错误模型

| 情况 | 行为 |
|------|------|
| ref 不存在 | 404 / CLI 退出 1 |
| 非 stream | 400：基线不生成文稿 |
| 无机器 transcript | 400：需先有 ASR 誊录 |
| 已有 prose | 400 或 200 幂等跳过；**v1 选 400 + 明确文案**（按钮本不应显示） |
| provider 失败 | 任务失败日志；不写半截为成功 form（与现 agent 失败行为对齐：不落假成功态） |
| workspace 任务忙 | 返回 step.busy 同类提示 |

## 8. 测试计划

### 8.1 端到端（用户可见路径）

1. Web 打开含音频 stream ref 的 workspace（已有 transcript、无 prose）。
2. 左侧点该参考 → 右侧出现「生成可读文稿」。
3. 点击 → 任务完成 → 形态表出现「文稿」，预览 `prose.md`（有标点/分段，非 raw ASR 稠密噪声形态）。
4. 再进该 ref：按钮不显示；`digest.md` 内容与 `products` digest 项未因本次改变。
5. 负例：纯文档 ref / corpus / 无 transcript → 无按钮；若强 POST → 4xx。

### 8.2 单元 / 功能

- `generate_prose`：成功路径写文件 + form + products。
- 前置：unknown / corpus / no transcript / prose-exists。
- `step()` 在 `normalize.enabled=false` 时仍不产 prose（回归）。
- Web：`can_generate_prose` 矩阵；POST 启动任务或同步成功；不写 constitution。
- 断言 digest product input_hash 在 generate_prose 前后不变（固定 fixture）。

### 8.3 不测

- LLM 文风质量（非 CI）。
- 多用户并发（产品定位单用户本地）。

## 9. 实现切片（建议顺序）

1. Core：`generate_prose` + 单测。
2. CLI：`kairo prose <id>` + 薄测。
3. Web：`can_generate_prose` + 按钮 + POST + i18n。
4. 任务完成后刷新 meta。
5. README 一句：Web 可对单条参考生成可读文稿（可选）。

## 10. 风险与开放点

| 点 | 说明 | 倾向 |
|----|------|------|
| LLM 耗时 | 长 transcript 可能分钟级 | 异步任务 + SSE，与 step 同 |
| 与全局 normalize 双路径 | 按钮旁路 vs constitution 开关 | 文档写清；行为一致（同一 NormalizeRule 逻辑） |
| 已有 prose 是否允许重产 | v1 不做 | 若用户要，另开 issue「重新生成文稿」 |
| 是否必须 CLI | Web 可同进程 | **建议有 CLI**，复用子进程模式 |

## 11. 交叉链接

- Issue: https://github.com/xforce-io/kairo/issues/60
- 分支 / worktree: `feat/60-ref-generate-prose`（`../kairo-60-ref-generate-prose`）
- 本文：`docs/design/60-ref-generate-prose.md`（SSOT）
