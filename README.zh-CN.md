# kairo

[English](README.md) | 简体中文

> step 驱动的增量知识构建引擎 —— 丢一个 reference，step 一下，知识往前长一格。

把「录音 → 转写 → 纪要 → 理解/判断」这条手工活，变成 `step` 驱动的增量知识构建引擎。它秉持工程纪律（可追溯、派生物可重生），是**编排 LLM** 的增量构建系统。

## 核心心智

一次 `kairo step` 把骨牌倒到底：`add` 一条 reference → ASR/doc2text → Digest（高密度记忆纪要 = 这条 reference 的记忆）→ Compose（增量综合进 `understanding.md`）。像 `make`：不执行命令，而是朝宪法声明的状态**调和**，跑到收敛。

> **可读全文 prose（可选，[#33](https://github.com/xforce-io/kairo/issues/33) / [#60](https://github.com/xforce-io/kairo/issues/60)）**：raw ASR 噪声大（无标点、口语化、同音错字），不便人通读。可旁挂规范化可读全文 `prose.md` 作**人读档案**——补标点、分段、纠错、合并口水。关键是它**只给人读、不进 digest 路径**：digest 恒从 raw `transcript` 派生（信息上界）。默认**关**；`constitution.yaml` 设 `pipeline.normalize.enabled: true` 可在 `step` 时批量生成，或在 Web 对单条参考点「生成可读文稿」/ CLI `kairo prose <ref_id>` 按需生成。只对机器派生的誊录（`origin≠added`）生效，人给文本与 corpus 不碰。

## 安装

```bash
# 全局 CLI（需要 uv，Python ≥ 3.11）
uv tool install git+https://github.com/xforce-io/kairo.git
# Web Console extra:
# uv tool install 'git+https://github.com/xforce-io/kairo.git[web]'

kairo doctor     # PATH / provider / ASR / skill
kairo connect    # 把 operator skill 挂到本机 Claude / Cursor / Codex / Pi
```

开发者在 checkout 里：`uv tool install .` 或 `uv run kairo --help`。

音频转写依赖本机 whisper，见下方「本机 ASR 配置」。

## 快速上手

```bash
kairo init "我的调研主题"            # 当前目录初始化为 Topic + 默认宪法
kairo add 录音.m4a                  # 登记路径指针（默认 stream/观测）
kairo add 录音.m4a --copy           # 先复制进 Topic 再登记（源删除仍可用）
kairo add ./会议夹                  # 目录→一条多形态参考(夹内音频/文档/图)
kairo add 调研报告.docx             # 二进制源(docx/pptx/xlsx/pdf)自动转 source_text
kairo add 白皮书.md --corpus        # 登记为 corpus/基线（权威参考资料）
kairo step                         # 调和到收敛:ASR/doc2text → Digest → Compose(开启 normalize 时旁挂 prose)
kairo status                       # 看各 reference / 文档的融入状态

# 全局 Ref 注册到 Topic（须先创建 Tag）
kairo tag create alpha              # 创建 Tag（与 Topic name 一致）
kairo add 会议录音.m4a --topic alpha --copy  # 注册为全局 Ref 并加入 Topic
cd alpha && kairo step              # 在 Topic 目录内 step

# 附加 form 到现有 Ref
kairo add 截图.png --to <ref_id> --copy    # 向既有 Ref 追加形态
```

产出 `understanding.md`（中立事实）。旧 workspace 里的 `assessment.md` 若仍在磁盘上则停更，不再 fold。

## 命令

| 命令 | 作用 |
| --- | --- |
| `init` | 初始化**当前目录**为 Topic + 默认宪法 |
| `list` | 列出 serve root 下各 Topic 摘要（`--json`；root 默认 `KAIRO_SERVE_ROOT` 或 cwd）[#95](https://github.com/xforce-io/kairo/issues/95) |
| `new` | 在 serve root 下新建 Topic 目录并 init（对标 Web 新建；须先创建同名 Tag） |
| `rm` | 删除 serve root 下某个 Topic（`--yes` 跳过确认；不碰 root glossary） |
| `add` | 登记 Ref：新 Ref（可选 `--topic <slug>` 加入 Topic）或 `--to <id>` 追加 form（默认路径指针；`--copy` 物化；`--corpus` 标基线；`--occurred YYYY-MM-DD` 钉发生日） |
| `tag create` | 创建 Tag（新建 Topic 前须先创建同名 Tag） |
| `tag add` | 为 Ref 添加 Tag（Topic 通过 include_tags 包含 Ref） |
| `include set` | 设置 Topic 的包含规则（命中任一 Tag 即成为成员） |
| `title` | 重命名参考展示名（不动 id / 目录） |
| `occurred` | 修正或清空参考发生时间（`--clear`；不改 id、不 step） |
| `timeline` | 跨 Topic 按发生日列出观测（`--day` / `--recent` / `--json`） |
| `step` | 跑调和循环到收敛（在 Topic 目录内；自动选择可读取材料的 provider，顺序见下文） |
| `run` | 有终态 blocked 则先清再 step（与 Web 主按钮一致） |
| `re-step` | 强制重算（文档级=整篇重综合，丢手改） |
| `retry-ref` | 单条参考清派生产物后重跑 |
| `rm-ref` | 永久删除一条参考 |
| `prose` | 为单条参考生成可读文稿 `prose.md` |
| `accept` | 接受手改、钉为新基线，解除 `blocked: manual-edit` |
| `status` | 列 references / 各文档融入状态 |
| `knowledge` | 知识 `list` / `add` / `rm`（`--scope workspace\|global`） |
| `glossary` | 兼容别名，等同 `knowledge` |
| `index` | 重生成 `references/MEETINGS.md` 导航索引 |
| `history` | 列版本快照 |
| `rollback` | 回退文档到某版本 |
| `diff` | 工作态 vs 版本文档差异（自带，不依赖 git） |
| `serve` | 启动本地 Web Console |
| `doctor` | 本机体检：provider / ASR / skill 挂载 |
| `connect` | 把 operator skill 挂到本机 coding agent |

## 核心概念

- **constitution.yaml**：本 Topic 的宪法——心智与协议（stream/corpus、fold、扩展名→role、转换声明）都在此声明，引擎不硬编码。
- **stream（观测）/ corpus（基线）**：reference 的认识论归类。stream 逐条 fold 进 `understanding.md`；corpus 作 agent 只读参考层，不 digest、不进 fold 循环，与观测冲突时以基线校正专名/术语。
- **综合产出**：`understanding.md`（事实层，中立、可标来源），完整文件不超过 20,000 Unicode 字符。Digest / Compose 用材料目录授读，不把原文倾倒进 prompt；超长旧文档先阻塞，确认“全量重综合会压缩历史正文，失败保留旧版”后用 `kairo re-step understanding.md` 迁移。
- **收敛**：`step` 像 `make`——朝宪法声明的状态调和，按内容 hash 判定 stale，跑到没有新推进为止。
- **二进制摄入**（[#15](https://github.com/xforce-io/kairo/issues/15)）：`add 文件.docx`（docx/pptx/xlsx/pdf）经 `doc2text`（[markitdown](https://github.com/microsoft/markitdown) 进程内转换）产 `source_text`，与 ASR 同构（`audio→transcript` ↔ `binary→source_text`），下游零改动；xlsx 转 GFM 表格保表头语义。无需机器配置（markitdown 是项目依赖）。仅 stream 型处理；corpus 二进制不转（基线只读直读，不派生）。
- **blocked 状态**：源/转换原因（`no-asr`、`asr-failed`、`convert-failed`、`missing-source`）、`manual-edit`、`provider-failed`，Compose 保护（`compose-degraded`、`compose-provenance-invalid`、`compose-migration-required`、`compose-over-budget`），以及 digest 骤缩护栏（`digest-degraded`）。`provider-failed` 可由 Run 重试；Compose 与 `digest-degraded` 是终态，旧文档或旧 digest 保持不变。普通 Run 不会清掉 `digest-degraded` 的参考。预算原因需在确认压缩取舍后显式执行 `kairo re-step understanding.md`。`understanding.md` 已超过 20,000 字符时，leftover `compose-degraded` 按 `compose-migration-required` 观察与恢复。

## 领域知识（knowledge）与兼容真名册（glossary）

`constitution.yaml: knowledge` 是工作区唯一的 v2 知识权威，根目录 `glossary.yaml` 保留为 global 知识的兼容文件路径。知识条目保存稳定 `ke-*` id、规范标题、别名（含 `auto_match`）、简短说明、状态、范围、标签、可选单向出处与带时区的审计时间。只有已确认条目按当前材料精确命中后，才会以受预算限制的参考上下文进入 Normalize、Digest 或 Compose；它不会替代材料证据。

`glossary` CLI/旧路由仍是兼容投影，`knowledge` 是等价入口。纯读不会迁移或写盘；显式写入/迁移会原子转换旧 v1 数据并移除第二权威。候选先审核为本地条目；已确认的本地条目可提交 global 审核，接受或合并后保留同一 `ke-*` id 与全部出处，并移除本地独立权威。

```yaml
knowledge:
  version: 2
  entries:
    - id: ke-example
      title: 灵犀系统
      description: 本项目所研究的系统
      aliases: [{value: 灵西, auto_match: true}]
      status: confirmed
      scope: workspace
      created_at: "2026-08-29T00:00:00+00:00"
      updated_at: "2026-08-29T00:00:00+00:00"
```

## 旧领域真名册（glossary）

`constitution.yaml` 可声明一张 `glossary`，把本领域的规范专名钉死。它在每次 Digest / Compose（及开启的 Normalize）时作为结构化只读数据注入（Issue [#20](https://github.com/xforce-io/kairo/issues/20)）：仅当提及能由规范名、alias 或定义充分对应时才用规范名，否则保留原文。每条三个键：`name`（规范名，作锚点）、`note`（给模型的 grounding，可选）、`aka`（已知变体 / 别名，纯参考，可选）。

```yaml
glossary:
- name: 灵犀系统            # 规范名(示例),各环节统一用它
  note: 本项目所研究的系统    # grounding,可选
  aka: [灵西, 凌犀, 灵息]    # 已知误识别/同音变体,可选
- name: 星图平台
  note: 平台名（与 corpus 基线一致）
```

注：纠正发生在**规范化 / 纪要 / 综合阶段**，ASR 转写本身不受影响（whisper 仍按音产出）。空表（`glossary: []`，默认）时零行为变化；对已生成的 reference 改 glossary 后，需 `kairo re-step <id>` 重产 digest 才会重新校正。公共条目在 `<serve-root>/glossary.yaml`，从 Root 首页维护；workspace 可对同名做本地覆盖。本机 `~/.config/kairo/glossary.yaml` 不再进入生效真名册（[#163](https://github.com/xforce-io/kairo/issues/163)）。

## 本机 ASR 配置

音频转写命令是**机器相关**的，不写进会被共享的 `constitution.yaml`（它只声明 `backend: whisper`）。在本机配一次即可，之后任何 workspace `kairo add 音频 && kairo step` 自动转写（Issue [#26](https://github.com/xforce-io/kairo/issues/26)）。

`~/.config/kairo/config.toml`，按 transform 的 `backend` 名分节（`[asr.<backend>]`）：

```toml
[asr.whisper]
cmd = "mlx_whisper {input} --model mlx-community/whisper-large-v3-turbo --language zh -f srt -o {outdir} --output-name {stem}"
origin = "whisper:large-v3-turbo"
```

听读联动需要 SRT 时间轴；旧 `-f txt` 配置仍可转写，但只显示正文、不随播放高亮。改为 `-f srt` 后，可用 `kairo re-step <reference-id>` 重产既有转写。

`kairo step` 按 `constitution.yaml` 里 transform 的 `backend`（默认 `whisper`）查对应节——故一台机器可并存多种后端（`[asr.whisper]`、`[asr.xxx]`），按 workspace 声明的 backend 路由。占位符：`{input}` 音频路径、`{outdir}` 临时输出目录、`{stem}` 输出名、`{output}`=`{outdir}/{stem}.txt`。模板含任一输出占位 → kairo 从产物文件读转写；否则捕获 stdout。环境变量 `KAIRO_ASR_CMD`（及 `KAIRO_ASR_ORIGIN`）全局覆盖。命令失败 → `blocked: asr-failed`（绝不写假转写）；无对应配置 → `blocked: no-asr`。

## 本机 LLM endpoint 配置

Kairo 可以把本机配置的 OpenAI-compatible Chat Completions endpoint 作为默认真实 provider。该配置不写进 `constitution.yaml`；凭据只从环境变量读取。

`~/.config/kairo/config.toml`：

```toml
[provider.openai]
base_url_env = "OPENAI_API_BASE"
model_env = "OPENAI_MODEL"
api_key_env = "OPENAI_API_KEY"
```

Provider 选择顺序：`KAIRO_STUB` → 显式 `KAIRO_PROVIDER` → auto 候选 `codex` CLI → `grok` CLI → `claude` CLI → 已配置 `[provider.openai]` → stub。需要读取材料的命令会跳过不支持授读的候选，所以有效 auto 顺序为 Codex → Claude → stub；其它命令保留完整偏好顺序。选中的 provider 失败后不跨 provider 重试。可用 `KAIRO_PROVIDER=openai` / `claude-code` / `grok` / `codex` 强制指定（见 [#61](https://github.com/xforce-io/kairo/issues/61) / [#153](https://github.com/xforce-io/kairo/issues/153) / [#160](https://github.com/xforce-io/kairo/issues/160)）。

## 技术栈

Python + uv；`AgentProvider` 缝（`run(config)→artifacts`，backend：stub / grok / openai-compatible / claude-code / codex），无 audit。详见 Issue [#4](https://github.com/xforce-io/kairo/issues/4)、[#54](https://github.com/xforce-io/kairo/issues/54) 与 [#61](https://github.com/xforce-io/kairo/issues/61)。

## Web Console（可选）

    uv tool install 'git+https://github.com/xforce-io/kairo.git[web]'
    kairo serve <包含多个 Topic 的根目录> [--port 8787]

浏览器（默认 `http://127.0.0.1:8787`，仅本机）统管 root 下的多个 Topic：

- **总览（dashboard）**：列出各 Topic（观测/基线计数、待 step / blocked 状态）；支持**单字段新建 Topic**——填 topic 即在 root 下建目录并 `init`。
- **详情页**：左栏分 `产物 / 参考(观测) / 基线`；选中条目 → 右栏常驻元信息（各形态可选预览、一键复制路径），中间为预览画布。transcript / digest 等形态即点即看（含 workspace 外的 `.txt` 转写，`.md` 渲染、纯文本保留换行），顶部可返回总览。
- **运行**：界面触发 `step`，实时看进度日志。

## Agent skill（可选）

operator skill 真身在 [`skills/kairo/SKILL.md`](skills/kairo/SKILL.md)（与 wheel 内同一份）。装好 CLI 之后：

```bash
kairo connect
# 或已会用 skills.sh：
npx skills add xforce-io/kairo --skill kairo -g
```

`kairo connect` 写入 `~/.agents/skills/kairo/`，并链到已探测的 Claude / Cursor / Codex / Pi。skill **默认只读**——纯「看状态 / 总结结论」意图不得触发 `step` / `re-step` / `accept` 等；raw transcript 不得当作最终结论。

## 设计与决策轨迹

可用的 CLI 工具（`init`/`add`/`step`/… 全部就绪，105+ 测试）。各特性的设计稿按 issue 编号存于 [`docs/design/`](docs/design)，是对应决策的 single source of truth：MVP [#1](https://github.com/xforce-io/kairo/issues/1)、AgentProvider [#4](https://github.com/xforce-io/kairo/issues/4)、源分层 [#13](https://github.com/xforce-io/kairo/issues/13)、Grok provider [#61](https://github.com/xforce-io/kairo/issues/61) 等。
