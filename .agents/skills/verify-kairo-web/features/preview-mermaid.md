# 预览 mermaid 画成图（#380）

## 入口

- Topic 活 target：`/w/<slug>` 阅读区
- Ref digest：`/w/<slug>/ref/<ref_id>` 换入阅读区后的正文
- Artifact：`/projects/<project_id>/runs/<run_id>`

不触发 Run，不碰现网 `~/kairo`。

## Fixture

```python
# uv run python /tmp/kairo-verify-fixture-mermaid.py "$ROOT"
import json
import sys
from pathlib import Path
from kairo.projects import create_project
from kairo.workspace import Workspace

root = Path(sys.argv[1])
ws = Workspace.init(root / "demo", topic="demo")
good = "```mermaid\nflowchart LR\nA[现场] --> B[IOT]\n```\n"
bad = "```mermaid\nthis is not a diagram\n```\n"
(ws.root / "understanding.md").write_text(
    "# 架构\n\n" + good + "\n两段边界。\n\n# 坏图\n\n" + bad + "\n结尾段。\n",
    encoding="utf-8",
)
src = root / "note.txt"
src.write_text("笔记", encoding="utf-8")
ws.add([src], ref_id="ref-mmd", title="笔记")
digest = ws.references_dir() / "ref-mmd" / "digest.md"
digest.parent.mkdir(parents=True, exist_ok=True)
digest.write_text("# 纪要\n\n" + good, encoding="utf-8")

project = create_project(root, "能源项目")
run_id = "run-mmd"
rel = f".kairo/projects/{project.id}/artifacts/{run_id}.md"
(root / rel).parent.mkdir(parents=True, exist_ok=True)
(root / rel).write_text("# 周报\n\n" + good, encoding="utf-8")
run_path = root / ".kairo" / "projects" / project.id / "runs" / f"{run_id}.json"
run_path.parent.mkdir(parents=True, exist_ok=True)
run_path.write_text(
    json.dumps(
        {
            "id": run_id,
            "project_id": project.id,
            "task_id": "tsk-1",
            "task_name": "周报",
            "task_version": 1,
            "status": "succeeded",
            "reason": None,
            "artifact_path": rel,
            "created_at": "2026-09-14T12:00:00+00:00",
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
print(project.id)
```

记下脚本打印的 `project_id`。

## 路径 → 可判定结果

| # | 路径 | 可判定结果 |
|---|---|---|
| A | GET `/w/demo`（中文） | 阅读区有 mermaid SVG（节点可见「现场」或「IOT」）；`flowchart LR` 不是该块主展示；另有恰好 1 个 `.doc-mermaid-error` / 「流程图无法绘制」；「架构」「两段边界」「结尾段」仍在 |
| B | GET `/w/demo/ref/ref-mmd` | 阅读区 digest 出图，不是 `flowchart` 源码主展示 |
| C | GET `/projects/<project_id>/runs/run-mmd` | Artifact 正文出图，不是 `flowchart` 源码主展示 |
