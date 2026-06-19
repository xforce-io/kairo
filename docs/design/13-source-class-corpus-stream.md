# 源分层概念模型：corpus(基线) vs stream(观测)

- Issue: #13
- 分支: `feat/13-source-class-corpus-stream`
- 状态: 设计已评审通过（对话），实现中

## 背景

kairo 当前把所有 `add` 进来的 reference 当**同质**素材统一 fold，没有区分两类时间动力学完全不同的源：

| 维度 | corpus（基线） | stream（观测） |
|---|---|---|
| 时间性 | 存量，不定期升版 | 持续追加，只增不改 |
| 认知角色 | 参照系 / 权威基线 / 佐证 | 新观测 / 信号 |
| 可信度 | 高（正式文档） | 中（ASR、口述，可能有误） |
| 对产物作用 | 校正、锚定、定义术语 | 推动图景演进、推翻旧判断 |

toc 基准对比暴露根因：bench3 三源全是 stream、缺 corpus 层，所以校正不了 ASR 品牌名（「看医通」未纠成「康医通」）、给不出架构锚点 —— 不是推理弱，是缺一整类源 + 缺源分层语义。

对应两种知识：corpus ≈ 本体/世界模型（"是什么"），stream ≈ 观测/事件（"正在发生什么"）。好的理解 = 用稳定本体解释并校正流动观测，同时用观测更新本体。

## 目标（v1，纯 prompt 级，零引擎/状态改动）

corpus 相对 stream 产生两个行为，**都在 Compose 的 prompt 层实现**：

1. **校正/术语权威**：冲突时以 corpus 为准、用 corpus 校正专名/术语，corpus 当权威术语基线。
2. **产物分区标注**：understanding/assessment 里把「来自基线」vs「来自观测」标清楚。

**不做（YAGNI，后续另立）**：corpus 升版触发重算；fold 先后顺序优先。

## 设计

### 1. 概念模型（constitution —— 中心，语义数据化）

新增 `source_classes`，每类声明显示标签 + fold 语义；引擎不硬编码语义，全部从 constitution 读：

```yaml
source_classes:
  stream:
    label: 观测
    hint: 会议/事件流；逐条融入，判断随之演进、可推翻旧判断。
  corpus:
    label: 基线
    hint: 权威参考资料；与观测冲突时以基线为准，用基线校正专名/术语(如 看医通→康医通)，并作术语权威基线。
default_class: stream
```

### 2. 每条 reference 的归属（manifest）

- `Manifest` 加字段 `class`（默认 `stream`）。Pydantic 默认保证旧 manifest（无该字段）→ `stream` → **行为与今天完全一致**（向后兼容，无状态迁移）。
- CLI：`kairo add 白皮书.md --corpus` → `class: corpus`；不带则 `stream`。

> 概念上 class（认识论地位/权威）与 role（形态 audio/transcript/source_text）正交，故用独立字段而非复用 role。

### 3. 唯一行为改动：Compose（prompt 级）

`rules.py` 拼 context 时，数据均取自 constitution：

- **分区标注**：每条 digest 块来源标签带 class label —— `[来源:references/白皮书/digest.md ·基线]` / `[来源:…/会议/digest.md ·观测]`。映射链：digest path → ref id（`path.split('/')[1]`）→ `read_manifest().class` → `constitution.source_classes[class].label`。
- **注入 hint**：把本次 delta 中出现的各 class 的 `hint` 组装成一小段前言，附到 system prompt（`fold_protocol + 源分类前言 + 既有 _OUTPUT_DISCIPLINE + _COMPOSE_DISCIPLINE`）。**仅当本次 fold 同时含 ≥2 个 class 时才注入**，保持单类场景 prompt 干净、零变化。

### 4. 明确不动

Digest 阶段（仍逐条忠实纪要，class 不影响纪要本身）、`state.json`、engine 循环、history 均不变。

## 局限（记录）

- 改一条 ref 的 class 不会自动触发重算 —— 需手动 `re-step`（class 不进 input_hash）。
- fold 仍批量同时融入、不分先后（顺序优先这版不做）。
- 二进制 corpus（docx/pdf）仍需先转 md/txt 才能 add（受 `_read_body` 用 `read_text()` 限制，与本 issue 正交）。

## 涉及模块

| 文件 | 改动 |
|---|---|
| `models.py` | `Manifest.class_` 字段（yaml 键 `class`，默认 stream）；`Constitution.source_classes` + `default_class` + 默认值 |
| `workspace.py` | `add(..., source_class=None)` 透传写入 manifest |
| `cli.py` | `add` 加 `--corpus` flag |
| `rules.py` | Compose：digest 块加 class label + 组装/注入源分类前言（≥2 class 时） |

无新依赖、无状态迁移。

## 验证

- 单元测试：
  - mixed（corpus+stream）→ compose context 含 `·基线`/`·观测` 标签 + hint 前言。
  - 纯 stream → 无 hint 前言、context 与今天一致。
  - 旧 manifest（无 class 字段）→ 默认 stream，无回归。
- 真实复测（可选，验证价值）：toc 白皮书转 md 后 `add --corpus` 进 bench3 再 step，看品牌名是否被校正、架构锚点是否补齐。
