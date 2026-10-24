# 多模态材料 digest（#453 S5）

主证 **S5.A1**（同 Ref 正文类 + 图 attachment，digest 可消费且可指认反映）。S1–S4 只列指针，不在此重复 Drive。scratch serve only；不碰现网 `~/kairo`，**不触发真 LLM Run**。

S5 默认组合：`transcript` 或 `source_text` + `attachment`(图)。本票不强制双正文。

## 入口

- CLI：scratch Topic 上 `KAIRO_STUB=1` 的 `kairo add` / `kairo run --ref`（或下列 pytest 旁证）
- 读 `references/<id>/manifest.yaml`、材料目录快照、`references/<id>/digest.md`
- **不走：** 现网 root、真 ASR/LLM、Project `read-url`、把网址登记成 attachment

## Fixture

一条 stream Ref：先挂正文（`.txt` → transcript，或办公文档 → source_text），再 `--to` 挂一张图。用 `Workspace.init` + `add`；脚本放 `/tmp`，不进仓库。

旁证（编排 / persona / 目录，不代替真模型识图）：

```bash
env -u KAIRO_SERVE_ROOT uv run --all-extras pytest \
  tests/test_rules_digest_attach.py \
  tests/test_meeting_multiform_e2e.py -q
```

## 路径 → 可判定结果

| # | 路径 | 可判定结果 |
|---|---|---|
| S5.A1 | 登记正文+图 → stub/确定性 provider 跑 digest → 读材料目录与 `digest.md` | 可消费集合同时含必读 body role 与按需 `attachment`；digest 对图至少 1 处可指认反映（引用文件名 / 转述可见内容 / 写明依据）。禁止只消化正文、对图零指认。stub 只证编排与 persona/目录；Jenny 用可读模型核真实 digest 摘录 |

判定样例（任一条即「可指认」）：引用 `board.png`；转述图上可见文字/结构；写「依据附件 …」。仅有目录行、正文只复述 transcript，不合格。

## S1–S4 指针（本文件不重复 Drive）

| ID | 入口 | 可判定 |
|---|---|---|
| S1.A1 | 人读 `docs/design/453-material-contract.md` §7 + 一条多形态 `manifest.yaml` | 五类 role 在契约中各有说明；manifest 字段能对上号 |
| S2.A1 | 同文件 §7 处理建议表 | 音频 / 办公文档 / 图片区分「推荐正文工具」与「仅原件可读」 |
| S3.A1 | 同文件 §6 文件/网址对照 | 文件按内容分 role；网址不默认 attachment、不迁 Project `read-url` |
| S4.A1 | `tests/test_transform.py`（含 `test_s4_audio_and_document_transform_or_blocked_visible`）、`tests/test_rules.py` ASR；blocked 露出见 L2 | 得 transcript/source_text，或 `products` / `kairo status` / `--json blocked_reasons` 上明确 blocked。交叉 `ref-run-runtime.md` 只证 run 编排，不证多模态反映 |

## Evidence

`/tmp/kairo-verify/453/`：`S5-before` / `S5-after`、材料目录快照、digest 摘录。证据目录不进仓库。

## Cleanup

关闭本次 scratch server；只删 scratch root。
