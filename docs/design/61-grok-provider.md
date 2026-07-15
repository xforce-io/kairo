# 设计: Grok CLI provider（`grok -p`）

- Issue: [#61](https://github.com/xforce-io/kairo/issues/61)
- 分支: `feat/61-grok-provider`
- 关联: #4（AgentProvider 抽象）、#8（错误不写坏产物）、#13 / #44（`read_dirs` corpus / 附件）、#54（OpenAI-compatible endpoint）
- 状态: 已实现（待合入）

## 1. 目标

新增一条 CLI subscription backend：`GrokProvider`，驱动本机 `grok -p` headless 单轮，接入既有 `AgentProvider` 缝（`run(config) → artifacts`）。

让已登录 Grok 的本机用户能跑真实 agent，而不必依赖 Claude Code、Codex 或 OpenAI-compatible endpoint 配置。

**默认 backend**：auto 路径下，本机 `grok` CLI 可用时优先选 `GrokProvider`（见 §4.3）。

## 2. 现状

| Backend | `name` | 形态 |
|---|---|---|
| `StubProvider` | `stub` | 确定性 Fake |
| `OpenAICompatibleProvider` | `openai` | 薄 Chat Completions adapter |
| `ClaudeCodeProvider` | `claude-code` | `claude -p` CLI |
| `CodexProvider` | `codex` | `codex exec` CLI |

`select_provider` 顺序：`KAIRO_STUB` → 显式 `KAIRO_PROVIDER` → 已配置 openai endpoint → `claude` CLI 可用 → stub。

Grok CLI 本机可用，且 headless 接口与 `claude -p` 同构，但**尚未接入**。

## 3. 通路验证（本机，2026-07-15）

- CLI：`grok`，版本 `0.2.101`
- Headless：`-p, --single <PROMPT>` → 答到 stdout 后退出
- 工作目录：`--cwd <CWD>`（runner 亦可用 `cwd=`）
- 输出：`--output-format plain|json|streaming-json`
- 模型：`-m, --model <MODEL>`
- JSON **成功**关键字段：`text`（**不是** claude 的 `result`）
- JSON **错误**形状（无效 model 实测）：`{"type":"error","message":"..."}`，exit ≠ 0
- 实测 plain：`grok -p "Reply with exactly: PONG"` → stdout `PONG`，exit 0
- **无** Claude 式 `--add-dir`；`--allow` / `--tools` / `--sandbox` 存在，但路径级只读授权未验证

## 4. 方案

### 4.1 `GrokProvider`（对齐 `ClaudeCodeProvider`）

```
AgentConfig{persona, context, artifact_dir, model, artifact?, timeout_s?, read_dirs?}
        │
        ▼  GrokProvider.run
  1. mkdir artifact_dir
  2. prompt = persona + "\n\n---\n\n" + context
     写 _prompt.md（内部文件，不计 artifact）
  3. runner("grok", args, cwd=artifact_dir, input=..., stdout_file=_grok_stdout.json, timeout=...)
  4. 解析 stdout JSON → text
  5. 写 config.artifact or "output.md"
  6. return AgentResult(artifacts=_scan_artifacts(...), result_text=text)
```

**CLI 参数（MVP）**

| 参数 | 值 | 说明 |
|---|---|---|
| `-p` / `--single` | prompt 字符串 | headless 单轮；prompt 走 argv（CLI 要求 `<PROMPT>`），同时 runner 的 `input=` 可冗余传入以对齐现有 runner 签名 |
| `--output-format` | `json` | 解析 `text` / 错误 `type` |
| `-m` | `self.model`（非空时） | 空则省略，跟 CLI 默认 |
| cwd | `config.artifact_dir` | 与 claude-code / codex 一致，产物落 cwd |
| timeout | `config.timeout_s` | 透传 |

不强制 `--cwd` CLI 旗标（与现有 `_default_cli_runner` 的 `cwd=` 重复）；实现时以 runner `cwd=` 为准，保持与 `ClaudeCodeProvider` 一致。

**身份**

- `name = "grok"`
- `model`：构造参数，默认 `""`（空 = CLI 默认模型；provenance 如实记录空串或运行时可知的默认值——MVP 记构造时的 `self.model`）

**错误处理（#8）**

在写业务产物**之前**拦截：

1. `_grok_stdout.json` 不存在 → `RuntimeError`
2. JSON 解析失败 → `RuntimeError`
3. `data.get("type") == "error"` → `RuntimeError(message)`
4. `text` 缺失或非 `str` 或 strip 后为空 → `RuntimeError`

均不写 `config.artifact`。

**runner 可注入**：`GrokProvider(model="", runner=None)`，默认 `_default_cli_runner`，单测 fake。

### 4.2 `read_dirs`（开放问题拍板：MVP = 忽略 + 可观测）

Grok CLI 无 `--add-dir`。MVP **不**阻断 run：

- 有 `read_dirs` 时：**忽略路径授权**，不传额外 CLI 旗标
- 不预授工具（无 claude 式 `--allowedTools`）
- 在模块/类 docstring 与 README 写明：corpus 参考层 / 图片 attachment（#13 / #44）依赖 `read_dirs` 的场景，**请用 `claude-code`**；`grok` 仅保证「persona+context → 文本产物」路径

理由（YAGNI）：

- 路径级授权未验证，强做易假安全
- Compose 在无 corpus 时 `read_dirs` 为空，纯 stream digest 主路径不受影响
- 假实现（把路径塞进 prompt）对大文件/图片无效，不如诚实降级

后续若 CLI 出现明确只读目录 API，另开 issue 补齐，不堵本 issue。

### 4.3 选择与注册

```python
_BACKENDS = {
    "stub": StubProvider,
    "claude-code": ClaudeCodeProvider,
    "codex": CodexProvider,
    "grok": GrokProvider,  # 新增
}
```

`select_provider`（**grok 为 auto 默认**）：

1. `KAIRO_STUB` → stub（不变，测试隔离最高）
2. 显式 `KAIRO_PROVIDER`：
   - `grok` → `GrokProvider()`
   - 其余不变（含 `openai` 校验）
3. **auto**（按序，命中即返回）：
   1. `grok` CLI 可用（`_cli_available("grok")`）→ **`GrokProvider()`** ← 默认真实路径
   2. 已配置 openai endpoint → `OpenAICompatibleProvider`
   3. `claude` CLI 可用 → `ClaudeCodeProvider`
   4. 否则 → `StubProvider`

用法：

```bash
# 本机有 grok 时，直接 step 即走 GrokProvider（无需 env）
kairo step

# 仍可用显式 env 覆盖
KAIRO_PROVIDER=claude-code kairo step
KAIRO_PROVIDER=openai kairo step
KAIRO_STUB=1 kairo step
```

README 同步改写选择顺序说明。

### 4.4 非目标

- 不做 Grok HTTP / SDK adapter（endpoint 场景继续用 `OpenAICompatibleProvider` 或后续另议）
- 不新增 Python 依赖
- 不改 `AgentConfig` / `AgentResult` / reconcile / provenance 字段语义
- 不改 engine / rules 调用点（仍 `provider.run(AgentConfig(...))`）
- 不在 CI 跑真实 `grok`（依赖本机登录与网络）

## 5. 涉及模块

| 文件 | 改动 |
|---|---|
| `src/kairo/provider.py` | `GrokProvider`；`_BACKENDS` 注册；模块 docstring 列表更新 |
| `tests/test_agent_provider.py` | fake runner：参数、`text`→artifact、error/空 text/无 stdout、identity；`read_dirs` 不进 args |
| `README.md` / `README.zh-CN.md` | provider 列表 + `KAIRO_PROVIDER=grok` + `read_dirs` 限制一句 |

可选（非必须，有则更好）：

| 文件 | 改动 |
|---|---|
| `tests/test_provider.py` 或既有 select 测试 | 显式 `KAIRO_PROVIDER=grok`；**auto 在 grok CLI 可用时选 `GrokProvider`**（mock `_cli_available`）；openai/claude 回落顺序 |

## 6. 测试计划

| 层级 | 内容 | CI |
|---|---|---|
| **单元** | fake runner：CLI cmd/args 含 `-p`、`--output-format json`、非空 model 时 `-m`；stdout JSON `text` 落到 artifact；`type=error` / 缺 text / 无文件 → 抛错且不写产物；`name`/`model`；有 `read_dirs` 时 args **不含**伪造的 add-dir | 是 |
| **集成** | 注入 `GrokProvider(runner=fake)`（或等价）跑 `step`/单 rule，产物入账、provenance `provider=grok` | 是（若仓库已有同类 fake-provider step 测；否则 provider 单测 + 手工 E2E 足够） |
| **E2E（本机真实 CLI）** | 已登录 Grok：无 env 时 `kairo step`（或显式 `KAIRO_PROVIDER=grok`）最小 workspace 跑通一步，artifacts 有正文，history/provenance 记 `grok` | **否**（与 claude-code/codex 真实路径同级，不进默认 CI） |

与单元的区分：E2E 验的是「真实 CLI 登录 + 网络 + 写盘」用户路径，不 mock runner。

## 7. 验收标准

- [x] `GrokProvider.run` 满足既有 artifact 约定（`_`/`.` 前缀不计）
- [x] 错误路径不写业务产物（#8）
- [x] `KAIRO_PROVIDER=grok` 可选中；**auto 在 `grok` 可用时默认选 grok**，否则回落 openai → claude → stub
- [x] 单测覆盖 §6 单元项
- [x] README 中/英已更新
- [x] 现有 stub / openai / claude-code / codex 测试全绿（286 passed）
- [x] 本机真实 E2E：`KAIRO_PROVIDER=grok kairo step` 产出 digest/understanding，provenance `provider=grok`

## 8. 决策记录（相对 issue 开放问题）

| # | 问题 | 决策 |
|---|---|---|
| 1 | `read_dirs` | **忽略 + 文档说明**；不 fail run；corpus/图片场景指回 claude-code |
| 2 | auto 选择 | **`grok` CLI 可用 → 默认 `GrokProvider`**；其后 openai → claude → stub。显式 env / `KAIRO_STUB` 仍可覆盖 |
| 3 | 默认 model | **`""`，跟 CLI 默认**；非空才传 `-m` |

评审推翻以上任一项时，先改本文再实现。

## 9. 实现顺序（通过评审后）

1. 单测（红）→ `GrokProvider` + 注册（绿）
2. `select_provider` 默认优先 grok + 单测 mock `_cli_available`
3. README 中/英选择顺序
4. 本机真实（无 env）`kairo step` 烟测，确认 provenance=`grok`
5. PR 链回 #61 与本文
