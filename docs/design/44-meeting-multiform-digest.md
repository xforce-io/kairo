# 设计:会议 = 多形态 reference,合并 digest 并支持增量维护

- Issue: #44
- 关联:#42(Web Console 添加 reference 入口)、#13(源分层 corpus/stream)、#15(二进制 markitdown 摄入)
- 状态:设计评审中

## 1. 目标

让「一个会议」成为**一条多形态 reference**:音频、PDF / Word / PPT、图片都挂在同一条会议 reference 下,合并产出**一份** digest,再折叠进 understanding。并支持**增量维护**:

- 界面能往一条已存在的会议 reference 追加 / 查看素材;
- 已总结完成的 digest 在加入新素材后能**自动重新总结**,并向下游 understanding 传播。

## 2. 现状与限制

当前引擎是「**一个文件 = 一条 reference = 一份 digest**」,两处写死了「单源」假设:

- `TransformRule._make`:`src = next(f for f in forms if f.role in consumes)` —— 只取**第一个**被消费的 form,产**单个** `references/{id}/{produces}.md`;`discover` 在 `produces in roles` 时即停。→ 一条 ref 上挂多个 document(pdf+docx+pptx),只有**第一个**被 markitdown 转,其余被忽略。
- `DigestRule._read_body`:按 `body_roles = ["transcript", "source_text"]` 顺序返回**第一个**命中的正文 form。→ 一条 ref 同时有 transcript 和 source_text 时,digest **只读 transcript**,其余正文被丢。

此外:

- `roles_by_ext` 不含图片扩展名 → `.png/.jpg` 落到兜底 `default_role = "transcript"`,会被当文本误读。
- `Workspace.add()` 传入已存在的 `ref_id` 会用新文件**重建 manifest、覆盖**既有 forms(不是追加)。
- Web「添加 reference」永远 `ws.add([src])` 新建一条,无法挂到指定会议。

故本 issue 的目标场景在现引擎下跑不通,需引擎 + 模型 + Web 三层改造。

## 3. 方案(B:digest 层合并)

### 3.1 转换路径

```
会议 reference(一条 ref,多个 form)
├─ audio.m4a   ─[asr / whisper]─────────▶ transcript.md           (role=transcript)
├─ deck.pdf    ─[doc2text / markitdown]─▶ source_text.deck.md     (role=source_text)
├─ notes.docx  ─[doc2text / markitdown]─▶ source_text.notes.md    (role=source_text)
├─ slides.pptx ─[doc2text / markitdown]─▶ source_text.slides.md   (role=source_text)
└─ board.png   ─(不转换,作附件)─────────▶ board.png               (role=attachment)
        │
        ▼  DigestRule(一次 LLM 调用)
   正文上下文 = transcript + 所有 source_text 拼接(各带来源文件名小标题)
   图片       = read_dirs 授 agent 读 attachment;persona 列出文件名 → 多模态 agent 用 Read「看」白板
        │
        ▼  digest.md(这一条会议的合并纪要)
        │  ComposeRule 折叠 Δdigest
        ▼  understanding.md(跨所有会议)
```

**关键判断**:图片**不做预抽取**(无独立视觉 backend / 新模型)。复用现有「agent Read 文件」机制(corpus 参考层同款 `read_dirs` + `--add-dir` + `--allowedTools Read`):claude-code 是多模态的,Read 图片即「看见」。这比预先 OCR/抽取更忠实(看原图而非有损文本)。代价:digest agent 必须是多模态 provider(claude-code 满足;stub/codex 看不到图,降级为仅文本)。

### 3.2 数据模型(`models.py`)

- `roles_by_ext` 增加图片:`.png/.jpg/.jpeg/.webp/.heic → "attachment"`。
- `attachment` **不在** `body_roles`(不进文本正文)、**不被任何 transform 消费**(无产物),仅作可被 Read 的素材 form 挂在 manifest。
- 不引入新的 source_class;会议仍是 `stream`(fold=True)。

### 3.3 摄入(`workspace.py`)

- `add()` 支持**追加到已有 ref**:`ref_id` 已存在 → 读出 manifest、追加新 forms、**按 location 去重**、写回(不再重建覆盖)。
- 上传 / 路径加入的素材**复制进该 ref 目录**(自包含,避免依赖会消失的外部缓存路径,如 WeWork cache)。原 add 对音频「按外部绝对路径引用」的行为保留兼容,但新的 attach 入口默认 copy-in。

### 3.4 派生(`rules.py`)

