# kairo

> step 驱动的增量知识构建引擎 —— 丢一个 reference，step 一下，知识往前长一格。

把「录音 → 转写 → 纪要 → 理解/判断」这条手工活，变成 `step` 驱动的增量知识构建引擎。它继承 toc 的纪律（可追溯、派生物可重生），但海拔相反——是**编排 LLM** 的增量构建系统。

## 核心心智

一次 `kairo step` 把骨牌倒到底：`add` 一条 reference → ASR → Digest（忠实纪要 = 这条 reference 的记忆）→ Compose（增量综合进 `understanding.md` 事实层 / `assessment.md` 判断层）。像 `make`：不执行命令，而是朝宪法声明的状态**调和**，跑到收敛。

## 安装

```bash
# 全局安装控制台命令 kairo（需要 uv）
uv tool install .

# 或在仓库内开发态运行
uv run kairo --help
```

需要 Python ≥ 3.11。音频转写依赖本机 whisper，见下方「本机 ASR 配置」。

## 快速上手

```bash
kairo init "我的调研主题"      # 当前目录初始化为 topic-workspace + 默认宪法
kairo add 录音.m4a            # 登记一条 reference（默认 stream/观测）
kairo add 白皮书.md --corpus  # 登记为 corpus/基线（权威参考资料）
kairo step                    # 调和到收敛:ASR → Digest → Compose
kairo status                  # 看各 reference / 文档的融入状态
```

产出两层文档：`understanding.md`（中立事实）与 `assessment.md`（立场判断）。

## 命令

| 命令 | 作用 |
| --- | --- |
| `init` | 初始化 topic-workspace + 默认宪法 |
| `add` | 登记一条 reference 的所有形态（`--corpus` 标基线，默认 stream 观测） |
| `step` | 跑调和循环到收敛（有 key→Claude，否则 stub；`KAIRO_STUB` 强制 stub） |
| `re-step` | 强制重算（文档级=整篇重综合，丢手改） |
| `accept` | 接受手改、钉为新基线，解除 `blocked: manual-edit` |
| `status` | 列 references / 各文档融入状态 |
| `index` | 重生成 `references/MEETINGS.md` 导航索引 |
| `history` | 列版本快照 |
| `rollback` | 回退文档到某版本 |
| `diff` | 工作态 vs 版本文档差异（自带，不依赖 git） |

## 核心概念

- **constitution.yaml**：本 workspace 的宪法——心智与协议（两层产出、stream/corpus、fold、扩展名→role、转换声明）都在此声明，引擎不硬编码。
- **stream（观测）/ corpus（基线）**：reference 的认识论归类。stream 逐条 fold 进文档、判断随之演进、可推翻旧判断；corpus 作 agent 只读参考层，不 digest、不进 fold 循环，与观测冲突时以基线校正专名/术语。
- **两层产出**：`understanding.md`（事实层）与依赖它的 `assessment.md`（判断层）；中立事实与立场判断不混。
- **收敛**：`step` 像 `make`——朝宪法声明的状态调和，按内容 hash 判定 stale，跑到没有新推进为止。
- **blocked 状态**：`no-asr`（本机未配 ASR 后端）/ `asr-failed`（转写命令失败）/ `missing-source`（源不可达）/ `manual-edit`（手改待 `accept`）。前置条件变化后下次 `step` 自动重试（如配好 ASR 后旧音频会被重转）。

## 领域真名册（glossary）

`constitution.yaml` 可声明一张 `glossary`，把本领域的规范专名钉死。它在每次 Digest / Compose 时注入 agent 提示词（Issue [#20](https://github.com/xforce-io/kairo/issues/20)），用于纠正口语 / 转写产生的同音变体与别名——产出时一律用规范名，遇含糊提及按此锚定。每条三个键：`name`（规范名，作锚点）、`note`（给模型的 grounding，可选）、`aka`（已知变体 / 别名，纯参考，可选）。

```yaml
glossary:
- name: 企业微信            # 规范名,各环节统一用它
  note: 私域运营所用平台     # grounding,可选
  aka: [企微, 起微, 球艺]    # 已知误识别/同音变体,可选
- name: 康医通
  note: 系统名（与 corpus 基线一致）
```

注：纠正发生在**纪要 / 综合阶段**，ASR 转写本身不受影响（whisper 仍按音产出）。空表（`glossary: []`，默认）时零行为变化；对已生成的 reference 改 glossary 后，需 `kairo re-step <id>` 重产 digest 才会重新校正。

## 本机 ASR 配置

音频转写命令是**机器相关**的，不写进会被共享的 `constitution.yaml`（它只声明 `backend: whisper`）。在本机配一次即可，之后任何 workspace `kairo add 音频 && kairo step` 自动转写（Issue [#26](https://github.com/xforce-io/kairo/issues/26)）。

`~/.config/kairo/config.toml`，按 transform 的 `backend` 名分节（`[asr.<backend>]`）：

```toml
[asr.whisper]
cmd = "mlx_whisper {input} --model mlx-community/whisper-large-v3-turbo --language zh -f txt -o {outdir} --output-name {stem}"
origin = "whisper:large-v3-turbo"
```

`kairo step` 按 `constitution.yaml` 里 transform 的 `backend`（默认 `whisper`）查对应节——故一台机器可并存多种后端（`[asr.whisper]`、`[asr.xxx]`），按 workspace 声明的 backend 路由。占位符：`{input}` 音频路径、`{outdir}` 临时输出目录、`{stem}` 输出名、`{output}`=`{outdir}/{stem}.txt`。模板含任一输出占位 → kairo 从产物文件读转写；否则捕获 stdout。环境变量 `KAIRO_ASR_CMD`（及 `KAIRO_ASR_ORIGIN`）全局覆盖。命令失败 → `blocked: asr-failed`（绝不写假转写）；无对应配置 → `blocked: no-asr`。

## 技术栈

Python + uv；`AgentProvider` 缝（`run(config)→artifacts`，backend：stub / claude / claude-code / codex），无 audit。详见 Issue [#4](https://github.com/xforce-io/kairo/issues/4)。

## 设计与决策轨迹

可用的 CLI 工具（`init`/`add`/`step`/… 全部就绪，105+ 测试）。各特性的设计稿按 issue 编号存于 [`docs/design/`](docs/design)，是对应决策的 single source of truth：MVP [#1](https://github.com/xforce-io/kairo/issues/1)、AgentProvider [#4](https://github.com/xforce-io/kairo/issues/4)、源分层 [#13](https://github.com/xforce-io/kairo/issues/13) 等。
