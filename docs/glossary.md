# 名词表

| 规范名 | 一句话定义 | 禁止别称 |
|---|---|---|
| workspace | 一个 topic 的自包含目录，内含 constitution、参考与产物。 | 课题仓、空间 |
| constitution | workspace 里声明期望状态的 `constitution.yaml`，step 朝它调和。 | 宪法文件 |
| 真名册 | 按层登记的领域规范名、说明与别名表，是后续文本产物的权威输入。 | — |
| 生效真名册 | 同一 workspace 上 root 与 workspace 两层按覆盖规则合并后的最终条目集。 | 合并表 |
| 覆盖 | workspace 同名条目整体替换对应 root 条目，不做字段级合并。 | 字段级继承 |
| stream | 观测型参考，digest 后 fold 进产物。 | 流水、观测流 |
| corpus | 基线型参考，只读参考层，不 digest、不 fold。 | 语料库 |
| digest | 一条 reference 的高密度记忆纪要，compose / 时段回顾的输入。 | 摘要、纪要原文 |
| fold | 把 stream digest 调和进 constitution 声明的活 target（默认 `understanding.md`）。 | 融合、合并文档 |
| 活 target | constitution 中运行时参与 fold 的 target；当前排除判断层 target。 | 活文档、活动目标 |
| 材料目录 | Digest/Compose 写入 prompt 的表：标记（必读/按需）、角色、来源、路径、体量；不含正文。 | 文件清单、prompt 目录 |
| 听读 | Web 上音频与带时间轴誊录联动播放、高亮、跳转的界面。 | 听写、播放器页 |
| timed unit | 听读消费侧一条带起点（及可选终点）的誊录片段，通常对应一条 ASR cue。 | 字幕条、句子 |
| 语者标签 | 写在 cue 行上的说话人标识，机器默认为 `SPEAKER_N`。 | 说话人 ID、spk |
| 轮次 | 听读/誊录展示层把连续同一语者标签的 timed unit 折成的一条可跳播发言。 | 气泡、发言块 |
| transcript | 机器或人提供的誊录正文，是 digest 的信息上界；展示层折叠不改这份文件。 | 转写稿、SRT 原文 |
| 人话进度 | 运行中默认可见的一行状态：当前步骤或对象（能判则判）加上已运行时长；不是 agent 原文，也不是任务终态。 | 进度条、ETA、控制台状态、Running… |
| 运行健康 | 本次 Web 任务会话内的传输稳定性提示；出现指定传输类事件且进程未退出时，明示不稳但仍在跑。 | 失败、Run failed、provider-failed、任务终态 |
| 原始运行日志 | 子进程合并 stdout/stderr 的按行原文，默认折叠，展开后才可见。 | 进度面、状态区、运行摘要 |
