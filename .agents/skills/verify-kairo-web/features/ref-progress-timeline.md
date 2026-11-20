# #455 Ref 进展与 Timeline

## Fixture

在 scratch root 用公开入口创建 Topic `alpha` 和一条开始于 2026-08-24 的 Ref。另建一条以同日发生的旧 Ref。两个 Ref 都用 Markdown 文本，详情可通过当天的 Timeline 打开。保持真实 provider 关闭。

```python
from pathlib import Path
import sys
from kairo.workspace import Workspace

root = Path(sys.argv[1])
ws = Workspace.init(root / "alpha", topic="进展验证")
source = root / "discussion.md"
source.write_text("# 持续讨论\n\n最初内容。\n", encoding="utf-8")
ws.add([source], ref_id="discussion", title="持续讨论", occurred_at="2026-08-24")
legacy = root / "legacy.md"
legacy.write_text("# 旧 Ref\n", encoding="utf-8")
ws.add([legacy], ref_id="legacy", title="旧 Ref", occurred_at="2026-08-24")
```

## 路径与判定

| 验收 | 入口和操作 | 可判定结果 |
|---|---|---|
| S1.A1 | `/timeline?day=2026-08-24` → 持续讨论 → 追加进展。核当天默认值，试提交仅含空格的正文，再保存 08-25 和 08-26 两条进展。重新打开详情，检查 08-24、08-25、08-26 三天及包含三天的区间。 | 空正文得到反馈，只有两条进展留存，按 08-25、08-26 排列。发生日期仍是 08-24。Timeline 只在 08-26 显示同一 Ref，区间仅一行。 |
| S2.A1 | 08-26 的该 Ref → 追加进展，保存补录 08-25。重新打开详情，再打开 08-26 Timeline。 | 补录是第三条进展，日期排在 08-26 之前。同一 Ref 仍只在 08-26 显示。 |
| S2.A2 | 通过 Topic Ref 元信息修改标题；在 scratch 用正文写入及 `retry_reference` 和 `StubProvider` 完成 Integration。回到 08-26 Timeline。 | 看到新标题，归日仍为 08-26，三条进展保留，重生成的 digest 存在。 |
| S3.A1 | 从 08-24 Timeline 打开旧 Ref。 | 原日期可找到旧 Ref，详情显示 08-24 发生日期、进展空态与追加入口。 |

补充回归：从 08-26 的「运行当前视图」仅打开确认预览，核对未加工计数与当日材料一致，随后关闭。公开只读路由的拒绝行为由 Integration 验证。
