# Digest/Compose 目录授读，并拆除判断层

- Issue: [#153](https://github.com/xforce-io/kairo/issues/153)
- 分支: `feat/153-catalog-read-no-assessment`
- 状态: Approved（会话拍板）
- 最后更新: 2026-08-27

## 1. 背景

Digest 把一条 stream reference 下全部 `transcript` / `source_text` 拼进 prompt。立项材料包（可研 + 多张 xlsx 转写）单条可达数兆，Codex 无法写出 `--output-last-message`，工作项记为 `provider-failed`。Compose 对 corpus 已是文件清单 + `read_dirs`，对当前文档和 Δdigest 仍整份内联，两段输入契约不一致。

产品同时维护 `understanding.md`（事实）与 `assessment.md`（判断）。判断层不再作为产物维护：不 fold、不并入事实层、不进主路径；磁盘旧文件停更。

链 issue [#153](https://github.com/xforce-io/kairo/issues/153)。L1 见该 issue comment。

## 2. 名词解释

本设计新增或易混：

| 术语 | 定义 |
|---|---|
| **材料目录** | 写入 provider prompt 的表：每条材料的标记（必读/按需）、角色、来源、路径、体量。不含正文。 |
| **授读** | 让 agent 只读所选材料：文件复制进临时工作集；所选目录才通过 `read_dirs` 授权。 |
| **工作集** | agent 工作目录内受控、唯一的材料文件副本；必读与按需分目录，避免授权或改写源文件。 |
| **活 target** | `constitution.targets` 中 `layer != judgment` 的综合文档；默认且唯一为 `understanding.md`。 |

已有术语（digest、fold、stream、corpus、transcript）见 [docs/glossary.md](../glossary.md)，不抄。

## 3. 目标与非目标

- **目标**：
  - Digest / Compose 共用材料目录 + 授读 + 必读/按需。
  - Digest prompt 不含正文全文；目录讲清角色与来源。
  - Compose 不读 stream 原文；当前 `understanding.md` 与本步全部 Δdigest 进入工作集；corpus 按需。
  - 无授读能力的 provider 失败，不回退倾倒全文。
  - 默认与主路径只产 `understanding.md`；`assessment.md` 停更。
- **非目标**：
  - 从评审录音蒸馏 digest prompt。
  - Compose 再读 stream 原文。
  - 自动把旧 `assessment.md` 并入 understanding。
  - 删除用户磁盘上的 `assessment.md`。
  - 为 Grok 做全文倾倒回退。
  - 改变 ASR / markitdown / digest 指纹「源变则重算」的语义。

## 4. 能力

1. Digest 将 form 编成材料目录（正文必读、附件按需），并把所选文件复制进临时工作集。
2. Compose 将当前 understanding、Δdigest（必读）与 corpus（按需）编成同一形态目录；文件进入工作集，corpus 目录仅授权该目录。
3. Codex 使用 sandbox 原生只读能力，不传会扩大可写根的 `--add-dir`；claude-code 对所选目录沿用 `--add-dir` + Read-only tools。
4. `supports_read_dirs` 为假的 provider（Grok、openai-compatible）在存在材料目录时抛错 → `provider-failed`。
5. `kairo new` / 默认 constitution 只有 `understanding.md`。`layer=judgment` 不 discover、不进 Web 产物栏、不进 Run 的 target 阻塞计数。

### 4.1 UI/UX

- **信息架构**：左栏产物只列活 target（默认 `understanding.md`）。磁盘上若仍有 `assessment.md` 且 constitution 仍声明该 path，不在主栏作为待 fold 产物；不新增「归档判断」页。
- **全状态**：digest/compose 失败仍为 `provider-failed`（#98）。无授读时摘要需能看出「不支持授读」，不得伪装成超时。
- **布局/交互**：Run 按钮语义不变（#75）。不改添加参考、听读、时间轴。

## 5. 思路与折衷

核心：统一的是输入契约，不是「禁止任何文件出现在磁盘工作集」。

| 选择 | 放弃 |
|---|---|
| Prompt 只放目录 | 放弃继续把 source_text 拼进 context（#44 对「一场会几页材料」成立，对材料包不成立） |
| 所选文件复制进工作目录；仅所选目录授读 | 放弃授权源文件父目录（会泄露兄弟文件，Codex `--add-dir` 还会扩大可写根） |
| Compose 仍只吃 digest | 放弃 compose 读可研/xlsx（digest 将被架空） |
| 无授读则失败 | 放弃 Grok 倾倒回退（会回到本 issue 的故障） |
| 运行时跳过 `layer=judgment` | 放弃改写用户 constitution、删除旧文件、把判断并进 understanding |

Grok 的 `grok -p` 无文件工具：目录化之后它看不见正文，失败是正确行为，不是回归。

## 6. 架构

分层：

```
constitution.targets（声明）
        ↓ live_targets: layer != judgment
DigestRule / ComposeRule
        ↓ 材料目录 + 受控工作集副本 + 所选目录 read_dirs
_run_agent → AgentConfig
        ↓ supports_read_dirs?
Provider（codex 原生只读 / claude-code 授读；其它拒绝）
```

主路径：有正文 form → digest（目录+授读+必读副本）→ 写 `digest.md` → compose（understanding + Δdigest 必读）→ 写 `understanding.md`。

失败路径：provider 不支持授读或 CLI 失败 → 不写半成品（#98）→ `provider-failed`。Grok 在 digest/compose 只要带了 `read_dirs` 即走失败路径。

## 7. 模块

| 模块 | 变化 |
|---|---|
| `catalog`（新） | `CatalogItem`、目录格式化、唯一且防越界的临时工作集路径、所选目录去重 |
| `rules` | Digest/Compose 改用目录；Compose 只 discover 活 target；`_run_agent` 检查授读能力并落工作集 |
| `provider` | `supports_read_dirs`；Codex 不扩大可写根；Grok/openai 遇材料授读抛错 |
| `models` | 默认 targets 仅 understanding；fold_protocol 去掉判断层指令 |
| `web/cli/skill/README/glossary` | 主路径只认 understanding；文案去掉两层产出 |

## 8. API/CLI

对外子命令不变。行为变化：

- `kairo new` / `init`：`constitution.targets` 仅 `understanding.md`。
- `kairo status`：不把 `layer=judgment` 当作待生成/待 fold 的活 target。
- Codex CLI 不为材料传 `--add-dir`；该参数会把源目录加入可写根。所选文件从临时工作集读，所选目录依赖 sandbox 原生只读能力。

无新 HTTP 路径。Web 产物栏数据源改为活 target。

## 9. 边界

- 材料目录同时展示原路径与受控读取路径；临时文件名不使用用户路径，避免 `..` 越界与同名覆盖。
- 大表仍会以文件形式存在；digest prompt 必须要求「表只抽关键数字与口径，禁止整表抄入纪要」。这是指令约束，不是引擎截断文件。
- 既有 workspace constitution 可仍含 assessment：运行时忽略 fold，不自动改 yaml。
- Stub provider：`supports_read_dirs=True`，并从必读路径读文件以保持测试链（正文能流到 digest）。

## 10. 迁移/兼容/回滚

- **默认**：新仓无 assessment。
- **旧仓**：`assessment.md` 留盘；不再 fold；主路径不展示为活产物。state 里旧的 `targets["assessment.md"]` 可残留，不参与 pending。
- **#99**：事实层仍用 S-… 与来源索引。判断层协议仅在仍执行的 judgment target 上存在；默认路径不再生成「依据事实索引」。
- **回滚**：回退本版本后，旧默认宪法会再次声明 assessment；已停更的文件不会被自动删除，可能再次进入 fold。

## 11. 测试计划

- **Unit**：目录格式含角色/来源/必读标记且不含正文；工作集路径防越界/同名覆盖；Codex 不扩大可写根；Grok 需要授读即抛错；Compose.discover 不含 `assessment.md`；`Workspace.init` targets 长度为 1。
- **Integration**：Digest 工作项的 context 无 `source_text` 全文，条目数=正文+附件 form；Compose 必读 Δ 条数=delta；带 judgment 的旧 constitution 执行 step 不写/不更新 `assessment.md`。
- **E2E**：`kairo new` 后 constitution.targets 唯一 path 为 `understanding.md`；Web 产物栏无 assessment（默认仓）。

对上 S1–S4。

## 12. 开放问题

无。蒸馏 digest prompt、按 class 分 digest 规程不在本期。

## 13. 关联

- [#153](https://github.com/xforce-io/kairo/issues/153)
- [#44](https://github.com/xforce-io/kairo/issues/44) 多形态 digest（本期改输入形态，不改「一条 ref 一份 digest」）
- [#13](https://github.com/xforce-io/kairo/issues/13) corpus/stream
- [#61](https://github.com/xforce-io/kairo/issues/61) Grok 无 `--add-dir`
- [#99](https://github.com/xforce-io/kairo/issues/99) 溯源（事实层保留；判断层默认不再产出）
- [#75](https://github.com/xforce-io/kairo/issues/75) / [#98](https://github.com/xforce-io/kairo/issues/98) Run 与失败终态
