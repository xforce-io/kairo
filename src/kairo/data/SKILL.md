---
name: kairo
description: Use when the user wants to operate kairo topic-workspaces in a session — phrases like kairo 现在有哪些调研, 某主题现在什么情况, 事实/判断到哪了, 帮我总结 kairo 结论, 为什么卡住/blocked, 怎么推进, 推进一下/step/重算/re-step, 接受手改/accept, archive 到 Kairo, 归档会话. Not for one-off machine ASR/LLM config unless the user explicitly asks to configure.
---

# kairo operator

把会话里的调研工作区意图翻译成正确的 `kairo` CLI 调用与文件读取，并按协议把输出解读成人话。这是 CLI + workspace 文件布局之上的**薄壳**：不重实现引擎逻辑，不代替人确认写操作。

命令模型：**看（status + 读综合文档）永远便宜**；**算/做（step / re-step / accept …）永远显式确认**。综合产物：`understanding.md`（中立事实）。旧 `assessment.md` 若仍在磁盘上则停更，不当判断层复述。

## 前置自检

首次调用前确认 CLI 可用：`kairo --help`（或开发态 `uv run kairo --help`）。失败 → 转述 README 安装（Python ≥ 3.11、`uv tool install git+https://github.com/xforce-io/kairo.git`，然后 `kairo doctor` / `kairo connect`），不硬闯、不贴裸 stack trace。

## 发现 workspace

合法 topic-workspace：**目录含可打开的 `constitution.yaml`**（实践上还有 `.kairo/state.json`）。与 Web `scan_workspaces` 同判据：只扫 root **下一层**子目录。

定位顺序：

1. 用户给出了 path / root → 用该 path
2. 否则当前 cwd 含 `constitution.yaml` → 单目标 workspace
3. 否则 **`kairo list [root]`**（root 默认 `KAIRO_SERVE_ROOT` 或 cwd；与 Web dashboard 同源；可用 `--json`）
4. 找不到 → 问用户 root，**不要**在随机目录 `init` 或 `step`

每个目标在其 **workspace 根目录** 下跑 CLI（`cd <ws>` 或等价 cwd）。多 workspace 时先 `kairo list`，再按用户点名深入。

## 命令映射（看优先）

**无参默认（用户只说 kairo / 调研现在怎样，无进一步意图）**：意图是「看现状」——别反问「想干嘛」，推断 root 后：枚举 workspace → 各跑 `kairo status` → 用人话摘要。**全程零写、不烧 step token。**

| 用户意图 | 跑什么 |
|---|---|
| 有哪些调研 / spaces / 工作区列表 | **`kairo list`**（或 `kairo list --json`）；勿手写扫盘 |
| 某主题现在什么情况 / 状态 | 在目标 cwd 执行 `kairo status`（只读；可对多个 ws 各跑一次） |
| 新建 / 删除整个 workspace | 确认后 `kairo new "<topic>"` / `kairo rm-ws <slug> --yes`（须讲清不可恢复） |
| 知识 / knowledge | `kairo knowledge list`；写操作用 `add` / `rm`（`--scope workspace|global`）并先确认；`glossary` 是兼容别名 |
| 事实到哪了 / 总结结论 / 结论是什么 | **先** `kairo status`，再读 `understanding.md`（见下节）；必要时下钻 digest，**不**把 raw transcript 当最终结论 |
| 为什么卡住 / blocked / 怎么推进 | 依据 `status` 的 `⚠ blocked:…` 与下表解释含义与选项；**未确认不写** |
| 推进一下 / step / 调和 | **默认**说明副作用（烧 LLM token、可能改文档）→ 确认后 `kairo step`（不自动清终态 blocked） |
| 与 Web 主按钮一致 / run / 含终态 blocked 一并重试 | `kairo run` 在有**可重试**终态 blocked 时会先清派生产物再 step，副作用比 `step` 更重（可重烧 ASR/LLM）；`digest-degraded` 等不可重试终态不会被 Run 清掉——**勿把口语「推进」默认落成 run**；须单独讲清并确认 |
| 重算 / re-step / 重试某条 | 说明副作用（文档级重综合可能丢手改；ref 级重产 digest）→ 确认后 `kairo re-step …` / `kairo retry-ref <id>` |
| 接受手改 / accept | 说明将钉为新基线、解除 `manual-edit` → 确认后 `kairo accept <doc>` |
| 回退 / rollback | 说明文档回退到快照、references 不动 → 确认后 `kairo rollback <seq>`（可先 `kairo history` / `kairo diff` 只读预览） |
| 登记材料 / add | 说明路径指针 vs `--copy`、stream vs `--corpus` → 确认后 `kairo add …`；往既有参考追加形态用 `kairo add <file> --to <id> [--copy]` |
| 归档会话 / archive 到 Kairo | 确认后 `kairo archive`（见下节）；**不要**用 `kairo add`。成功回执必须原样写回；**不**自动 `step` |
| 改参考展示名 / title | 确认后 `kairo title <id> <新名>`（不改 id） |
| 发生时间 / 时间轴 | 只读 `kairo timeline [root]`（`--day` / `--recent` / `--json`）；写发生时间确认后 `kairo occurred <id> YYYY-MM-DD` 或 `--clear` |
| 删参考 / rm-ref | 说明会改 state；确认后 `kairo rm-ref <id>`（若带 `--recompose` 会立刻重综合，副作用更大，须单独确认） |
| 生成 prose / 重建 MEETINGS 索引 | 写磁盘；确认后 `kairo prose <id>` / `kairo index` |

