# Knowledge 漂移：只指真受影响的产物 · Topic 页一键重算（#372）

## 入口

- Topic 页 `/w/<slug>`：右侧面板「N 个产物基于旧知识」链接 + 「重算受影响」按钮（无漂移时整块不出现）
- Knowledge 页漂移队列 `/knowledge?workspace=<slug>&queue=drift`：逐行列产物、原因（已变/新增）

## Fixture

serve 进程必须带 `KAIRO_STUB=1`（重算走 StubProvider，不触 LLM）。

```python
# uv run python /tmp/kairo-verify-fixture-drift.py "$ROOT"
import sys
from pathlib import Path
from kairo.workspace import Workspace
from kairo.engine import step
from kairo.provider import StubProvider
from kairo.knowledge import load_workspace, save_workspace, new_entry

root = Path(sys.argv[1])
ws = Workspace.init(root / "demo", topic="demo")
for name, text in {"a": "胡值彬强调能源优先。", "b": "预算讨论,无人名。", "c": "排期讨论,无人名。"}.items():
    m = root / f"{name}.txt"; m.write_text(text); ws.add([m], ref_id=name)
step(ws, StubProvider())                      # 三份 digest + understanding,均未匹配任何知识
doc, _ = load_workspace(ws.root)
doc.entries.append(new_entry(title="胡值彬", scope="workspace"))
save_workspace(ws.root, doc)                  # 之后确认的人名:只有 a(及折叠了 a 的 understanding)受影响
```

## 路径 → 可判定结果

| # | 路径 | 可判定结果 |
|---|---|---|
| A | GET `/w/demo` | 面板出现「2 个产物基于旧知识」（a 的 digest + understanding.md）与按钮「重算受影响」；b、c 不计入 |
| B | GET `/knowledge?workspace=demo&queue=drift` | 漂移列表恰 2 行：`a` 的纪要行带「新增:胡值彬」；`understanding.md` 行；无 b/c |
| C | 在 `/w/demo` 点「重算受影响」 | `#step-area` 出现运行进度并到 done；**重新 GET** `/w/demo` 后「基于旧知识」整块消失 |
| D | 重算后 GET `/knowledge?workspace=demo&queue=drift` | 队列空态；`.kairo/state.json` 中 `references/a/digest.md` 的 `knowledge_diagnostic.matched_entry_ids` 非空，`references/b/digest.md` 的 `knowledge_generation` 与重算前相同 |
| E | 无漂移时 GET `/w/demo` | 无「基于旧知识」文案、无「重算受影响」按钮 |
