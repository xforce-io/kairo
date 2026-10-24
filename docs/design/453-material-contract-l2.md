# #453 L2 — 材料元数据契约 · 最小技术合同

- Issue: [#453](https://github.com/xforce-io/kairo/issues/453)
- L1 基线：`docs/design/453-material-contract.md` 版本 **`453-l1-v1`**（**Approved** peng 2026-10-24 ~17:23 Asia/Shanghai；S5=正文+图）
- 版本: `453-l2-v1`
- 状态: Active（随已批准 L1；实现阶段）
- 日期: 2026-10-24 Asia/Shanghai
- L2: write 依据见 how 文末：公共契约面 + Ref vs Project URL 责任边界 + verify 地图（S5）
- how：`/tmp/kairo-verify/453/how.md`

本文**不**复制 L1 产品正文；**不**写巨量实现清单 / TODO。

## 1. 设计依据与技术目标

落实 L1.8：`S1.A1`–`S5.A1`。技术目标：

- 契约事实源落在设计文档（及可选 glossary），运行时继续消费既有 constitution / Form / DigestRule 字段。
- 明确文件落点与模块责任；新增模态优先改声明，不新开 digest 平行分支。
- 为 S5 指定 verify 功能文件映射。

非目标：通用爬虫、废除 Transform、迁移 Project `read-url` 入 Ref（同 L1 Out）。

## 2. 现状与改动范围

依据 how（源码出处）：

| 复用 | 路径 |
|---|---|
| Form / Manifest | `src/kairo/models.py` |
| roles_by_ext / transforms / body_roles | `Constitution` 同文件 |
| TransformRule / DigestRule / catalog | `src/kairo/rules.py`、`src/kairo/catalog.py` |
| step 组规则 | `src/kairo/engine.py` · `_build_rules` / `step` |
| Project URL | `src/kairo/project_materials.py`、`src/kairo/readers.py` |

本票预期改动面（实现阶段，非本 L2 已改代码）：

- **文档**：L1/L2（本对）；glossary 已补「role / 推荐正文工具 / 仅原件可读」三行。
- **运行时**：以「声明补齐 + 验收门禁」为主；若现网已满足 S4/S5，可只加测与 verify，不扩引擎。若缺口（例如某路径静默丢 attachment），最小修补 DigestRule/catalog 消费，禁止平行管线。
- **verify**：新增或扩展功能文件覆盖 S5（见 §8）。

## 3. 总体架构与关键路径

```text
add/attach → Manifest.forms(role…)
    → TransformRule(constitution.transforms)  // 推荐正文
    → DigestRule(body_roles 必读 + attachment 按需)
    → digest.md（一条 Ref 一份）

URL（现状）→ project_materials.read_url_material → 材料身份 type=url
         ↛ Ref Transform（本票不合并）
```

失败：backend blocked → `state.products[key].status=blocked` + reason；step 收敛且可观察（L1 R4）。

Transform blocked 露出（S4，稳定 reason：`missing-source` / `no-asr` / `asr-failed` / `convert-failed`）：

| 面 | 读法 |
|---|---|
| `state.products[<transform-key>]` | `status=blocked` + `reason` |
| `ref_product_blocks(ws, id)` | Web / CLI 单条 Ref 的 product blocks |
| `kairo status` | `⚠ blocked:reason` |
| `kairo status --json` | `blocked_reasons[].reason`（`scope=ref`） |
| Web run plan | `blocked_refs` |

`asr-failed` / `convert-failed` 为终态（须 `retry-ref` / `re-step`）；`no-asr` / `missing-source` 条件恢复后可自动重试。

## 4. 数据与状态契约

- **不变量**：一条 Ref 至多一份 `references/<id>/digest.md`；多模态共享。
- **Form.role**：摄入或 Transform 写入；契约枚举以 L1 R1/R2 为准。
- **constitution**：`roles_by_ext`、`transforms`、`body_roles`、`source_classes` 为声明面；新类型优先改这里。
- **ProductState**：Transform/Digest 的 blocked reason 保持稳定字符串（既有 asr-failed、convert-failed、provider-failed…），供 S4 取证。
- **无新存储形态**除非实现证明缺口；禁止为「文件」「网址」另造平行 manifest schema。

## 5. 接口与协作契约

- CLI/Web 入口名本票不强制新增；S4/S5 走既有 `step`/`run`/Console 调和。
- Project：`project read` 拒 URL；`project read-url` 保持独立（how 已引）。
- Agent 消费：材料目录格式仍由 `catalog.format_catalog` 产出；必读/按需语义不变。

## 6. 运行与保障

- corpus（fold=False）：不 Transform、不 Digest —— 实现不得「为了网址/文件」打破此层。
- stub（`KAIRO_STUB`）：测例可产占位正文；验收注明 stub 只证明编排，不证明真实 whisper/markitdown。
- 取消/超时：沿用既有 provider-failed / transport halt；不新发明。

## 7. 迁移、发布与回滚

- 无强制数据迁移。历史单 form Ref 仍合法。
- 回滚：还原设计文档与（若有）声明/测例；磁盘 forms 无需改写。
- 发布：随实现 PR；设计 Draft→采用以 L1 确认规则为准。

## 8. 测试与验证（映射 L1.8）

| L1.8 | 机制 | verify / 测 |
|---|---|---|
| S1.A1 | 设计 + manifest 对照 | 人读设计；可选 pytest 读样例 manifest |
| S2.A1 | 设计建议表 | 人读 |
| S3.A1 | 设计对照结论 | 人读 |
| S4.A1 | TransformRule + step | 既有 `tests/test_transform.py` 等；CLI step 证据 |
| S5.A1 | DigestRule catalog + digest 产出反映 | **主功能文件**：`.agents/skills/verify-kairo-web/features/material-multiform-digest.md`；旁证 `tests/test_meeting_multiform_e2e.py` / `test_rules_digest_attach.py`。真路径证明以 verify 手册为准，不以单测代替「产出可指认反映」 |

每个 Issue S 一个主要功能文件：S5 → 上述 material-multiform-digest；S4 可与 `ref-run-runtime.md` 交叉链接，但 S5 反映口径不并进 notes 类 feature。

## 9. 技术风险与开放问题

- S5「可指认反映」依赖模型产出，需在 verify 步骤写明判定样例（引用/转述/依据句），避免主观口头 pass。
- 若真实 ASR 环境缺失，S4 音频路径用 blocked 可观察或受控 stub，并在证据中标明边界。
- L1 开放问题 1/3 已决（批准 + S5 正文+图）；glossary 是否补名词表行由实现决定，不得缩小 L1.8 必需项。
