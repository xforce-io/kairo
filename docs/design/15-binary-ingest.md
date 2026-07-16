# #15 二进制/结构化源摄入：docx/pptx/xlsx/pdf → source_text

- Issue: https://github.com/xforce-io/kairo/issues/15
- 分支: `feat/15-binary-ingest`
- 状态: 定稿（本文档为单一事实源；issue body 保留一行摘要 + 链接）

## 缺口

kairo 只吃纯文本：`DigestRule` 从 `body_roles=[prose, transcript, source_text]` 取正文。
二进制源（docx/pptx/xlsx/pdf）没有纯文本正文 → 取不到 body → 不产 digest →
**进不了 compose**。唯一办法是用户手工 pandoc 转 md（且 `.xlsx` 表格语义丢失，pandoc 读不了）。

## 方向：与 ASR 同构，下游零改动

架构已有声明式 `Transform(consumes→produces via backend)` + `roles_by_ext` + 资源转换规则。
二进制摄入与 ASR 同构：`audio→transcript` ↔ `binary→source_text`。
`source_text` 已是 `body_role`，**下游 normalize/digest/compose 全部零改动**。

```
audio  --(whisper backend)----> transcript    （已有）
binary --(markitdown backend)-> source_text   （新增）
```

## 范围（已与 owner 锁定；#88 引用模型）

- **stream 型二进制**：`add x.docx`（默认 stream，fold=True）→ 产 `source_text` Form
  → 进 digest/compose 管线。
- **corpus 二进制（#88 引用模型）**：基线是**路径指针**，不跑 markitdown / 不抽 `source_text`、
  不 digest、不 fold。`corpus.collect` 把 `document` 等原件路径挂进基线前言 + `read_dirs`；
  Web 能预览（md/图）则预览，否则「用系统应用打开」。目录指针（#24）内二进制叶子 bulk 物化
  **不在本 issue**。
- **格式**：docx / pptx / xlsx / pdf 全上（stream 侧 markitdown 统吃）。
- **非目标**：OCR、扫描件、版面/图片提取、表格→结构化 schema（正交，另一层）；基线全量抽取。

## Backend 选型：markitdown 单后端

微软 `markitdown`，纯 pip 依赖，统吃 docx/pptx/xlsx/pdf/图片，产物即喂 agent 读的 markdown。

- 相比 v1 提案的 `pandoc`(系统二进制，读不了 xlsx) + `openpyxl` 双后端：一个依赖全包，
  零系统二进制，一条 Transform、一个 backend——契合「结构大于逻辑、最好的代码是无需代码」。
- **进程内调用**（`MarkItDown().convert(path)`），不走 ASR 那套「本机可配置命令」——
  markitdown 是声明的项目依赖，无需用户配置。

## 改动清单

1. **`roles_by_ext` 扩展**（`models.py`）：`.docx/.pptx/.xlsx/.pdf → document`
   （单一 role，markitdown 统吃，无需按格式分裂 role）。
2. **默认 Transform 增一条**（`_default_transforms`）：
   `Transform(name="doc2text", consumes=["document"], produces="source_text", backend="markitdown")`。
3. **`AsrRule` → `TransformRule`**（`rules.py`）：类已参数化 consumes/produces/backend；
   仅把名字改诚实 + 把后端执行抽到 `backends.py`。`discover()` 跳过 corpus（fold=False）—
   基线不派生（#88 引用模型）。
4. **新 `kairo/backends.py`**：后端 dispatch 注册表，统一返回
   `("ok", text, origin)` | `("blocked", reason)`。
   - `markitdown`：进程内转换；产 `source_text`，`origin="markitdown-from:<src_hash>"`；
     转换失败/空产物/未安装 → `blocked: convert-failed`（终态，不自动重试，同 asr-failed）。
   - `whisper`/其它 asr 系：沿用 `machine.resolve_asr` + 命令执行（`_run_asr_cmd` 迁入本模块），
     行为不变（`no-asr` / `asr-failed` 语义保留）。
   - 源不可达由 `TransformRule.run` 在 dispatch 前判 `blocked: missing-source`（源回来自动重试）。
5. **依赖**（`pyproject.toml`）：加 `markitdown[docx,pptx,xlsx,pdf]`。

## 分层

```
machine.py   配置解析（resolve_asr：env/config.toml）
   ↑
backends.py  后端执行 + dispatch（run_backend：markitdown 进程内 / asr 命令）
   ↑
rules.py     规则编排（TransformRule：discover/run/is_stale）
```

## blocked 状态语义（沿用既有收敛策略）

| reason | 含义 | 重试条件 |
|---|---|---|
| `missing-source` | 源文件不可达 | 源回来（`src_path.exists()`） |
| `no-asr` | 本机未配 asr 后端（仅 asr 系） | 配好 ASR / stub 模式 |
| `asr-failed` | asr 命令失败 | 终态，手动 re-step |
| `convert-failed` | markitdown 转换失败/空/未安装 | 终态，手动 re-step |

## 测试

- **stub 模式**覆盖 rule 接线：stream `document` 被 discover、corpus 二进制不 discover、
  missing-source、收敛幂等；corpus.collect 纳入 document 指针（#88）。
- **真实转换**：提交小 fixture（`tests/fixtures/sample.docx/.pptx/.xlsx/.pdf`），
  以 markitdown 可用性 gate（`pytest.importorskip("markitdown")`）跑端到端转换断言。
- ASR 既有测试全绿（仅类名 `AsrRule`→`TransformRule` 的机械替换）。