纯读命令（无需确认）：`list`、`status`、`history`、`diff`、`knowledge list`、`glossary list`、`timeline`，以及直接读 workspace 内 markdown / state。

## 怎么读知识产物

**读路径优先序**（固定）：

1. `kairo status` — fold 进度、blocked、corpus 漂移提示
2. `understanding.md` — **事实层**（中立、可标来源）；综合结论在这里
3. 需要出处/细节时再下钻 `references/<id>/` 下的 **digest**（高密度记忆纪要 = 该条 reference 的记忆）
4. transcript / source_text / prose / 原始 form — **原料或人读档案**，不是「调研结论」

回复必须：

- 给出可打开核对的文件路径（至少 `understanding.md`；未生成则说明）
- 文件为空或不存在 → 如实说「未生成 / 空」，**不编造**结论
- **禁止**：把 transcript 当最终结论；把停更的 `assessment.md` 当现行判断；把 corpus 基线材料当成已 fold 的观测进度

## blocked 闭集与下一步

`status` 里 `⚠` / `blocked:` 原因闭集（与 README 一致）：

| reason | 含义 | 典型下一步（需用户确认后再写） |
|---|---|---|
| `no-asr` | 本机未配 ASR 后端 | 配 `~/.config/kairo/config.toml`（或 env）后下次 `step` 可自动重试 |
| `asr-failed` | 转写命令失败 | 查 ASR 命令；**终态**，需确认后 `retry-ref` / `re-step` |
| `convert-failed` | 二进制转换失败或空产物 | 查源文件；终态，需确认后重试 |
| `missing-source` | 源路径不可达 | 恢复源或 `--copy` 重登记 |
| `manual-edit` | 文档被手改，待接受 | 确认后 `kairo accept <doc>`；或放弃手改再 `re-step`（会丢手改，必须讲清） |
| `compose-degraded` | 综合输出骤缩，已拒绝覆盖以保护旧文档 | 终态；确认后 `re-step` 重算。若 `understanding.md` 已超过 20,000 字符，按 `compose-migration-required` 观察与恢复 |
| `digest-degraded` | 纪要输出骤缩，已拒绝覆盖以保护旧 digest | 终态；普通 Run 不会清掉该 digest。确认后 `kairo re-step <id>` / `retry-ref` |
| `compose-migration-required` | 旧 `understanding.md` 超过 20,000 字符，普通 run 不静默压缩（含超长 leftover `compose-degraded`） | 说明“全量重综合会压缩历史正文，失败保留旧版”；确认后 `kairo re-step understanding.md` |
| `compose-over-budget` | 候选 `understanding.md` 超过 20,000 字符，已拒绝覆盖 | 终态；确认压缩代价后 `kairo re-step understanding.md` |
| `compose-provenance-invalid` | 候选溯源结构无效，已拒绝覆盖 | 终态；检查来源后确认 `re-step` |
| `provider-failed` | provider 调用或材料读取能力失败 | 查安全诊断；恢复后可 Run，或确认后 `re-step` |

规则摘要：

- 前置条件变化后，部分 blocked 在下次 `step` **自动**重试（如配好 ASR 后的 `no-asr`）
- `asr-failed` / `convert-failed` / `compose-degraded` / `digest-degraded` / `compose-migration-required` / `compose-over-budget` / `compose-provenance-invalid` 视为**终态**，需手动 `re-step` / `retry-ref`
- skill **解释 + 给选项**；**绝不**未授权就 `accept` / `step` / `re-step`

## 铁律