- **`TransformRule` 支持同 role 多源**:`discover` 改为「该 role 的每个源若缺对应产物则补」;产物名带来源 stem:`source_text.<slug(stem)>.md`。多个 document 各转一份。`input_hash` 仍取各源 form 的 hash(逐源独立收敛)。
- **`DigestRule._read_body` 改为拼接**:遍历 `body_roles`,收集**所有**命中的 form,按 `# <文件名>` 小标题拼成一段正文(顺序:transcript 优先,其后 source_text 按文件名排序,保证确定性)。
- **`DigestRule` 看附件**:该 ref 有 `attachment` form 时,`read_dirs` 传 attachment 所在目录,persona 追加一段「本会议另有以下现场图片,请用 Read 逐一查看并把其中与会议相关的信息并入纪要:<文件名列表>」。

### 3.5 重算触发:指纹驱动的全量重算(核心诉求)

digest **不做增量合并**——每次都从整条会议的全部素材**重新生成整份** `digest.md`(见 3.4)。是否需要重算,由一个**指纹**决定:

- digest 的 `input_hash` 定义为对**该会议当前全部输入**的指纹:
  **`hash(prompt + 当前全部正文拼接 + 所有 attachment 字节 hash(按文件名排序))`**。
- `DigestRule.is_stale`:磁盘上记录的 `input_hash` 与当前指纹**相同 → 没变 → 跳过**(`step` 显示 no change,幂等);**不同 → 全量重算**整份 digest。
- 任意素材增 / 删 / 改(文本或图片)都会改变指纹 → 触发一次全量重算;不改则永不重算。统一指纹、无文本/图片特例。

digest 变化产生 Δdigest → `ComposeRule` 自动把增量折叠进 understanding(understanding 这一层才是增量;digest 这层始终全量)。**全自动,无需手动 re-step 指定 target**。

### 3.6 Web(`views.py` / 模板)

- 单条 reference 详情页(`/w/{slug}/ref/{ref_id}`)加「素材(forms)」区:
  - **加内容**:复用 #42 的弹框样式,POST 新路由 **`POST /w/{slug}/ref/{ref_id}/attach`**(支持上传 / 本地路径);服务端按扩展名定 role(audio/document/attachment),调用 `add(..., ref_id=ref_id)` 追加。
  - **看现有 forms**:列出 audio / transcript / source_text×N / attachment×N;图片给缩略图、可点开;文本形态可预览(沿用现有 `_ref_forms` + 预览端点)。
  - 删除某 form:**本期不做**(避免范围膨胀,后续 issue)。
- i18n:新增素材区相关字符串(en + zh)。

## 4. 单元边界

| 单元 | 职责 | 依赖 |
|---|---|---|
| `models.roles_by_ext` / `attachment` | 扩展名→role 映射 | 无 |
| `Workspace.add`(追加语义) | 把多源 form 落到一条 ref(去重、copy-in) | manifest 读写 |
| `TransformRule`(多源) | 每个可转源各产一份派生文本 | backends(whisper/markitdown) |
| `DigestRule`(多正文 + 看图) | 拼接全部正文 + 授读图片 → 一份 digest;input_hash 纳入图片 | provider(多模态)、read_dirs |
| `attach` 路由 + ref 详情 UI | 增 / 看一条会议的素材 | Workspace.add |

## 5. 测试

- 一条 ref 多个 document → 各产一份 `source_text.<stem>.md`(不再只转第一个)。
- `DigestRule` 拼接 transcript + 多份 source_text 进同一份 digest。
- 新增 attachment 使 digest 指纹变化 → stale → 全量重算;指纹不变则跳过(幂等)。
- `add(ref_id=已存在)` 追加而非覆盖;按 location 去重。
- `attach` 路由:按扩展名归 role;图片 copy-in;坏路径沿用 #42 的 400+报错。
- 多模态缺位(stub provider)时降级为仅文本、不报错。

## 6. 取舍与风险

- **绑定多模态 provider**:看图只在 claude-code 下生效。可接受(默认 provider)。
- **图片每次 re-digest 被重看**:token 成本可控(会议图片通常数张);未来可加「图片描述缓存」优化,本期不做。
- **产物文件名加来源 slug**:历史只有单一 `source_text.md` 的 workspace 需兼容——`_read_body` 同时认旧的 `source_text.md` 与新的 `source_text.<stem>.md`。

## 7. 范围外(后续)

- 删除 / 重排 form 的 UI。
- 图片描述缓存。
- 视觉专用本地 backend(若将来要脱离多模态 provider 离线跑)。
