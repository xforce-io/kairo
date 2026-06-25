# #6 为跑真实基准做准备：subscription backend

> 本文是 Issue [#6](https://github.com/xforce-io/kairo/issues/6) 的 single source of truth。
> 关联 #1（MVP / §12 前序项目 回归基准）· #4（AgentProvider 缝）· #3（transform 声明化）。

## 目标

为跑**真实质量基准**（#1 设计稿 §12：用真模型验证 Digest/Compose 输出是否站得住）铺路。两件事：让 LLM **只走 subscription**（claude-code）、修通真实 provider 取产物。**不支持直连 API 模式**（删除 `ClaudeProvider`）。

- 幂等（不重复跑）已是 reconcile 既有保证（#4 §5：`is_stale`/`discover` 锚 `input_hash`），**非本文范围**。
- **dry-run 经评估非必须**，移出本次范围：幂等已防重复跑、订阅非按次付费、初期基准规模可预期（Digest×N + Compose×2）。真有「批量预估」需求时再开（见非范围）。

## 组件 1：provider 从 stdout 取产物

**问题**：`claude -p --output-format json` 把回答写到 **stdout 的 json**（`result` 字段），`codex exec` 写 stdout/last-message——都**不写文件**。当前 `ClaudeCodeProvider.run` / `CodexProvider.run` 只 `_scan_artifacts(artifact_dir)`，真实跑时产物丢失，`rules` 读 `config.artifact` 报 `FileNotFoundError`。这条路径从未真跑过。

**方案**：
- runner 签名加 `stdout_file` 参数：default runner 把子进程 stdout 重定向到该文件。
- `ClaudeCodeProvider.run`：runner 写 `_claude_stdout.json`（内部文件）→ 读取、解析 `result` → 写到 `config.artifact`（缺省 `output.md`）→ `_scan_artifacts`。`result` 也填入 `AgentResult.result_text`。
- `CodexProvider.run`：用 `--output-last-message _codex_last.txt` → 读该文件 → 写 `config.artifact`。
- runner 仍可注入；测试用 fake runner 模拟写 `stdout_file`，零真实 CLI 调用。
- 解析失败 / 文件缺失：抛清晰错误（产物缺失是真问题，不该静默覆盖成空产物）。

## 组件 2：只走 subscription，删除 API 模式

kairo 真实 LLM **只走 subscription（claude-code）**，不支持直连 API。

- **删除 `ClaudeProvider`（直连 SDK）类**及其测试；从 `_BACKENDS` 去掉 `"claude"`；删 `ANTHROPIC_API_KEY` 相关逻辑。
- **删 `anthropic` 依赖**（`pyproject.toml`）——删 `ClaudeProvider` 后已无其他引用。
- `select_provider` 简化为：`KAIRO_STUB` > `KAIRO_PROVIDER`（`stub` / `claude-code` / `codex`）> **auto**。auto：`claude` CLI 可用（`claude --version` exit 0）→ `ClaudeCodeProvider`；否则 `StubProvider`。
- 新增探活 `_cli_available(cmd)`：`subprocess` 跑 `<cmd> --version`，异常 / 非 0 → False。

## 测试策略（全程 TDD）

- **组件 1**：`ClaudeCodeProvider` / `CodexProvider` 从 `stdout_file` 取 `result`/last-message 写 `config.artifact`（fake runner 写 json/txt）；更新现有两个 CLI provider 测试。
- **组件 2**：`select_provider` auto（注入 `_cli_available`）：claude 在 → claude-code、claude 缺 → stub；`KAIRO_STUB` / 显式 `KAIRO_PROVIDER` 仍最高优先；删除 `ClaudeProvider` / `ANTHROPIC_API_KEY` 相关测试。
- 幂等既有测试不动。

## 非范围

dry-run（批量预估额度，YAGNI——幂等已防重复跑、订阅非按次付费）· 真实 ASR 后端（P4，audio→no-asr 不变）· assessment 多 provider 共识 · 并发。