1. **默认只读**：用户说「看看 / 什么情况 / 总结一下」→ 写类命令次数 = 0（`add` / `archive` / `step` / `run` / `re-step` / `retry-ref` / `accept` / `rollback` / `rm-ref` / `rm-ws` / `new` / `prose` / `index` / `init` / `glossary add|rm` / `occurred` 皆算写或副作用）
2. **写操作先确认**：执行前说明命令与副作用（token、覆盖文档、丢手改、改 state）。用户本轮已明确「直接执行 / 不用问了」可跳过确认
3. **不代批 accept**：手改接受权在人；只解释 `manual-edit`，确认后才 `accept`
4. **不串 cwd**：始终在目标 workspace 根执行 CLI；多 workspace 禁止在 A 目录对 B 主题 step
5. **不擅自 init / 不碰机器配置**：`init`、ASR/LLM 本机配置是一次性设置；用户没明确要求就交回普通流程并给 README 锚点
6. **看永远便宜**：缺结论时读文件 + `status`，**绝不**为了「看起来完整」顺手 `step` 烧 token
7. **只操作用户给出或可推断的本地 path**；不把 workspace 内容无故外传

## 输出解读

- `status` 行形态：`reference <id>: [roles]`；blocked 时带 `⚠ …:reason`
- `target understanding.md: folded N;距上次 A 已 D 条`；未生成时 `(未生成)`；手改/失败时 `⚠ blocked:reason`；corpus 变更时 advisory「corpus 已变,可 re-step 重算」——**advisory 不是自动执行**
- 把上述归纳成人话：名称、进度、blocked 列表、建议下一步（问句），别贴超长原始日志

### Web ACTIONS 主按钮

灰色 **Needs re-step** / **需要 re-step**（`disabled`）+ **Blocked: N** = `plan=attention`：只有不可自动重试的终态 blocked。这是状态，不是动作。

- **不是**按钮坏了；**不是** METADATA 里当前选中的 reference 要重跑
- **不要**点主按钮，**不要**把「点 re-step」落成 `kairo run`（attention 下普通 run 不会综合）
- 先 `kairo status` 看 `⚠ blocked:reason`
- `compose-migration-required`（含超长 leftover `compose-degraded`）：恢复入口在左边活 target（`understanding.md`）的「重新生成」，或讲清压缩代价后确认 `kairo re-step understanding.md`（失败保留旧版）

命令报错 → 如实呈现 stderr 要点，不臆造 workspace 状态。

## Common mistakes

- 想看状态却跑 `step` / `run` → 白烧 token 还可能改文档。**看用 `status` + 读 md。**
- 把 `serve` root 当成单个 workspace 在 root 上 `status` → 失败或误导。**先 `kairo list`。**
- 问「有哪些 spaces」却手写 `find` 扫盘 → 应用 **`kairo list`**。
- 把 transcript / prose 摘要当成「调研结论」→ **结论在 understanding.md。**
- 未确认就 `accept` 或 `re-step` → 钉基线或丢手改。**先说明副作用。**
- 用户说「推进」就对所有 workspace 批量 `step` → 越界。**先列清单，确认范围。**
- 找不到 CLI 就编造 status → **先修安装/PATH。**
- 用户说「archive 到 Kairo」却走 `kairo add`，或把 compaction 摘要当完整会话续接 → 会错绑或分叉。**用 `kairo archive`；输入必须是宿主原始 transcript。**
- 把灰色 **Needs re-step** / **需要 re-step** 当成可点 Run，或当成当前选中 reference 的 retry → **先 `kairo status` 看 reason**；主按钮在 attention 下故意不可点。

## 归档会话（`kairo archive`）

用户说「archive 到 Kairo」时：

1. 导出**宿主保存的原始 transcript**（或与之逐字等价的 Markdown）到本地文件。上下文已被 compaction 时，不得用回忆/摘要冒充完整会话去自动续接。
2. 调用 `kairo archive SESSION.md`（serve root 与 `kairo list` 相同：`--root` / `KAIRO_SERVE_ROOT` / cwd）。不要 `cd` 进某个 workspace 再 `add`。
3. **退出码 0**：把 stdout 整行信封原样写入本轮最终回复，独占顶层一行，不得包进代码围栏或引用块。`--json` 时只复制 `receipt`。宿主 compaction 中逐字保留该信封（best-effort）。
4. **退出码 2**：stdout 是 JSON 清单（`need-workspace` / `need-bind` / `fork`）。转述给人，问清 workspace，以及新建（`--workspace SLUG --create`）还是绑定已有归档（`--workspace SLUG --bind REF`）。**禁止**自行挑选。
5. **退出码 1**：转述 stderr，不编造已归档。
6. 归档成功后**不**自动 `step`。要把该会话折进 understanding，须按铁律单独确认后再 `step`。
