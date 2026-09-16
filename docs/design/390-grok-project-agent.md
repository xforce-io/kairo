# #390 — Grok 支持 Project agent（`supports_project_cli`）

- Issue: [#390](https://github.com/xforce-io/kairo/issues/390)
- 分支: `feat/390-grok-project-agent`
- 状态: Approved
- 最后更新: 2026-09-16（L1 Approved · peng via Grok Bot widget；defaults A / override-only / fake+live）
- L1: Approved（peng 2026-09-16；§8.1 A、§8.2 override-only、§8.3 fake+live）

本文件是 #390 的 L1 事实源。Issue 只保留摘要与本链接。不自批。

## 1. 背景

[#390](https://github.com/xforce-io/kairo/issues/390)。Project agent Task 要求 provider 声明 `supports_project_cli`。现网仅 `CodexProvider` 为 True。本机 Console 若固定 `KAIRO_PROVIDER=grok`（`KAIRO_PROJECT_AGENT` 未设），`select_project_agent()` 选到 Grok 后因能力位被拒，Run 直接 `failed` / `reason=provider_unsupported`（UI：「当前 provider 不能运行 Project Task」）。同一环境 Topic `step` / `run` 走 Grok 已正常（#61 / #350）。能源团队管理历史上成功的 Project run 均为 `provider=codex`。用户要 Grok 也能跑 Project agent，不必为 Project 单独切 Codex。

发现于 #388 Notion Project datasource E2E。

## 2. 现有机制（keel-how · Forge）

对照 `feat/390-grok-project-agent` tip（与 `main` @ `e2726f1` 对齐；契约边界引用，非实现清单）：

- `src/kairo/provider.py` — `AgentProvider.supports_project_cli`；`AgentConfig.write_dirs` 注释为「Project 缓存与本 Run 临时读取记录；不得含 serve root / Topic」。`CodexProvider.supports_project_cli=True`，对 `config.write_dirs` 逐项传 `--add-dir`（Codex sandbox 本就可读绝对路径；`--add-dir` 把目录升级为可写；见 `--sandbox workspace-write`）。`GrokProvider.supports_project_cli=False`。Grok #350 仅在 `read_dirs` 非空时加 `--allow Read`；**无** `--add-dir`（[#61](61-grok-provider.md) / [#350](350-grok-read-dirs.md)）。`write_dirs` 在 Grok 路径上被忽略。`ClaudeCodeProvider` / `OpenAICompatibleProvider` / `StubProvider` 均为 `supports_project_cli=False`。`select_provider()`：`KAIRO_STUB` → 显式 `KAIRO_PROVIDER` → auto `codex` → `grok` → `claude` → openai → stub；**不**按 `supports_project_cli` 过滤。
- `src/kairo/projects.py` — `select_project_agent()`：若设 `KAIRO_PROJECT_AGENT` 则只实例化该 backend，缺 `supports_project_cli` 则 `None`（override-only，不回落到 `select_provider()`）；未设则 `KAIRO_STUB` → `None`，否则 `select_provider()` 后再拒无能力位者。`_run_agent_task` 在 `agent is None` 或无 `supports_project_cli` 时写 `status=failed`、`reason=provider_unsupported`，不进入 `_execute_agent_run`。`_execute_agent_run` 在临时 `artifact_dir`（`tempfile.mkdtemp`，即 grok cwd）写 `SKILL.md` + prompt，令 agent 用 `python -m kairo` 跑 `project context` / `project read`（禁止 step / re-step / accept / 写 Topic）；`AgentConfig(write_dirs=[cache_root, scratch])`，其中 `cache_root = _project_dir(serve, project_id) / "cache"`，`scratch = scratch_dir(...)` → `_project_dir / "scratch" / run_id`（均在 serve 下、**cwd 外**）。宿主在 agent 退出后校验已登记 `input_id`、行号与 Data Source 已读，再 `finalize_inputs` 并落 Artifact。
- `src/kairo/project_materials.py` — `cache_dir` / `scratch_dir` / `inputs_dir` 均在 `_project_dir` 下；`project context` / `project read` 会写 cache 与本 Run scratch。
- Topic digest/compose：只传 `read_dirs`（工作集），**从不**传 `write_dirs`（全树仅 `_execute_agent_run` 赋值）。Grok Topic 路径保持 #350：`read_dirs` 非空 → `--allow Read`；无 `--always-approve` / `--add-dir`。
- 已批契约：[#299](299-project-context-task.md) §6.1 — `supports_project_cli` 不能复用 `supports_read_dirs`；名称本身不构成证据；附加可写范围仅 cache + 本 Run scratch；Topic / `project.json` / 其他 Run / 终态 inputs **不授写**；后端不能表达写边界时视为不支持，**不关闭沙箱绕过**。Codex 用 `--add-dir` 授写独立目录，不能对整个 serve root 或源 Topic 使用 add-dir。

今日事实：`KAIRO_PROVIDER=grok`（或 `KAIRO_PROJECT_AGENT=grok`）在能力门闩处失败，到不了 Project CLI / cache / scratch。只翻 `supports_project_cli=True` 而不接线 `write_dirs`，S1 过、S2 仍会在 cwd 外写 cache/scratch 或跑 `python -m kairo` 时被 grok 权限挡住。伪造 `--add-dir` 违反 #61/#350。给 Topic 路径加全局 `--always-approve` / `--dangerously-skip-permissions` 违反 #350/#160。

## 3. 名词解释

沿用 [docs/glossary.md](../glossary.md) 的 Project、Data Source、Task、Run、Artifact、授读。本票用语：

| 用语 | 本票含义 |
|---|---|
| Project agent | `mode=agent` 的 Task Run：宿主选 `supports_project_cli` 的 provider，agent 按 Skill 调 `kairo project context` / `project read`，写 `artifact.md` |
| 能力位 | `AgentProvider.supports_project_cli`；False → `select_project_agent()` 拒、`_run_agent_task` → `provider_unsupported` |
| `write_dirs` | 本 Run 附加可写目录闭集：Project `cache/` 与本 Run `scratch/{run_id}`。不得含 serve root / Topic / 其它 Project |
| Topic Grok 路径 | digest/compose：`read_dirs` 可非空，`write_dirs` 为空；#350 `--allow Read` |
| 最小额外权限 | 仅当 `write_dirs` 非空时，为 Project CLI + cache/scratch 写盘增加的 grok CLI 参数。不得发明 `--add-dir`，不得把全局 `--always-approve` / `bypassPermissions` 绑到 Topic 路径 |

## 4. 目标与非目标

### 4.1 目标

1. **S1**：`KAIRO_PROVIDER=grok`（或 auto 选到 Grok，或 `KAIRO_PROJECT_AGENT=grok`）时，`select_project_agent()` 返回可用的 `GrokProvider`（`supports_project_cli=True`），不再因能力位直接 `provider_unsupported`。
2. **S2**：该 Grok Project agent Run 能执行 `project context` / `project read`（经 `python -m kairo`），按需写 cache / scratch；succeeded Artifact 可打开，引用的 `input_id` 均已登记；若失败，原因为业务 / 读取错误，而不是能力门闩或「缺写目录」。至少 1 次 live 或等价集成证明读过 scope 内 Data Source。
3. Topic `step` / `run` 的 Grok 路径不回退：`write_dirs` 为空时 CLI 参数与 #350 一致（`read_dirs` 非空才 `--allow Read`；无 `--add-dir`；无 `--always-approve`）。

### 4.2 非目标

- 不给 Claude / OpenAI-compatible / Stub 开 Project agent。
- 不做嵌套外链（企微 / 墨刀等）自动跟读。
- 不改 Notion datasource 产品行为（#388）。
- 不强制用户改掉 `KAIRO_PROVIDER=grok`，也不把 auto 默认从 Codex 改成 Grok。
- 不发明 grok 不存在的 `--add-dir`。
- 不把全局 `--always-approve` / `--yolo` / `--dangerously-skip-permissions` 接到 Topic 路径，也不把它当 Project 的默认绕过。
- 不改 `AgentConfig.write_dirs` 语义（仍不得含 serve root / Topic）。
- 不新增 `KAIRO_DIGEST_PROVIDER` 或拆 digest/compose backend（#350 已否）。

## 5. 能力与契约（L1）

### 5.1 选择与能力位

| 输入 | 今日 | 本票后 |
|---|---|---|
| `KAIRO_PROVIDER=grok`，未设 `KAIRO_PROJECT_AGENT` | `select_provider()` → Grok → `select_project_agent()` `None` → `provider_unsupported` | 返回 `GrokProvider`，进入 `_execute_agent_run` |
| `KAIRO_PROJECT_AGENT=grok` | 实例化 Grok 后因能力位 `None` | 返回 `GrokProvider`（仍 override-only，见 §8） |
| auto 且 Codex CLI 可用 | Codex（`supports_project_cli=True`） | **不变** |
| auto 且仅 Grok CLI 可用 | 选到 Grok 后被拒 | 返回 Grok |
| `KAIRO_PROVIDER=claude-code` / `openai` / stub；`KAIRO_STUB=1` | 拒 / `None` | **不变** |

S1 只要求能力位与选择门接受 Grok。S1 单独翻旗而不接线 `write_dirs` **不够 S2**。

### 5.2 Grok CLI · `write_dirs` 非空（Project 路径）

宿主已把 `write_dirs=[cache_root, scratch]` 传入，且两目录在 grok cwd（临时 `artifact_dir`）之外。Codex 用 `--add-dir` 表达该边界。Grok **没有** `--add-dir`（#61/#350；xAI CLI reference 亦无此旗标）。

**已锁定（peng 2026-09-16 · §8.1 A）：**

1. `GrokProvider.supports_project_cli=True`。
2. **仅当** `write_dirs` 非空：在现有 args 上增加 `--allow Bash` + `--allow Write`，使 headless grok 能 (a) 跑 `python -m kairo`，(b) 让该子进程与 agent 写 `write_dirs` + cwd 内 `artifact.md`。
   - 实现环境无 `grok` on PATH，无法 `grok --help`。xAI permissions 认可名：`Bash`（不是 `Shell`）、`Write`（`Edit` 别名）。单测锁这两项字面量。
3. 禁止：伪造 `--add-dir`；Project 默认 `--always-approve` / `--yolo` / `--dangerously-skip-permissions`；把 serve root 或 Topic 塞进可写范围；为迁就 grok 改 `write_dirs` 语义。
4. 不把「关沙箱」当默认绕过（#299）。不传 `--sandbox`（保持与 Topic 相同的默认 `off`）。不主动加 `--sandbox off`。`GROK_SANDBOX` 非空且非 `off` 时 **fail-closed**（`RuntimeError`，不调用 grok）：不静默改 profile、不 symlink `write_dirs` 进 cwd、不关沙箱。

### 5.3 Grok CLI · `write_dirs` 为空（Topic 路径）

与 #350 字节级对齐：

- `read_dirs` 非空 → `--allow Read`；否则不加 `--allow`。
- 不含 `--add-dir`、`--always-approve`、`--yolo`、`--dangerously-skip-permissions`。
- 不因本票给 Topic 预授 Bash / Write。

单测须继续锁这条（现有 `test_grok_provider_allows_read_dirs_with_allow_read`）。

### 5.4 Run / Artifact 契约（不改宿主）

沿用 #299 / #315：

- prompt / Skill / `python -m kairo` 入口不变。
- 成功：`artifact.md` 非空；`[标题](input:INPUT_ID)`（可选 `#L…`）均已登记；有 Data Source 的 scope 须读过至少一条 datasource，否则 `datasource_unread`。
- 失败码闭集不因本票新增：`provider_unsupported` 只留给仍无能力位的 backend（Claude / OpenAI / Stub / 显式不支持者）。Grok 过门后的失败走既有 `provider_failed` / `empty_artifact` / `invalid_input_ref` / Reader 错误等。

### 5.5 Console / CLI

无新页面、无新子命令、无新 env 键。行为变化：

- `KAIRO_PROVIDER=grok`（或 auto 选到 Grok / `KAIRO_PROJECT_AGENT=grok`）下触发 agent Task：不再因能力位 400 / `provider_unsupported`。
- 未设或 `KAIRO_PROVIDER=codex`：与现网相同。

## 6. 思路与折衷

**选择：只给 Grok 补 Project 能力位，并在 `write_dirs` 非空时加最小额外权限；Topic 路径零扩散。**

| 选择 | 放弃 |
|---|---|
| 复用 `supports_project_cli` + 现有 `write_dirs` 契约 | 放弃新 env、放弃把 `supports_read_dirs` 当 Project 门 |
| `write_dirs` 非空才加权限 | 放弃 Topic 预授 Shell/Write；放弃全局 `--always-approve` |
| 不发明 `--add-dir` | 放弃把 Codex 旗标抄到 grok argv |
| auto 顺序不变（Codex 仍先于 Grok） | 放弃逼用户改 `KAIRO_PROVIDER`；放弃把默认改成 Grok |
| 写边界仍由宿主 `write_dirs` 闭集 + Skill/CLI 范围表达 | 放弃 symlink-into-cwd 当默认（多一层移动部件，且 #299 要的是 provider 能表达边界，不是宿主改布局） |

代价：Grok 没有路径级 `--add-dir`，OS 写边界弱于 Codex。#299 已承认「通用 shell ≠ OS 隔离」；本票把「最小 `--allow Bash` + `--allow Write`」限制在 Project 路径（§8.1 A）。只翻旗、不接线 `write_dirs`，S2 失败。

**证明边界：** 产品 S1/S2 只认 kairo CLI / Console 触发的 Project agent Run。MCP 或手工 `grok -p` 演示不算过线。

## 7. 模块边界（所有权）

| 面 | Owner | 变化方向 |
|---|---|---|
| `GrokProvider.supports_project_cli`；`write_dirs` 非空时的最小 CLI 权限；Topic 路径不回退 | Forge（engine / CLI provider） | 能力位 + Project-only args；单测锁 Topic argv |
| `select_project_agent` / `_run_agent_task` / `_execute_agent_run` | Forge | 选择逻辑不改结构；Grok 过门后走既有宿主。`KAIRO_PROJECT_AGENT` 默认仍 override-only（§8.2） |
| Skill / `project context` / `project read` / 证据校验 | Forge | **不改**协议；S2 证明 Grok 能走通 |
| Console 文案 / i18n | Atlas | 无新文案。能力位修复后 `proj.reason_unsupported` 不再出现在 Grok 主路径 |
| verify-kairo-web | Atlas | 本票无新 Console Story；勿在 scratch 对 live root 触发 LLM Run |

## 8. 已锁定（peng 2026-09-16）

### 8.1 cwd 外 `write_dirs` 的精确 grok 旗标

现网 Grok **没有** `--add-dir`。cache / scratch 在临时 cwd 外。xAI CLI（docs.x.ai，对照用、非本机实测）：`--allow` / `--deny`；`--sandbox` 默认 `off`；`workspace` profile 只写 CWD / `~/.grok/` / temp；`--always-approve`（alias `--yolo`）与 Claude 别名 `--dangerously-skip-permissions` 存在。本环境无 `grok` 可 ` --help`，**不得把文档当现场 argv 真相**。

请批一条，禁止实现时并行 fallback：

| 案 | 做法 | 风险 |
|---|---|---|
| **A（L1 默认假说）** | `write_dirs` 非空时加 `--allow Shell`（或文档等价的 Bash）+ `--allow Write`；不传 `--sandbox`（保持与 Topic 相同的默认 `off`）；不传 `--always-approve` | 写边界靠 Skill/CLI/宿主校验，弱于 Codex `--add-dir`。若现场钉了 `GROK_SANDBOX=workspace`，cwd 外写会失败——应 fail-closed，不静默改 profile |
| B | 把 cache/scratch symlink 进 cwd，再靠 cwd 写 | 改宿主布局；#299 要 provider 表达边界，不是搬目录。违背 fail-fast |
| C | Project 路径 `--always-approve` / `--dangerously-skip-permissions` | #350/#160 已否全局绕过；即使用 `write_dirs` 门控，仍无路径边界 |
| D | 自定义 sandbox profile 把 `write_dirs` 加成可写根 | 要写用户 `sandbox.toml`，超出本票、且本机未验证 |

**已锁定：A。** 实现 argv：`--allow Bash` + `--allow Write`。本环境无 `grok --help`；`Bash` 为 xAI 认可的 Shell 等价名（`Shell` 不是 filter）。不得用 B/C/D。`GROK_SANDBOX=workspace`（或任何非 `off`）→ fail-closed。

### 8.2 `KAIRO_PROJECT_AGENT` 是否仍只做 override

今日：仅当 env 有值才走该名字；否则 `select_provider()`。**已锁定：保持 override-only。** `KAIRO_PROVIDER=grok` 已足够 S1。不把 `KAIRO_PROJECT_AGENT` 提升为第二默认，也不在未设时改 auto 顺序。

### 8.3 S2 证明：live Grok vs fake-runner

| 案 | 做法 |
|---|---|
| **A（L1 默认假说）** | CI：fake-runner 单测锁 argv（`write_dirs` 非空含最小 `--allow`；空则 #350；永不 `--add-dir` / `--always-approve`）+ 既有 `ProjectCliTestProvider` 协议回归。S2 产品验收：本机已登录 grok 至少 1 次 live Run（读 scope 内 Data Source，Artifact 含已登记 `input_id`）。默认 CI **不**跑真实 grok（同 #61/#350） |
| B | 只靠 fake-runner「等价集成」，无 live | 证明不了 cwd 外写与 Shell 预授 |

**已锁定：A。** CI fake-runner 锁 argv + 既有协议回归。无 grok 登录的环境不得声称 S2 产品过线；默认 CI **不**跑真实 grok。

## 9. 验收映射

- S1 ← §5.1（能力位 + 选择；`KAIRO_PROVIDER=grok` / auto 选到 Grok / `KAIRO_PROJECT_AGENT=grok`）
- S2 ← §5.2 + §5.4（Project CLI + cache/scratch + 合法 Artifact；失败非能力门闩）
- Topic 不回退 ← §5.3（不是新 Story，是 S1/S2 的回归门；破则本票失败）

S1/S2 只认 kairo CLI（及适用的 Console）。L1 已批；本分支实现 Forge engine/CLI only。

## 10. 关联

- Issue [#390](https://github.com/xforce-io/kairo/issues/390)
- [#61](61-grok-provider.md) Grok CLI provider（无 `--add-dir`）
- [#350](350-grok-read-dirs.md) Topic 授读（`--allow Read`；禁 `--always-approve` / `--add-dir`）
- [#299](299-project-context-task.md) Project CLI 与 `write_dirs` 边界
- [#388](388-notion-project-datasource.md) 发现现场（能源团队管理）
