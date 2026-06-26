# kairo

[English](README.md) | 简体中文

> step 驱动的增量知识构建引擎 —— 丢一个 reference，step 一下，知识往前长一格。

把「录音 → 转写 → 纪要 → 理解/判断」这条手工活，变成 `step` 驱动的增量知识构建引擎。它秉持工程纪律（可追溯、派生物可重生），是**编排 LLM** 的增量构建系统。

## 核心心智

一次 `kairo step` 把骨牌倒到底：`add` 一条 reference → ASR/doc2text → Digest（忠实纪要 = 这条 reference 的记忆）→ Compose（增量综合进 `understanding.md` 事实层 / `assessment.md` 判断层）。像 `make`：不执行命令，而是朝宪法声明的状态**调和**，跑到收敛。

> **可读全文 prose（可选，[#33](https://github.com/xforce-io/kairo/issues/33)）**：raw ASR 噪声大（无标点、口语化、同音错字），不便人通读。开启 `normalize` 后，旁挂生成一份规范化的可读全文 `prose.md` 作**人读档案**——补标点、分段、纠错、合并口水。关键是它**只给人读、不进 digest 路径**：digest 恒从 raw `transcript` 派生（信息上界），所以 prose 怎么精简都不影响纪要质量、也不存在「二次有损」，无需任何护栏。默认**关**，在 `constitution.yaml` 设 `pipeline.normalize.enabled: true` 开启；只对机器派生的誊录（`origin≠added`）生成，人给的文本原文与 corpus 不碰。

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
kairo add 调研报告.docx       # 二进制源(docx/pptx/xlsx/pdf)自动转 source_text
kairo add 白皮书.md --corpus  # 登记为 corpus/基线（权威参考资料）
kairo step                    # 调和到收敛:ASR/doc2text → Digest → Compose(开启 normalize 时旁挂 prose)
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
- **二进制摄入**（[#15](https://github.com/xforce-io/kairo/issues/15)）：`add 文件.docx`（docx/pptx/xlsx/pdf）经 `doc2text`（[markitdown](https://github.com/microsoft/markitdown) 进程内转换）产 `source_text`，与 ASR 同构（`audio→transcript` ↔ `binary→source_text`），下游零改动；xlsx 转 GFM 表格保表头语义。无需机器配置（markitdown 是项目依赖）。仅 stream 型处理；corpus 二进制不转（基线只读直读，不派生）。
- **blocked 状态**：`no-asr`（本机未配 ASR 后端）/ `asr-failed`（转写命令失败）/ `convert-failed`（二进制转换失败/空产物）/ `missing-source`（源不可达）/ `manual-edit`（手改待 `accept`）/ `compose-degraded`（综合输出相对上一版骤缩，疑为退化输出，已拒绝覆盖以保护旧文档）。前置条件变化后下次 `step` 自动重试（如配好 ASR 后旧音频会被重转）；`asr-failed` / `convert-failed` / `compose-degraded` 视为终态，需手动 `re-step` 重算。

## 领域真名册（glossary）

`constitution.yaml` 可声明一张 `glossary`，把本领域的规范专名钉死。它在每次 Digest / Compose（及开启的 Normalize）时注入 agent 提示词（Issue [#20](https://github.com/xforce-io/kairo/issues/20)），用于纠正口语 / 转写产生的同音变体与别名——产出时一律用规范名，遇含糊提及按此锚定。每条三个键：`name`（规范名，作锚点）、`note`（给模型的 grounding，可选）、`aka`（已知变体 / 别名，纯参考，可选）。

```yaml
glossary:
- name: 灵犀系统            # 规范名(示例),各环节统一用它
  note: 本项目所研究的系统    # grounding,可选
  aka: [灵西, 凌犀, 灵息]    # 已知误识别/同音变体,可选
- name: 星图平台
  note: 平台名（与 corpus 基线一致）
```

注：纠正发生在**规范化 / 纪要 / 综合阶段**，ASR 转写本身不受影响（whisper 仍按音产出）。空表（`glossary: []`，默认）时零行为变化；对已生成的 reference 改 glossary 后，需 `kairo re-step <id>` 重产 digest 才会重新校正。

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

## Web Console（可选）

    pip install 'kairo[web]'
    kairo serve <包含多个 workspace 的根目录> [--port 8000]

浏览器（默认 `http://127.0.0.1:8000`，仅本机）统管 root 下的多个 workspace：

- **总览（dashboard）**：列出各 workspace（观测/基线计数、待 step / blocked 状态）；支持**单字段新建 workspace**——填 topic 即在 root 下建目录并 `init`。
- **详情页**：左栏分 `产物 / 参考(观测) / 基线`；选中条目 → 右栏常驻元信息（各形态可选预览、一键复制路径），中间为预览画布。transcript / digest 等形态即点即看（含 workspace 外的 `.txt` 转写，`.md` 渲染、纯文本保留换行），顶部可返回总览。
- **运行**：界面触发 `step`，实时看进度日志。

## 设计与决策轨迹

可用的 CLI 工具（`init`/`add`/`step`/… 全部就绪，105+ 测试）。各特性的设计稿按 issue 编号存于 [`docs/design/`](docs/design)，是对应决策的 single source of truth：MVP [#1](https://github.com/xforce-io/kairo/issues/1)、AgentProvider [#4](https://github.com/xforce-io/kairo/issues/4)、源分层 [#13](https://github.com/xforce-io/kairo/issues/13) 等。
