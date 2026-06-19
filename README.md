# kairo

> step 驱动的增量知识构建引擎 —— 丢一个 reference，step 一下，知识往前长一格。

把「录音 → 转写 → 纪要 → 理解/判断」这条手工活，变成 `step` 驱动的增量知识构建引擎。它继承 toc 的纪律（可追溯、派生物可重生），但海拔相反——是**编排 LLM** 的增量构建系统。

## 核心心智

一次 `kairo step` 把骨牌倒到底：`add` 一条 reference → ASR → Digest（忠实纪要 = 这条 reference 的记忆）→ Compose（增量综合进 `understanding.md` 事实层 / `assessment.md` 判断层）。像 `make`：不执行命令，而是朝宪法声明的状态**调和**，跑到收敛。

## 状态

设计阶段。MVP 设计稿是 **single source of truth**：[docs/design/kairo-mvp.md](docs/design/kairo-mvp.md)。

## 技术栈

Python + uv；薄 `ModelProvider`（Claude `claude-opus-4-8` / stub），无 audit、无 agent loop。
