# #419 平时只处理单条材料，综合单独更新

状态：Approved

事实源：本文件。L1 评论：[issuecomment-5776248791](https://github.com/xforce-io/kairo/issues/419#issuecomment-5776248791)。批准记录：[issuecomment-5776412994](https://github.com/xforce-io/kairo/issues/419#issuecomment-5776412994)。

## 1. 背景

[Issue #419](https://github.com/xforce-io/kairo/issues/419)。当前 `kairo run` 等同整主题调和，转写、纪要和 `understanding.md` 写在同一次里。综合超长或溯源无效会拖住同一次里尚未转写的材料。L1 已批准。本文件补上命令行字段名，以及 operator skill 要对齐的行为。

## 2. 名词解释

新增 [未折入](../glossary.md)。已有的 Topic、Ref、digest、fold、待处理、已融入、全量综合后已增量融入只以名词表为准，此处不抄。

`compose-over-budget` 与 `compose-provenance-invalid` 是状态原因码，不是领域名词。

## 3. 目标与非目标

### 目标

- 指定一条材料的 `kairo run --ref` 只做到转写和纪要。
- 单独综合走 `kairo step --understanding-only`。
- 未折入在未综合时仍可从 `kairo status` 读到。人读与 JSON 数字相同。
- 综合阻塞不挡住单条转写和纪要。
- 仓库内 operator skill 与上述三条命令对齐。

### 非目标

- 不改 2 万字上限，不改模型与推理级别，不自动补跑历史未折入，不改纪要写法。
- 不新造子命令。不改 `kairo run --all` 与 `kairo run-view`。
- 不改 Web 主按钮，不改名词「入集 Topic」。
- 不把 `kairo re-step understanding.md` 改成日常综合。
- 不要求 `kairo status` 在综合失败时以非 0 退出。
- 不核查能源梳理两条录音，也不核查「文智沟通-260901」。不打开部署。

## 4. 能力

### 4.1 UI/UX

N/A。本项没有页面。Web 主按钮保持现状，不在验收内。

### 4.2 命令

主题沿用已有 `--topic` / `-t`，省略时为当前 Topic 目录。

| 意图 | 命令 | 成功时 | 拒绝或失败 |
|---|---|---|---|
| 只处理指定的一条 | `kairo run --ref <reference id>` | 退出码 0。该条有转写和纪要。understanding 正文、折入计数、未折入都不变。该条原本已有转写和纪要时同样退出码 0，且不改上述三项。 | 不带 `--ref`：退出码 2，不写任何产物。`--ref` 与 `--all` 同时出现：退出码 2。材料不在该主题，或是 corpus：退出码 2，不写。 |
| 只把已有纪要折入 understanding | `kairo step --understanding-only` | 退出码 0。不新开转写或纪要。未折入下降数等于本次实际写入条数。 | 长度上限或溯源无效：退出码不为 0。见 §4.4。 |
| 看未折入 | `kairo status`；`kairo status --json` | 退出码 0。人读与 JSON 的未折入相同。 | 参数错误仍是退出码 2，与综合失败分开。 |

不带新开关的 `kairo step` 保持现有整链调和，含转写、纪要和综合。

### 4.3 status 契约

人读的 target 行在现有「已融入」「全量综合后已增量融入」之外，增加「未折入 N」。

`kairo status --json` 的每个 `targets[]` 增加整数 `unfolded`。这是未折入的唯一 JSON 字段名。不使用 `not_folded`、`pending` 或 `stale` 表示这个数。

`unfolded` 的计算：该主题内已有纪要、且该纪要尚未记入该 target 折入账的材料条数。不含尚无纪要的材料，不含 corpus。

单条是否已折入仍用 `kairo status --ref`。其 `state` 闭集不变：`folded_current`、`not_folded`、`folded_stale`、`digest_missing`。

### 4.4 综合失败

只适用于 `kairo step --understanding-only`。失败原因沿用 `compose-over-budget`、`compose-provenance-invalid`。

- 当次一批都没提交就失败：正文与执行前逐字节相同；`unfolded` 仍为失败前的 N（N 大于 0）。
- 已有一批提交成功、下一批失败：未折入等于失败前条数减去第一批实际写入条数，且仍大于 0。正文停在第一批提交后的版本，不含失败候选。分批沿用现有规则（约 2.4 万字符或 16 条）。
- 开跑前 understanding 已是上述两种阻塞之一：命令不以成功结束；未折入仍为 N；正文不变；原因仍在。

两种失败下，人读与 `targets[].unfolded` 相同，并带对应原因。`kairo status` 成功读出时退出码为 0。

单条 `kairo run --ref` 不调用综合。上述阻塞只留在 understanding 上，不写到该条材料上。

## 5. 思路与折衷

未指定材料时拒绝 `kairo run`，不保留「无参数等于整主题」。整主题仍用现有 `kairo step`。放弃兼容开关。

单独综合是 `kairo step` 上的 `--understanding-only`，不是新子命令，也不是 `re-step understanding.md`。后者仍是强制整篇重算。

未折入放在已有 `kairo status` 上。字段名定为 `unfolded`，与人读「未折入」一对一。放弃把「全量综合后已增量融入」或 `pending` 当作未折入。

综合失败时只扣已成功提交的批。放弃「只有条数下降才算有结果」。

operator skill 与命令同时改。放弃只改 CLI、让 skill 继续把无参 `kairo run` 或整链 `kairo step` 当成「处理这一条」。

## 6. 架构

分层不变：命令解析 → 现有调和。不新增进程，不新增存数。

主路径：`kairo run --ref` 停在该条转写和纪要 → `kairo status` 读出 `unfolded` → `kairo step --understanding-only` 只折入已有纪要。

失败路径：`--understanding-only` 在长度上限或溯源无效时非 0 退出，已成功批次保留，失败稿不覆盖；`kairo status` 以退出码 0 读出剩余 `unfolded` 和原因。无 `--ref` 的 `kairo run` 在写盘前拒绝。

## 7. 模块

N/A。不在本文件指定改哪些函数。实现仍落在现有 `run`、`step`、`status` 上。

要改的 skill 只有一份：`src/kairo/data/SKILL.md`（`kairo connect` 挂到本机的 operator skill）。不改 `.agents/skills/verify-kairo-web`。

该 skill 对齐到下面三条，不保留与之相反的旧说法：

1. `kairo run --ref <id>`：只做该条转写和纪要，不改 understanding，不减少未折入。用户要「处理这一条」时走这里。无参 `kairo run` 会退出码 2，不得再写成整主题或 Web 主按钮的等价命令。
2. `kairo step --understanding-only`：只折入已有纪要。用户要「单独综合」时走这里，不走 `kairo re-step understanding.md`，也不走不带该开关的 `kairo step`。
3. `kairo status`：人读写出未折入；`--json` 用 `targets[].unfolded`。不得把 `pending`、已融入、全量综合后已增量融入读成未折入。综合失败后，status 仍是只读，退出码 0；原因码仍是 `compose-over-budget` 或 `compose-provenance-invalid`。

登记一条录音之后，若用户只要这一条的转写和纪要，下一步是 `kairo run --ref`，不是默认 `kairo step`。不带开关的 `kairo step` 只在用户明确要整主题调和时使用。

`kairo run --all` 与 `kairo run-view` 的既有说明保持不变。

## 8. API/CLI

无 HTTP API。命令行即 §4.2–§4.4。

退出码：

- 0：`kairo run --ref` 完成或无需再写；`--understanding-only` 按写入条数折入成功；`kairo status` 成功读出。
- 2：`kairo run` 缺少 `--ref`，或 `--ref` 与 `--all` 冲突，或材料不在主题，或材料是 corpus，或 status 参数错误。
- 非 0 且非上述用法错误：`--understanding-only` 因长度上限或溯源无效失败。不要求 status 使用这个退出码。

## 9. 边界

- 未折入不含尚无纪要的材料，不含 corpus。
- 一批已提交后再失败时，`unfolded` 只减去已提交条数。
- 开跑前已阻塞时，单独综合不报成功，`unfolded` 不变。
- 单条 run 不清除综合阻塞。
- 本项不改 Web，因此 Web 上仍可能整主题运行。这不在 S1–S3 的通过条件里。

## 10. 迁移/兼容/回滚

无参数 `kairo run` 从整主题推进改为拒绝。这是不兼容变更，不留兼容开关。调用方改用 `kairo step` 做整主题，或改用 `kairo run --ref` 做单条。

`kairo run --all`、`kairo run-view`、不带 `--understanding-only` 的 `kairo step`、`kairo re-step` 的行为不改。

无数据迁移。回滚是回到变更前的命令行为；不新增需要清理的存数。

## 11. 测试计划

用户可见路径在命令行，不在 Web。`verify-kairo-web` 不触发会调 provider 的 Run，且本项非目标明确不改 Web。因此不新增、不更新 `.agents/skills/verify-kairo-web/features/` 下的文件。S1–S3 的证明走 CLI。

E2E：

- S1：`kairo run --ref` 只处理指定的 1 条。该条有转写和纪要；understanding 正文与折入计数不变；`unfolded` 不变。
- S2：understanding 已处于终态阻塞时，对尚未转写的一条执行 `kairo run --ref`。转写与纪要都产出；阻塞仍只在 understanding。
- S3：综合前 `kairo status` 的未折入为 N，且 N 大于 0，人读与 `targets[].unfolded` 相同。成功的 `--understanding-only` 使 N 的下降等于写入条数。失败按沈予三条：一批都未提交；先成功一批再失败；开跑前已是 `compose-over-budget` 或 `compose-provenance-invalid`。过的条件是 §4.4。不过：退出码 0、失败稿覆盖正文、未折入变成 0、status 没有对应原因，或人读与 `unfolded` 不一致。
- 补充：`kairo run` 不带 `--ref` 时退出码 2，主题无写入。

Integration：`kairo status` 人读未折入与 `targets[].unfolded` 一致。`kairo run --ref` 与 `--all` 同时出现时退出码 2。`src/kairo/data/SKILL.md` 含 §7 的三条命令，且不再把无参 `kairo run` 写成整主题入口。

Unit：`unfolded` 不含尚无纪要的材料，不含 corpus。已提交批与随后失败批的差等于已提交条数。

## 12. 开放问题

无。JSON 字段名已定为 `unfolded`。Web 主按钮是否以后跟单条语义，不在本项。

## 13. 关联

- https://github.com/xforce-io/kairo/issues/419
- L1：https://github.com/xforce-io/kairo/issues/419#issuecomment-5776248791
- 批准：https://github.com/xforce-io/kairo/issues/419#issuecomment-5776412994
- 现有入口：`src/kairo/cli.py` 的 `run`、`step`、`status`、`re-step`
- 要改的 skill：`src/kairo/data/SKILL.md`
