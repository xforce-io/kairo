# Knowledge 页候选审核

## 入口（全部要走）

1. 顶部导航 Knowledge → 选 Topic → 队列导航 `候选审核 · n`
2. 队列导航 `目击 · m`（直接 URL：`/knowledge?workspace=<slug>&queue=sighted`）
3. 每条候选的 `采纳到本工作区` / `忽略` 按钮；有本地 confirmed 条目时展开 `合并到已有条目`，再看下拉与 `合并`

## Fixture

写到 `/tmp/kairo-verify-fixture.py` 后 `env -u KAIRO_SERVE_ROOT uv run python /tmp/kairo-verify-fixture.py "$ROOT"`：

```python
import sys
from pathlib import Path
from kairo.workspace import Workspace
from kairo.knowledge import load_workspace, new_entry, save_workspace
from kairo.knowledge_review import ingest_candidates

root = Path(sys.argv[1])
ws = Workspace.init(root / "demo")


def digest(rid: str, text: str) -> str:
    p = ws.root / f"references/{rid}/digest.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return f"references/{rid}/digest.md"


a = digest("a", "高希彬提出方案。康医通要上线。")
b = digest("b", "康医通下周演示。")
ingest_candidates(
    ws.root, source_kind="digest", path=a, source_text="高希彬提出方案。康医通要上线。",
    drafts=[
        {"title": "高希彬", "quote": "高希彬提出", "description": "项目提出人"},
        {"title": "康医通", "quote": "康医通要上线", "description": "待上线业务系统"},
    ],
)
ingest_candidates(
    ws.root, source_kind="digest", path=b, source_text="康医通下周演示。",
    drafts=[{"title": "康医通", "quote": "康医通下周演示", "description": "待上线业务系统"}],
)
doc, _ = load_workspace(ws.root)
doc.entries.append(new_entry(title="无关条目", scope="workspace", description="本地已确认但不匹配候选"))
save_workspace(ws.root, doc)
```

期望初态：`候选审核 · 1`（康医通，两篇 digest，拟议说明「待上线业务系统」与出处摘录不同）、`目击 · 1`（高希彬，一篇，拟议说明「项目提出人」）、本地 confirmed「无关条目」1 条。

## 路径 → 可判定结果

| # | 路径 | 可判定结果 |
|---|---|---|
| A | 打开 `/knowledge?workspace=demo` | 队列导航含 `候选审核 · 1` 与 `目击 · 1`；候选区列出「康医通」，出处 2 条 |
| B | 点 `目击 · 1` | 目击区列出「高希彬」，带 `采纳到本工作区`、`忽略`、`编辑` |
| C | 目击区「高希彬」点 `采纳到本工作区`，再 GET 页面 | `目击 · 0`；`本地知识` 队列出现「高希彬」confirmed |
| D | 候选区「康医通」点 `忽略`，再 GET 页面 | `候选审核 · 0`；review yaml 中该条 `status: ignored` |
| E | 空态 | 两个队列都空时分别显示 `没有待审核的知识候选。` / `没有目击中的专名。` |
| S1 | GET `/knowledge?workspace=demo`，读多源「康医通」未展开主文再展开出处；再走目击队列读单源「高希彬」 | 未展开主文只有拟议说明（康医通「待上线业务系统」/ 高希彬「项目提出人」），不含出处摘录。单源目击也一样：摘录「高希彬提出」不在 description 旁的 gl-note，只在展开后的出处 details |
| S2 | GET `/knowledge?workspace=demo`，看「康医通」主操作行，展开「合并到已有条目」，不选目标点合并 | 主行只有 `采纳到本工作区` 与 `忽略`；下拉第一项是空值「选择已有条目」，默认不是「无关条目」；未选目标不合并且候选仍 pending |

## 已知不做

批量操作；跨 Topic 汇总。
