# KAIRO_PROVIDER=grok 支持目录授读

- Issue: [#350](https://github.com/xforce-io/kairo/issues/350)
- 分支: `feat/350-grok-read-dirs`
- 状态: Approved
- 最后更新: 2026-09-09

## 1. 背景

`KAIRO_PROVIDER=grok` 已能选中 `GrokProvider`，但 [#153](https://github.com/xforce-io/kairo/issues/153) 将 grok 标成不支持授读：digest/compose 只要带材料目录就 `provider-failed`，并禁止把正文倾倒回 prompt。依据是 [#61](https://github.com/xforce-io/kairo/issues/61) 对 grok CLI 0.2.101 无 `--add-dir` 的验证；[#160](https://github.com/xforce-io/kairo/issues/160) 因此在材料路径上跳过 grok。

当前 grok CLI 1.0.13 能 Read cwd 工作集里的文件；digest 的必读材料会复制进该 cwd。会话否决「digest 硬编码 grok、compose 仍走 Codex」和「再加一个 digest 专用变量」，改为只保证唯一开关 `KAIRO_PROVIDER=grok` 能按目录授读跑完。

链 [#350](https://github.com/xforce-io/kairo/issues/350)。L1 见该 issue 修订评论。

## 2. 名词解释

本设计易混：

| 术语 | 说明 |
|---|---|
| **授读** | 见 [docs/glossary.md](../glossary.md)。本期 grok 只覆盖已 stage 进 cwd 工作集的读取，不覆盖 cwd 外目录授权。 |

已有术语（digest、compose、材料目录、工作集、`KAIRO_PROVIDER`）见 glossary 与 [#153](https://github.com/xforce-io/kairo/issues/153) L2，不抄。

## 3. 目标与非目标

- **目标**：
  - `KAIRO_PROVIDER` 仍是唯一选择开关。
  - 值为 `grok` 时，带文件材料的 digest/compose 按目录授读跑完，不再因「不支持授读」失败。
  - prompt 仍只放材料目录；grok 读 cwd 工作集；不回退倾倒全文。
  - 现网默认不变：`KAIRO_PROVIDER=codex` 仍整步 Codex。
- **非目标**：
  - digest 与 compose 拆成两个 backend。
  - 新增 `KAIRO_DIGEST_PROVIDER` 或把 grok 写死在 digest。
  - 改全局 auto/默认，使未设 env 时 digest 自动变 grok。
  - 给 grok 做 cwd 外 `--add-dir`。
  - 恢复全文倾倒回退。

## 4. 能力

1. `GrokProvider` 声明可授读 cwd 工作集：`read_dirs` 非空时不再 spawn 前失败。
2. 调用 grok CLI 时预授最小读权限（`--allow Read` 一类），不使用全局 `--always-approve`，不伪造 `--add-dir`。
3. 材料路径 auto 不再把 grok 当无能力候选跳过；偏好仍是 Codex → Grok → Claude → OpenAI-compatible → Stub。OpenAI-compatible 仍不授读。
4. 显式 `KAIRO_PROVIDER=grok` 整步生效：digest 与 compose 的 `produced_by.provider` 均为 `grok`。

### 4.1 UI/UX

N/A。无新页面、无新按钮。失败仍为 `provider-failed`，不得伪装成超时。

## 5. 思路与折衷

核心：grok 成为可授读 backend，而不是第二套配置或阶段硬编码。

| 选择 | 放弃 |
|---|---|
| 一个 `KAIRO_PROVIDER`，grok 可授读 | 放弃 digest 写死 grok；放弃 `KAIRO_DIGEST_PROVIDER` |
| digest 与 compose 共用所选 backend | 放弃「digest 走 grok、compose 走 Codex」 |
| 最小授权 Read cwd | 放弃 #160 否决过的全局 `--always-approve`；也放弃继续因「不支持授读」失败 |
| 现网默认仍 Codex | 放弃把 auto/默认改成 grok |

要 grok 跑材料路径，设 `KAIRO_PROVIDER=grok`；compose 会一起走 grok。不设则现网仍整步 Codex。

## 6. 架构

```
KAIRO_STUB / KAIRO_PROVIDER / auto
        ↓ select_provider(require_read_dirs=…)
DigestRule / ComposeRule
        ↓ 材料目录 + cwd 工作集
_run_agent
        ↓ supports_read_dirs?
GrokProvider（cwd Read；无 --add-dir）
```

主路径：`KAIRO_PROVIDER=grok` → 有正文 form → digest（目录 + cwd 工作集）→ compose（understanding + Δdigest 必读）→ `produced_by.provider=grok`。

失败路径：grok CLI 不可用或调用失败 → 不写半成品 → `provider-failed`。不倾倒全文。cwd 外 corpus 树 grok 读不到，不在本期补目录授权。

`KAIRO_PROVIDER=codex` 或 auto 且 Codex 可用：与现网相同，digest/compose 均 Codex。

## 7. 模块

| 模块 | 变化 |
|---|---|
| `provider.GrokProvider` | 声明可授读；`read_dirs` 非空时预授 Read；不再 `_reject_unsupported_read` |
| `select_provider` | 材料路径不再因 grok 无授读而跳过；显式 grok 语义不变 |
| README / glossary | 材料路径有效 auto 含 grok；补授读/工作集 |
| 测试 | 改写 grok 遇 `read_dirs` 即失败的用例；补显式 grok 整步闭环 |

## 8. API/CLI

对外子命令不变。行为变化：

- `KAIRO_PROVIDER=grok` 的 `kairo step` / `run`：digest/compose 不再因授读门禁失败。
- 未设或 `KAIRO_PROVIDER=codex`：行为不变。
- 无新 HTTP 路径、无新 env。

## 9. 边界

- grok 只保证 cwd 工作集可读。cwd 外 `read_dirs` 不授权、不倾倒、不失败伪装成已读。
- OpenAI-compatible 仍 `supports_read_dirs=False`。
- 显式选择不支持授读的 backend 仍 fail-closed（#153）。
- 不改 ASR / Normalize / ReviewFold / 知识抽取的 backend 选择。

## 10. 迁移/兼容/回滚

- **默认**：现网 `KAIRO_PROVIDER=codex` 无需改配置。
- **要 grok**：`export KAIRO_PROVIDER=grok`（或等价）后整步走 grok。
- **回滚**：回退本版本后 grok 再次遇材料即「不支持授读」。

## 11. 测试计划

- **Unit**：grok 对 cwd 工作集 + 非空 `read_dirs` 不抛授读错误；CLI args 含 `--allow Read`、不含 `--always-approve` / `--add-dir`；prompt/context 不含源正文。auto：Codex+grok → Codex；grok+claude 且材料路径 → grok。OpenAI 遇 `read_dirs` 仍失败。
- **Integration**：`KAIRO_PROVIDER=grok` 跑有 stream 的 workspace：digest 与 `understanding.md` 的 `produced_by.provider` 均为 `grok`。`KAIRO_PROVIDER=codex` 的整步测试保持双方均为 Codex。
- **E2E**：丢弃 Topic + 一条小文本 Ref，`KAIRO_PROVIDER=grok` 跑 `kairo step`；digest 非空且含源事实。

对上 S1–S3。

## 12. 开放问题

无。

## 13. 关联

- [#350](https://github.com/xforce-io/kairo/issues/350)
- [#61](https://github.com/xforce-io/kairo/issues/61) Grok CLI provider
- [#153](https://github.com/xforce-io/kairo/issues/153) 目录授读（本期修订 grok 不授读条款）
- [#160](https://github.com/xforce-io/kairo/issues/160) 材料路径跳过无能力候选
