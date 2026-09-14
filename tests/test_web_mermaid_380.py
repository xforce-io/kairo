"""#380: mermaid 占位进入 Topic / Ref / Artifact 预览。"""

import json

from fastapi.testclient import TestClient

from kairo.projects import create_project
from kairo.web.server import create_app
from kairo.workspace import Workspace

_FLOW = "```mermaid\nflowchart LR\nA[现场] --> B[IOT]\n```\n"


def _client(root):
    return TestClient(create_app(root))


def test_topic_preview_uses_mermaid_placeholder(tmp_path):
    ws = Workspace.init(tmp_path / "ws", topic="t")
    (ws.root / "understanding.md").write_text(f"# 架构\n\n{_FLOW}\n两段。\n", encoding="utf-8")
    page = _client(tmp_path).get("/w/ws", headers={"accept-language": "zh"})
    assert page.status_code == 200
    assert 'class="doc-mermaid"' in page.text
    assert "language-mermaid" not in page.text
    assert "mermaid_render.js" in page.text
    assert "流程图无法绘制" in page.text


def test_ref_digest_preview_uses_mermaid_placeholder(tmp_path):
    ws = Workspace.init(tmp_path / "ws", topic="t")
    src = tmp_path / "note.txt"
    src.write_text("笔记", encoding="utf-8")
    ws.add([src], ref_id="ref-mmd", title="笔记")
    digest = ws.references_dir() / "ref-mmd" / "digest.md"
    digest.parent.mkdir(parents=True, exist_ok=True)
    digest.write_text(f"# 纪要\n\n{_FLOW}\n", encoding="utf-8")
    page = _client(tmp_path).get("/w/ws/ref/ref-mmd")
    assert page.status_code == 200
    assert 'class="doc-mermaid"' in page.text
    assert "language-mermaid" not in page.text


def test_artifact_preview_uses_mermaid_placeholder(tmp_path):
    project = create_project(tmp_path, "能源项目")
    run_id = "run-mmd"
    rel = f".kairo/projects/{project.id}/artifacts/{run_id}.md"
    (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / rel).write_text(f"# 周报\n\n{_FLOW}\n", encoding="utf-8")
    run_path = tmp_path / ".kairo" / "projects" / project.id / "runs" / f"{run_id}.json"
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
    page = _client(tmp_path).get(f"/projects/{project.id}/runs/{run_id}")
    assert page.status_code == 200
    assert 'class="doc-mermaid"' in page.text
    assert "language-mermaid" not in page.text
