# kairo

> step 驱动的增量知识构建引擎 —— 丢一个 reference，step 一下，知识往前长一格。

把「录音 → 转写 → 纪要 → 理解/判断」这条手工活，变成 `step` 驱动的增量知识构建引擎。它继承 toc 的纪律（可追溯、派生物可重生），但海拔相反——是**编排 LLM** 的增量构建系统。

## 核心心智

一次 `kairo step` 把骨牌倒到底：`add` 一条 reference → ASR → Digest（忠实纪要 = 这条 reference 的记忆）→ Compose（增量综合进 `understanding.md` 事实层 / `assessment.md` 判断层）。像 `make`：不执行命令，而是朝宪法声明的状态**调和**，跑到收敛。

## 状态

设计阶段。MVP 设计稿是 **single source of truth**：[docs/design/1-kairo-mvp.md](docs/design/1-kairo-mvp.md)（Issue [#1](https://github.com/xforce-io/kairo/issues/1)）。

## 本机 ASR 配置

音频转写命令是**机器相关**的，不写进会被共享的 `constitution.yaml`（它只声明 `backend: whisper`）。在本机配一次即可，之后任何 workspace `kairo add 音频 && kairo step` 自动转写（Issue [#26](https://github.com/xforce-io/kairo/issues/26)）。

`~/.config/kairo/config.toml`：

```toml
[asr]
cmd = "mlx_whisper {input} --model mlx-community/whisper-large-v3-turbo --language zh -f txt -o {outdir} --output-name {stem}"
origin = "whisper:large-v3-turbo"
```

占位符：`{input}` 音频路径、`{outdir}` 临时输出目录、`{stem}` 输出名、`{output}`=`{outdir}/{stem}.txt`。模板含任一输出占位 → kairo 从产物文件读转写；否则捕获 stdout。环境变量 `KAIRO_ASR_CMD`（及 `KAIRO_ASR_ORIGIN`）可临时覆盖。命令失败 → `blocked: asr-failed`（绝不写假转写）；未配置 → `blocked: no-asr`。

## 技术栈

Python + uv；`AgentProvider` 缝（`run(config)→artifacts`，backend：stub / claude / claude-code / codex），无 audit。详见 Issue [#4](https://github.com/xforce-io/kairo/issues/4)。
