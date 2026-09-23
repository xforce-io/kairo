# #423 — `kairo run --ref` 默认追加一条机器 note

- Issue: [#423](https://github.com/xforce-io/kairo/issues/423)
- 分支: `feat/423-run-ref-generated-note`
- 状态: Draft（薄 L2，待核对；未核对前不写功能代码）
- L1: [Approved](https://github.com/xforce-io/kairo/issues/423)（issue `## 设计`，2026-09-23）
- 本文件是 #423 的 **L2 事实源**。与实现同一分支，不单独合入设计。不混 #422。

## 1. 背景

[#423](https://github.com/xforce-io/kairo/issues/423)。`kairo run --ref` 把单条材料收到转写和详备纪要为止，不自动留下短 note。负责人已定：单条完成后默认再追加一条 note，且这条是机器生成，不能与 #410 的人工四类共用语义。

## 2. 名词解释

沿用 [docs/glossary.md](../glossary.md) 的 Ref、digest、notes、note。本设计新增并已写入名词表：

| 规范名 | 一句话定义 | 禁止别称 |
|---|---|---|
| generated | `kairo run --ref` 在该 Ref 转写和纪要完成后追加的机器 note 类型；作者固定为机器，不是人的决定或修正。 | 人工批注、insight、决策 |

`notes` 仍是挂在 Ref 上的可追加集合。`generated` 是该集合里的机器类型，不是新的存储对象。

## 3. 目标与非目标

### 3.1 目标

`kairo run --ref` 在转写和详备纪要就绪且本次 exit 为 0 时，为这一条 Ref 追加 1 条类型 `generated`、作者 `machine`、正文不超过 800 个 Unicode 字符的 note。

### 3.2 非目标

- 不改前端，不改 #411 的页面、按钮和人工类型选项。
- 不改写详备纪要，不把 note 折入 understanding。
- 不回填本次 `--ref` 以外的 Ref。
- 不覆盖、不删除已有人工 note，也不覆盖已有 `generated` note。
- 不设生成时限，失败不自动再试。
- 不进入 #422（无选项 `kairo step` 不写 understanding）。
- 超长不截断后落盘。

## 4. 能力

### 4.1 UI/UX

N/A。本票无新页面。已有 notes 读取若列出该条，类型与作者按落盘原样显示。

### 4.2 何时追加

只在本次 `kairo run --ref` 结束时该 Ref 的转写和详备纪要已就绪、且 run 按既有规则 exit 为 0 时尝试 1 次。纪要是本次新写还是沿用已有，都尝试。转写或纪要按既有规则失败时，不要求留下 note，exit 维持既有失败。

模型输入只取这一条 Ref 已完成的详备纪要。不读其它 Ref，不读 understanding，不读已有 notes。相对现行单条 run，至多多 1 轮模型调用。

正文按去掉首尾空白后的 Unicode 标量值计数，含标点与内部空白，不按字节。空、超过 800、或这一轮模型失败：不落盘，exit 仍为 0。不自动再叫一轮。用户再次执行命令是一次新的 run，不是这一轮的重试。

作者字段固定为 `machine`，不取本机用户名。同一条再次成功生成则再追加一条；已有条目的正文、类型、作者不变。

## 5. 思路与折衷

机器 note 与人工盖楼放在同一 notes 集合，用类型 `generated` 和作者 `machine` 分开。读者不看正文也能判断它不是人的决定或修正。人工写入路径不开放这个类型。

放弃「一并折入综合」：本票不写 understanding，折入另议。放弃「生成失败则整次 run 失败」：转写和纪要完成即成功。放弃「回填已有参考」。放弃「超长就截断到 800」。放弃「再次 run 覆盖上一条机器 note」。放弃「把已有人工 note 喂进这次生成」。

代价：重复执行会叠多条 `generated`。这是可观察的追加，不是幂等。

## 6. 架构

```mermaid
flowchart TD
  run["kairo run --ref"]
  run --> base{"转写和详备纪要就绪且 exit 应为 0?"}
  base -->|"否"| oldfail["按既有失败退出；notes 不增加"]
  base -->|"是"| call["至多 1 轮模型；输入仅该纪要"]
  call --> ok{"非空且不超过 800 个字符?"}
  ok -->|"是"| append["追加 1 条 generated / machine"]
  ok -->|"否"| skip["不落半条；exit 仍为 0"]
  append --> done["exit 0；understanding 不变"]
  skip --> done
```

主路径：单条 run 成功 → notes 条数 +1 → 类型 `generated`、作者 `machine`、正文 ≤ 800 → understanding 与其它 Ref 的 notes 不变。

失败路径：转写或纪要失败 → 既有非 0，本票不要求 note。模型失败、空正文或超长 → exit 0，该 Ref 的 notes 与尝试前一致。

## 7. 模块

| 面 | 变化 |
|---|---|
| `kairo run --ref` | 纪要就绪且成功结束时尝试追加 1 条机器 note |
| Ref notes 集合 | 允许类型 `generated`；只由此路径追加 |
| `kairo notes add` 与 #411 的追加 | 类型闭集仍为四类；`generated` 拒绝且不落半条 |
| `kairo notes list` / `show` | 不改命令；原样读出新类型与作者 |
| understanding / 详备纪要 | 不因本票改写 |
| Console | 不改 |
| `docs/glossary.md` | 新增 `generated` |

## 8. API/CLI

无新命令，无新 HTTP。

`kairo run --ref` 的参数与缺 `--ref` 时的拒绝保持现行。成功结束且纪要就绪时增加上面的尝试。note 失败不把 exit 改成非 0。

`kairo notes add --type generated` 以及 #411 追加接口送出 `generated`：与其它非法类型相同，`invalid_request`，不落半条。省略类型仍为 `insight`。四类人工类型的作者仍是本机用户名。

读取沿用 `kairo notes list --ref` / `show --ref`。新条的 `type` 为 `generated`，`author` 为 `machine`。

## 9. 边界

- 只作用于本次 `--ref` 的那一条 Ref。
- 不改详备纪要正文。understanding 正文与折入计数保持执行前。
- 不删除、不修改已有 note。
- 没有生成时限，也没有自动重试。
- 前端不增加对 `generated` 的选项或单独样式。

## 10. 迁移/兼容/回滚

无历史回填。已有 Ref 不会因为部署本变更而自动获得 `generated`。回滚去掉该提交后，不再追加；已经落下的 `generated` 仍是普通 notes 记录，不在本票删除。不是发版。

## 11. 测试计划

功能地图：`.agents/skills/verify-kairo-web/features/run-ref-generated-note.md`。无新 Console。模型用确定性替身。不碰现网 serve root。

| 层级 / 验收 | 路径 | 可判定结果 |
|---|---|---|
| E2E S1 | `kairo run --ref` 一条；替身返回合法正文 | exit 0；notes +1；类型 `generated`、作者 `machine`、正文 ≤ 800；详备纪要不被改写；understanding 更新次数为 0 |
| E2E S2 | 替身失败、空白或超过 800 字符 | exit 0；notes 增加 0；不落半条；不自动再试 |
| E2E S3 | 只跑一条，旁边有历史 Ref | 未指定 Ref 的 notes 增加 0；understanding 更新次数为 0 |
| E2E S1 再次 run | 同一条再成功一次 | 再 +1 条 `generated`；旧人工 note 与上一条机器 note 不变 |
| Integration | `notes add --type generated` 与四类人工 add | `generated` 为 `invalid_request` 且条数不变；人工四类行为与 #410 一致 |
| Unit | 计数与空/超长判定 | 按 Unicode 标量值；超长不截断；空正文不落盘 |

## 12. 开放问题

无。篇幅 800、失败不挡成功、不折入、不回填、再次 run 再追加，均已在 L1 冻结。本文件补上作者落盘值 `machine` 与「人工路径拒绝 `generated`」。

## 13. 关联

- Issue [#423](https://github.com/xforce-io/kairo/issues/423)
- 人工 notes [#410](https://github.com/xforce-io/kairo/issues/410)、只读与追加界面 [#411](https://github.com/xforce-io/kairo/issues/411)
- 拆开 [#422](https://github.com/xforce-io/kairo/issues/422)
- 单条 `kairo run --ref` 的既有边界见 [#419](https://github.com/xforce-io/kairo/issues/419)
