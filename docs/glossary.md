# 名词表

| 规范名 | 一句话定义 | 禁止别称 |
|---|---|---|
| workspace | 一个 topic 的自包含目录，内含 constitution、参考与产物。 | 课题仓、空间 |
| constitution | workspace 里声明期望状态的 `constitution.yaml`，step 朝它调和。 | 宪法文件 |
| 真名册 | 按层登记的领域规范名、说明与别名表，是后续文本产物的权威输入。 | — |
| 生效真名册 | 同一 workspace 上 root 与 workspace 两层按覆盖规则合并后的最终条目集。 | 合并表 |
| 覆盖 | workspace 同名条目整体替换对应 root 条目，不做字段级合并。 | 字段级继承 |
| 候选 | Digest 成功后提出、尚未进入权威真名册的建议条目，必须带可打开证据。 | 自动词条 |
| 待审核 | workspace 内尚未终态的候选。 | 待办词 |
| 知识条目 | 可审核、带范围和状态的领域知识最小对象，兼容并取代真名册作为唯一结构化事实源。 | 知识卡片、实体 |
| 知识候选 | 来自单份 digest 或跨材料 compose、尚未被人工采纳为知识条目的建议。 | 自动知识、知识草稿 |
| 目击 | 专名已在单篇 digest 出现、尚未达到待审出处门槛的候选状态。 | sighted |
| 知识出处 | 知识条目或候选单向指向可定位材料产物的轻量引用，材料不反向维护它。 | 证据对象、双向引用 |
| 知识匹配器 | 将已确认知识条目按确定性规则匹配到本次文本、并受歧义与预算约束的可替换能力。 | 术语检索器、RAG |
| stream | 观测型参考，digest 后 fold 进产物。 | 流水、观测流 |
| corpus | 基线型参考，只读参考层，不 digest、不 fold。 | 语料库 |
| digest | 一条 reference 的高密度记忆纪要，compose / 时段回顾的输入。 | 摘要、纪要原文 |
| kind | constitution 建仓时选用的填法名；运行时读 digest.enabled / targets / review_input。 | 工作区类型枚举 |
| journal | kind 预设：空 targets、开 digest（不计回顾自身 source_text）、关 compose、材料不进时段回顾原料。现网「总结」仓。 | 回顾仓、总结仓 |
| fold | 把 stream digest 调和进 constitution 声明的活 target（默认 `understanding.md`）。 | 融合、合并文档 |
| 回顾折入 | 把一条回顾上后附材料的 digest 写进该条回顾正文。 | — |
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
| remote | 在本机配置、通过 SSH 到达且受信任的 Kairo 备份目标。 | 远端环境、备份服务器 |
| 路径指针 | manifest form 中指向 workspace 外部源文件或目录、尚未物化进 workspace 的 location。 | 外链、外部引用 |
| 恢复闭包 | 恢复 serve root 所需的全部目录内数据及 manifest 登记的路径指针材料。 | 备份范围、恢复集 |
| 备份 generation | remote 上一份不可变、完整且校验通过的恢复版本。 | 备份批次、快照 |
| current | remote 上唯一指向当前可消费备份 generation 的原子指针。 | 当前版本、线上版本 |
| 备份清单 | `backup.json` 中完整描述一份备份 generation 的目录、文件、物化对象及其完整性信息。 | 文件清单 |
| 最近结果 | 源环境为某个 remote 保留的最近一次备份尝试记录，含尝试时间、成功时间、backup_id 与成败。 | 同步日志 |
| 重叠跳过 | 同一 remote 上一轮仍在运行时，本轮不启动第二次备份并留下可判定记录。 | 取消 |
| 数据根 | 容器内 public-read 进程打开的 serve root，必须经 current 跟随到该 generation 的 `data/`。 | 挂载点、备份根 |
| Project | serve root 上的业务聚合对象，多对多关联 workspace，不拥有其内容。 | 课题、工作区组 |
| Settings | 本机全局控制台，存放规则、默认值与连接健康，不存放单一 Project 的业务内容。 | 项目设置、系统偏好 |
| 连接 | Settings 中一条外部授权记录；凭据只在本机，Project 只引用其 id。 | 账号、token 对象 |
| Data Source | Project 内对外部资料的配置：连接引用、链接、用途与 Reader；平台由 URL 推断。 | 数据连接、表格源 |
| Reader | 按平台读取 Data Source 的可替换能力（腾讯文档、企微、Notion）；不是 spreadsheet 类别。 | 爬虫、导入器、文件类型 |
| Task | Project 内可编辑的工作定义，一次性或按规则触发。 | 作业、流水线 |
| Run | 一次 Task 触发的不可变记录，冻结当时 Task 版本与输入。 | 执行、job |
| Artifact | 成功 Run 产生的可阅读正文，追溯到该 Run、当时 Task 版本与输入 Data Source。 | 报告、产物文档 |
