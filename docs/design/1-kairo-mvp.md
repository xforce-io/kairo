# kairo MVP 设计稿（workspace = 一个 topic）

> 状态:**MVP 设计基本收敛**。2026-06-19 一轮 review 敲定:Compose 常态(B 批量 Δ)、漂移/抖动治理、指纹拆 model、target 间依赖、手改处理、版本/rollback、MVP 四段切分。详见 §2 决策表(★ = 本轮新增/修订)。剩余小项见 §13。
> 来源:[../brainstorm/2026-06-18-kairo-concept.md](../brainstorm/2026-06-18-kairo-concept.md)(发散稿)+ 后续 review;并以 `~/lab/tiansu/toc/` 的 `understanding.md` / `assessment.md`(三场会议增量综合而成)为真实参照。
> 范围:**一个 workspace = 一个 topic**,MVP 跑通它。多 topic = 多 workspace(P2)。分层归并 / 异构 loader 见分期。
> Issue:[#1](https://github.com/xforce-io/kairo/issues/1)。本文件是 **single source of truth**;issue 只放一行摘要 + 指回本文件的链接。

---

## 1. 一句话与赌注

把「录音 → 转写 → 纪要 → 理解/判断」这条手工活,变成 **`step` 驱动的增量知识构建引擎**:丢一个 reference,`step` 一下,知识往前长一格。它继承 toc 的纪律(可追溯、派生物可重生),但海拔相反——它是**编排 LLM** 的增量构建系统。

---

## 2. 已拍板的决策

| # | 决策 | 结论 |
|---|---|---|
| D-step | `step` 的定位 | **最外围的薄驱动壳**,零业务知识;扫规则→跑过期的→**跑到收敛**(一次 `step` 把骨牌倒到底) |
| D-topic | workspace 与 topic | **一一对应**:一个 workspace 就是一个 topic。**多 topic = 多 workspace** |
| D-belong | reference 归属 | reference **归属其被 add 的 topic**(住在该 workspace 的 `references/`);内容 topic 无关,故可被别的 topic 复用(扇出=跨 topic 引用,P2) |
| D-seg | 三段分界 | **归属**(topic 绑定)/ **生成**(transcript·digest,topic 无关、统一)/ **折叠**(进文档,topic 相关) |
| D2 | LLM 可审计 | **MVP 不做审计**;只在 provider 留薄缝,不为审计做额外抽象 |
| **D3 ★** | 幂等/重跑 | 按**输入指纹** hash 推进,产物 pin 住;`re-step` 强制重算。**指纹 = 输入内容 + 治理配置(prompt/fold_protocol/target)**;**`model`/`provider` 版本拆为溯源字段 `produced_by`,不进指纹、不触发重算**——换模型重产靠显式 `re-step`(见 §3 修订:回应 review,把"严格 pin"从抖动源里摘出去) |
| **D-repro ★** | 可重生边界 | **可逐字重生保证只锚到 digest 层**(忠实、低温、近确定);`understanding`/`assessment` 是 **living artifact**,称「可重新综合」而非「可逐字重生」 |
| D-status ★ | 节点状态 | `step` 对每个产物报 `ok / blocked(reason)`,**不伪造、不静默**;reason ∈ `missing-source` / `no-asr` / **`manual-edit`** |
| D-source | 源文件丢失 | 默认指针;已存在产物在源丢失时**存活**(下游不受影响),仅**需重新派生时**遇源丢失 → `blocked: missing-source`;opt-in `add --copy` 入库求完全可复现 |
| **D4 ★** | 文档生成 | 文档=综合派生物;**常态 B-增量综合修订(批量 Δ:一次 step 把本文档本步全部新 digest 用一次 op 融入,允许重组/推翻、非追加)**;**全量重综合 A 为兜底**(`re-step` / 配置变);**漂移校正 MVP 靠手动 `re-step`**,自动阈值留 P |
| **D-target ★** | target 间依赖 | **`assessment` `depends_on` `understanding`**;step 内拓扑序(understanding→assessment);assessment 输入含当前 understanding;understanding 变 → **级联** assessment 重综合 |
| D-layer | 事实/判断分层 | MVP **同时产** `understanding.md`(事实、中立、⚠️ 标未确认)+ `assessment.md`(判断、带立场、会演进),互相 link;依赖关系见 D-target |
| **D-version ★** | 版本 / rollback | 自建 `.kairo/history`,版本单位=**整个 workspace、step 粒度**;每次 step 收敛后快照 `{综合文档 + state.targets 段}`;**只回退 文档 + targets 段,不动 `references/` 与 state.products 段**;rollback 后下次 step 重新融入更晚 digest;diff/抖动护栏用**自带 `kairo diff`(不依赖 git)**;MVP 快照全留 |
| **D-phase ★** | MVP 切分 | 四段递进 **M0**(全 stub 走路骨架)→ **M1**(接真 Claude)→ **M2**(第二层+依赖)→ **M3**(治理闭合);见 §12 |
| **D-prompt ★** | digest prompt 归属 | **per-topic 宪法可定制 + 引擎内置默认**(缺省回落);**不**移成全局规则。代价:跨 topic 复用须 prompt 匹配(P2 扇出) |
| **D-id ★** | add 派生 id 日期 | **取「添加当天」**(运行 `add` 的日期),简单稳定;补录场景用 `--id` 覆盖 |
| 记忆粒度 | 一条记忆 = ? | **一个 reference**;机制上 **digest 即记忆**,无独立 memory 产物 |
| 形态 | 一资源多形态 | reference 可有多个 form;modality 在 manifest 声明 |
| 原始文件 | 是否入库 | **不拷贝**;manifest 存指针(location)+ hash |
| 技术栈 | 语言/运行时 | **Python + uv** |
| Provider | 模型抽象 | 薄 `ModelProvider`(Claude `claude-opus-4-8` / stub),**无 audit、无 agent loop** |

---

## 3. 核心模型:跑在依赖 DAG 上的调和循环

```
step():
  repeat:
    progressed = False
    for rule in rules:                 # 规则 = 声明层,step 不懂它们干啥
      for item in rule.discover(ws):   # 每条规则自己扫出"待办"
        if (产物缺失) or (输入hash变了) or (被 re-step 强制):
          产物 = item.produce()        # 可能调 LLM
          写盘; 更新 state
          progressed = True
    until not progressed               # 收敛即停
  snapshot(.kairo/history/<seq+1>)     # 收敛后快照一个版本(D-version)
```

- 一次 `kairo step` 把骨牌**倒到底**:ASR→transcript、Digest→digest、Compose→综合进文档,全在一条 step 内。(像 `make`:不会为一个目标手动跑三次。)
- `step` 是**油门**,「做什么」全在规则/宪法里。新增能力 = 加规则,不改 `step`。
- **收敛是结构性保证,不是靠迭代上限兜底**:progress 锚在离散项——「产物缺失 / 输入指纹变」和「某文档未融入的 digest(Δ)」;每个 (文档 × 未融入 digest) 只融一次即记账、不再触发 → 必然终止。迭代上限只是失控 backstop。
- **target 间有依赖时按拓扑序推进**(understanding → assessment,见 D-target):同一 step 内先让上游文档收敛,下游才用其最新结果综合。
- (可选,留待 §13)`--dry-run` 提供"综合不写盘、只看将产生的 diff"的预演关卡。

---

## 4. 数据布局(workspace = 一个 topic)

```
<workspace>/                       # = 一个 topic
  constitution.yaml                # 本 topic:pipeline(生成) + targets(综合) + 维护指引
  references/                      # 本 topic 的引用库(归属此 topic)
    2026-06-18-meeting/
      manifest.yaml                # 指针:forms 在哪 + hash + 溯源
      transcript.md                # ASR 产物(若有 audio form)
      digest.md                    # 纪要 = 这条 reference 的记忆(topic 无关、忠实)
  understanding.md                 # 综合派生:事实、中立、⚠️、挂源
  assessment.md                    # 综合派生:判断、立场、会演进(depends_on understanding)
  .kairo/
    state.json                     # reconcile 账:products 段 + targets 段(见 §9)
    history/                       # 版本线(D-version)
      0000/ {docs, state.targets.json}   # init 初始态
      0001/ …                            # 第 1 次 step 收敛后
```

- workspace 即 topic,扁平、自包含。多 topic = 多个这样的 workspace。
- **扇出(P2)**:别的 topic 引用本 topic 名下某条 reference,复用其 topic 无关的 digest(机制见 §13)。

---

## 5. reference、形态、id

### 5.1 manifest(`references/<id>/manifest.yaml`)

```yaml
id: 2026-06-18-meeting
title: meeting
forms:
  - role: audio                               # role = 语义角色(media 可由扩展名推)
    location: /Users/xupeng/rec/meeting.m4a   # 指针,不拷贝(--copy 时指内部 raw/)
    hash: a3f2…
    origin: added
  - role: transcript                          # ASR 跑完追加;用户给完整转写稿也算 transcript
    location: references/2026-06-18-meeting/transcript.md
    hash: c4d1…
    origin: asr-from:a3f2…
  # 其他 role:note(人工笔记,补充)/ source_text(本身即文本 reference,如 docx)
```

- **id = 稳定主键** = 目录名 = 文档里的挂源标签。
- **`role` ∈ `audio | transcript | note | source_text`**;添加时按扩展名猜 role,此后以 manifest 为准。

### 5.2 `add` 的 id 策略

- **一条命令登记一个 reference 的所有形态**,id 只定一次:`kairo add meeting.m4a notes.md`。
- **id 默认派生** `<日期>-<文件名slug>`;`--id` 覆盖、`--to <id>` 追加形态、`--role` 覆盖猜测、`--copy` 把原文件拷进 `references/<id>/raw/`。
- id 冲突报错;`add` **topic 无关**(作用于当前 workspace)。
- **id 日期 = 添加当天**(D-id):简单稳定;补录旧素材时用 `--id` 覆盖。

---

## 6. 流水线规则与「谁控制」

| 规则 | 层级 | 触发 | 输入 → 产物 | LLM |
|---|---|---|---|---|
| **ASR** | reference(topic 无关) | 有 `audio` form、**无 `transcript` form** | audio → `transcript.md`(role=transcript) | 否(MVP stub) |
| **Digest** | reference(topic 无关) | 有可用正文(transcript/source_text)、无 digest | 正文 + note(补充)→ `digest.md`(忠实纪要,挂源) | **是** |
| **Compose** | 文档(topic 相关) | 某文档有未融入的 digest(Δ),或上游文档变(D-target),或 `compose_config_hash` 变 | (当前文档 + **本步全部 Δdigest** + 上游文档 + 维护指引) → 综合修订/重综合 | **是** |

**触发与输入的明确约定(回应 review):**
- **ASR 只看 transcript**:有 audio 且无 transcript 才转写;单纯 `note` **不抑制** ASR(否则音频内容丢失)。用户给完整转写稿 = role `transcript` → 跳过 ASR。
- **Digest 输入**:正文取 `transcript`(或 `source_text`),`note` 作**带标注的补充**;digest 输入指纹含 `pipeline.digest.prompt`(**不含 model 版本**,见 D3),故改 prompt 会重算;model 升级记入 `produced_by` 溯源、不自动重算。
- **Compose 批量 Δ**:一次 step 内,每个文档把「当前文档 + 本步**全部**新 digest」用**一次 op** 融入,**不逐条序列化** → 折叠顺序问题(原 §13#6)随之消灭。
- **target 依赖**:`assessment` 的输入含当前 `understanding`,故 step 内先综合 understanding;understanding 变则级联 assessment 重综合(D-target)。
- **ASR stub 契约**:测试/`KAIRO_STUB` → 产**显式标记**占位转写(头部 `⚠️ STUB TRANSCRIPT`),只验骨牌链、不被当真;默认真实模式无 ASR 后端 → `blocked: no-asr`,Digest **不在假转写上跑**。

**三个控制者,各管一段**(D-seg):

| 控制什么 | 谁控制 | 形态 |
|---|---|---|
| **DAG 形状**:有哪些产物、依赖、触发 | 引擎(规则) | 代码;宪法不增删规则 |
| **digest 怎么生成**(topic 无关、统一) | `constitution.pipeline` | prompt(默认+可覆盖) |
| **怎么综合进各文档**(topic 相关) | `constitution.targets[].fold_protocol` | 维护指引 |

- ASR/Digest 是 reference 级、跨 topic 共享、**生成统一**;Compose 是文档级、**topic 相关**。
- 「一资源两形态」:add 已有 text form → ASR 不触发,直接 Digest。

---

## 7. 增量综合修订(D4)与文档

### 7.1 机制:常态 B-批量增量,兜底 A

| | 常态(B-批量增量) | 兜底(A-全量重综合) |
|---|---|---|
| 输入 | **当前文档 + 本步全部新 digest(Δ)** + 上游文档 | 全部 digest |
| 编辑 | **综合级修订**:加列/去重/重组/**推翻旧结论**/标⚠️;一次 op 融完本步全部 Δ(无逐条序列) | 整篇重综合 |
| 触发 | 本文档有未融入的 Δ,或上游文档变 | `re-step` / 已融入的 digest 变更或删除 / **`compose_config_hash` 变**(改了 fold_protocol、target;**不含 model**) / **手动校正漂移** |

- **常态 B,批量 Δ**:对原 B 的两点修正——① 砍掉「其余不动」(它逼出堆砌);② 一次 op 融本步全部 Δ(不逐条,消灭顺序依赖)。
- **可重生只锚 digest 层(D-repro)**:digest 忠实、低温、可逐字重生;understanding/assessment 是 living artifact——B 路径依赖、不可逐字重生,但每个 **A checkpoint 可从 digest 集重新综合**,B 增量是 checkpoint 之上的快进(LSM minor/major 的母题,与脑暴稿 §1 类比一致)。
- **漂移(原 §13#1)MVP 靠手动**:B 常态会攒「重读全部才看得出的全局重组」之债;MVP **不自动触发 A**,而由 `status` 显示「某文档距上次 A 已融入 N 条 digest」,用户据此手动 `re-step`。自动阈值(LSM size-tiered,如 `compose.major_every: N`)接口先想着,留 P 实现。
- **抖动(原 §13#2)靠看 diff**:B 允许「推翻重组」,「正当修订 vs 无谓搅动」MVP **不靠工具判定,靠人看 `kairo diff`**(自带、不依赖 git)+ `--dry-run` 预演;自动 diff 护栏留 P。
- discover:某文档「未融入的 digest」(Δ)= 全部 digest 减去 state 里该文档已融入集(按 digest hash 比对)。本步全部 Δ 一次 op 融入,完后记账。
- 参照 toc:`understanding.md`/`assessment.md` 正是一场会议一场会议**增量综合修订**而成(v0.3→v0.4),结构会被改(智能体表加「现状」列、术语表去重并入)。⚠️ 注意 toc 那是**人**做的增量(有持久记忆与"何时全局重组"的判断);LLM 做 B 只见「当前文档+Δ」、缺此判断 → 故需 A 兜底与漂移可见。

### 7.2 文档约定(有机生长,非冻结锚点)

- 结构(章节/表格/术语表)由综合**有机生长并修订**,不预先钉死;宪法只给**维护指引**(维持术语表、未确认挂 ⚠️、事实与判断分两篇)。
- **挂源**:章节级「来源」头 + 关键处内联标注(如 `(renmin 会议)`),不强制每条 `(src:)`。
- `understanding.md`:事实、中立,⚠️ 标未确认;`assessment.md`:判断、立场,**引用 understanding 的事实**(D-target,标来源)但只谈「我怎么看」,随讨论演进。

---

## 8. Provider 抽象

- **`ModelProvider`**:唯一缝。`ClaudeProvider`(`claude-opus-4-8`,adaptive thinking,streaming)/ `StubProvider`(确定性占位,离线 + 测试)。
- 选择:有 `ANTHROPIC_API_KEY` 且非 `KAIRO_STUB` → Claude;否则 stub。**ASR MVP 恒 stub**(留 `faster-whisper`/云接口位)。
- 每次调用产出记 `produced_by: {provider, model}` 溯源(D3);model 不进指纹、`status` 可告警「此产物由旧模型产出」。
- 不做 audit、不做 agent loop;规则自己拼 prompt、一次性调用。
- 纯 stub 离线即可端到端走通骨牌链;接 key 即用真 Claude 做纪要/综合。

---

## 9. reconcile / state 语义

`.kairo/state.json`:

```json
{
  "products": {
    "<产物路径>": {
      "input_hash": "…",                       // 输入内容 + 治理配置(不含 model)
      "produced_by": { "provider": "claude", "model": "claude-opus-4-8" },
      "status": "ok|blocked",
      "reason": null
    }
  },
  "targets": {
    "understanding.md": {
      "depends_on": [],
      "compose_config_hash": "…",              // fold_protocol + target 配置(不含 model)
      "output_hash": "…",                      // 上次产出内容(手改检测)
      "produced_by": { "provider": "claude", "model": "claude-opus-4-8" },
      "folded": { "<digest路径>": "<digest hash>" },
      "last_major_folded": { "…": "…" }        // 上次 A 时的 folded 快照 → status 算"距上次 A 已 N 条"
    },
    "assessment.md": {
      "depends_on": ["understanding.md"],
      "upstream_hash": { "understanding.md": "<output_hash>" },  // 上游变检测 → 级联重综合
      "…": "…"
    }
  }
}
```

- **input_hash / compose_config_hash** = 输入内容 + 治理配置(**不含 model**,D3);model 进 `produced_by` 溯源。
- **重产判定**:产物缺失 ∨ input_hash 变。源不可达且需重产 → `status: blocked, reason: missing-source`(不静默)。
- **综合判定(每文档独立 + 依赖)**:
  - 某 digest 不在 `folded` / 其 hash 变 → 增量综合修订(B,批量本步全部 Δ);已融入的 digest 变了 → 该文档**整篇重综合**(A)。
  - **上游文档 output_hash 变**(`upstream_hash` 不匹配)→ **级联**该下游文档重综合(D-target)。
  - **`compose_config_hash` 变**(改了 fold_protocol / target 配置)→ 该文档**整篇重综合**(A)。**换 model 不触发**(D3,只更新 `produced_by` + 告警)。
  - 当前文档内容 ≠ `output_hash` → **检测到手改** → 该文档 Compose **暂停**并报 `blocked: manual-edit`(不静默覆盖,D-status)。两条出路见下。
- **手改处理(D-status `manual-edit`)**:
  - `kairo accept <doc>`:把当前手改内容**钉为新 `output_hash` 基线**,解除阻塞,后续 Δ 在手改版上增量。
  - `kairo re-step <doc>`:**丢弃手改**、整篇重综合(对齐 D4「改动应进宪法」)。
- **`re-step`**:忽略 state 强制重算(全量 / 指定 reference / 指定文档);文档级 `re-step` = 清空该文档 folded、整篇重综合(A)。
- **版本 / rollback(D-version)**:
  - 每次 step 收敛后快照 `.kairo/history/<seq>/ = {综合文档 + state.targets 段}`;`products` 段与 `references/` **不入快照、不回退**(digest 是源侧、可重生)。
  - `kairo rollback <seq>`:恢复**文档 + targets 段**到该版本;告警「references 里更晚的 digest 将在下次 step 重新融入」。
  - `kairo diff [<seq>..<seq>]`:展示版本间(或工作态 vs 最近版本)文档差异,**自带、不依赖 git**——抖动护栏即此。

---

## 10. 宪法 = 期望状态声明(Makefile 类比)

`step` 不「执行宪法」,而是**朝宪法声明的状态调和**。`kairo init` 生成默认;字段缺省回落内置默认。

```yaml
topic: main

pipeline:                            # ① 生成层(topic 无关、统一)
  digest:
    prompt: |
      为这条 reference 写一份忠实纪要,保留要点,可溯源。

targets:                             # ② 综合层(topic 相关)——MVP 两篇
  - path: understanding.md
    layer: fact
    fold_protocol: |
      把新材料融进对本 topic 的事实理解;凡改变图景处就重组/修正/推翻,而非末尾追加。
      维持一张去重的术语表;未确认的挂 ⚠️;每段标来源。只放中立事实,判断进 assessment。
      仅对确实无关的部分不动。
  - path: assessment.md
    layer: judgment
    depends_on: [understanding.md]   # D-target:判断建立在已综合的事实之上
    fold_protocol: |
      沉淀立场与判断,引用 understanding 的事实(标来源);随新材料演进、可推翻旧判断。
      不与 understanding 的中立事实混。
```

| 字段 | 谁读 | 怎么用 |
|---|---|---|
| `topic` | Compose/status | 文档标题、身份标签 |
| `pipeline.digest.prompt` | Digest 规则 | 生成 digest 的指令(统一) |
| `targets[]` | Compose 规则 | **声明本 topic 维护哪些文档**;每个各跑一遍综合修订 |
| `targets[].depends_on` | Compose/reconcile | **target 间依赖**;定 step 内拓扑序 + 上游变级联(D-target) |
| `targets[].fold_protocol` | Compose | 该文档的综合/维护指引 |

> 加产物 = 加一行 target 声明,引擎自动多维护一篇(事实/判断分层就是这么落地的)。

---

## 11. CLI

| 命令 | 作用 |
|---|---|
| `kairo init [topic]` | 当前目录初始化为 topic-workspace(默认 `main`)+ 默认宪法 |
| `kairo add <files...> [--to id] [--id id] [--role r] [--copy]` | 往本 workspace references 登记(指针),自动派生 id |
| `kairo step [--dry-run]` | 跑调和循环到收敛(`--dry-run`:综合不写盘,只输出将产生的 diff) |
| `kairo re-step [target]` | 强制重算(全量 / 指定 reference / 指定文档) |
| `kairo accept <doc>` | 接受手改、钉为新基线,解除 `blocked: manual-edit`(D-status) |
| `kairo status` | 列 references / 产物 / 各文档融入状态 / **距上次 A 已 N 条 / 旧模型告警** |
| `kairo history` | 列版本快照(D-version) |
| `kairo rollback <seq>` | 回退文档 + targets 段到某版本(D-version) |
| `kairo diff [<seq>..<seq>]` | 版本/工作态文档差异(自带,不依赖 git) |

---

## 12. 分期

### MVP 内部四段递进(D-phase)

| 段 | 范围 | 验什么 |
|---|---|---|
| **M0 走路骨架** | 全 stub:`add`(audio+text)→ ASR stub(标记占位)→ Digest stub → Compose stub 进**单篇 understanding**;**`.kairo/history` 快照从此写入** | reconcile 循环 / state 记账 / folded / 收敛 / 完整骨牌链 / 挂源。**零 API** |
| **M1 接真 Claude** | Digest + Compose 换 `ClaudeProvider`,跑 `~/lab/tiansu/toc` 的真实文本 reference | 真实纪要/综合质量;`produced_by` 溯源 |
| **M2 第二层 + 依赖** | 加 `assessment`(`depends_on understanding`)+ 级联重综合 | 事实/判断分层;拓扑序;上游变级联(D-target) |
| **M3 治理闭合** | `re-step` / 手改 `blocked`+`accept` / `blocked` 三态(no-asr·missing-source·manual-edit)/ 漂移可见 / `rollback`·`diff`·`history` 命令 | 安全网与可追溯闭环 |

> 本质:M0 把 toc 的核心循环用一个 topic、零 LLM 复现出来(证明骨牌+宪法+可追溯+收敛),后续段逐层接真模型与治理。真实音频转写靠 P4 的 ASR 后端。

### M1 验收基准:toc 回归(real-data dry-run)

以 `~/lab/tiansu/toc/` 为黄金参照,把"按顺序 add+step 能否长出根目录 understanding/assessment"做成 M1 的端到端验收。

**输入(两轨,缺一不可)**:
- **纪要流轨**:三场会议的现成 transcript —— `nutritionist_0617` / `renmin_0617`(均有 audio→whisper transcript)/ `wangqiang_0617`(无 audio,仅文字实录,正好验证「给 transcript 跳过 ASR」)。
- **资料库轨**:`references/康医通系统/`(8 份 docx)+ `references/nutritionist/`(I/O 语料)。⚠️ **最终文档约 1/3 内容来自此轨**(understanding §2 平台口径、§4a 真实 I/O 语料;assessment「白皮书⟂真实水位」对照需文档与会议同时在场)。docx/xlsx 异构 loader 是 P4,M1 阶段**手工转成 .md 当 `source_text` 喂入**绕过。

**流程**:先 add 资料库 → 按 `recorded_at` 顺序 `add`+`step`(nutritionist→renmin→wangqiang),每步一次 step;末了拿产出与真实 `understanding.md` / `assessment.md` **对照 diff**。

**验收维度(不要求逐字,D-repro)**:
1. **会议贡献部分长出**:§3 商业打法 / §4 方法论 / §4b 落地优先级 / §5b 三智能体 / assessment 多数判断。
2. **增量综合质量**:事实/判断分层、挂源、⚠️ 未确认、**结构有机生长与重组**(如三智能体表加列、术语表去重并入)而非堆砌。
3. **全局重组是承重点**:跨材料对照(「看医通→康医通」纠错、白皮书⟂真实水位)是 B-增量最易漂移处——记录**需几次手动 `re-step`(A)** 才让全局结构干净浮现,即为漂移自动阈值(P3)提供真实数据。
4. **合格等价物**:产出结构相似、分层正确、立场合理即通过;逐字等同非目标。

### MVP 之后

- **P2**:多 topic=多 workspace + **扇出**(跨 topic 引用,复用 topic 无关的 digest)+ 落地 D1 资料库/事件流分轨。
- **P3**:分层归并(时间维 LSM:日→周→月),被滚的就是 digest log;把综合的输入压到有界。**B 漂移的自动 A(size-tiered compaction)在此与归并一起落地**。
- **P4**:异构 loader(URL/xmind/飞书)+ 富 post-hook;真实 ASR 后端;(可选)D2 审计挂到 provider 缝。

---

## 13. 未闭环问题(待继续拍板)

> 2026-06-19 review 已收敛:Compose 常态 B-批量(D4)、可重生边界(D-repro)、漂移 MVP 手动 + 抖动看 diff(§7.1)、指纹拆 model(D3)、target 依赖(D-target)、手改处理(D-status)、版本/rollback(D-version)、MVP 四段(D-phase)、digest prompt 归属(D-prompt)、id 日期(D-id)。原 #1/#2/#6 已分别落入 §7.1 / §7.1 / 「批量 Δ」消灭。下列均为 **P2+ 远期项**,MVP 不阻塞。

1. **扇出(P2)的形态(原 #4)**:跨 topic 引用怎么声明、`folded` 各 workspace 各记、并发/命名;复用 digest 须 prompt 匹配(D-prompt)。
2. **真实 ASR 接入形态(原 #7)**:provider 接口怎么留,P4 平滑换 whisper/云。
3. **漂移自动阈值(P3)**:`compose.major_every: N` 还是偏移信号触发?何值合适——待真实数据上观察 B 漂移速度后定。
