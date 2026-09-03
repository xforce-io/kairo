"""Project / Data Source / Task / Run / Artifact。存数在 serve root，不含凭据。"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from kairo.readers import ReadError, read_datasource
from kairo.settings import CONNECTION_TENCENT, get_connection

_FORBIDDEN_KEYS = frozenset({"token", "api_key", "password", "secret", "credential"})


class ProjectError(ValueError):
    """Project 域操作非法。"""

    def __init__(self, message: str, *, code: str | None = None):
        self.code = code
        super().__init__(message)


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class DataSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    connection_id: str = CONNECTION_TENCENT
    url: str
    kind: str  # spreadsheet | smartsheet
    purpose: str = ""
    reader: str = CONNECTION_TENCENT


class TaskDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    datasource_id: str
    schedule: str = "once"  # once | interval
    interval_hours: int | None = None
    enabled: bool = True
    version: int = 1


class Project(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    workspace_slugs: list[str] = Field(default_factory=list)
    datasources: list[DataSource] = Field(default_factory=list)
    tasks: list[TaskDef] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


class RunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    task_id: str
    task_name: str
    task_version: int
    datasource_id: str
    datasource_url: str
    datasource_kind: str
    status: str  # succeeded | failed
    reason: str | None = None
    artifact_path: str | None = None
    created_at: str = ""


def _projects_root(serve: Path) -> Path:
    return Path(serve) / ".kairo" / "projects"


def _project_dir(serve: Path, project_id: str) -> Path:
    return _projects_root(serve) / project_id


def _project_path(serve: Path, project_id: str) -> Path:
    return _project_dir(serve, project_id) / "project.json"


def _run_path(serve: Path, project_id: str, run_id: str) -> Path:
    return _project_dir(serve, project_id) / "runs" / f"{run_id}.json"


def _artifact_path(serve: Path, project_id: str, run_id: str) -> Path:
    return _project_dir(serve, project_id) / "artifacts" / f"{run_id}.md"


def _atomic_json(path: Path, payload: dict) -> None:
    _assert_no_secrets(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)
    except Exception as exc:
        temp.unlink(missing_ok=True)
        raise ProjectError(f"保存失败:{exc}") from exc


def _assert_no_secrets(obj: Any) -> None:
    if isinstance(obj, dict):
        for key, val in obj.items():
            if str(key).lower() in _FORBIDDEN_KEYS:
                raise ProjectError("Project 存数不得包含凭据字段")
            _assert_no_secrets(val)
    elif isinstance(obj, list):
        for item in obj:
            _assert_no_secrets(item)


def workspace_exists(serve: Path, slug: str) -> bool:
    return (Path(serve) / slug / "constitution.yaml").is_file()


def list_projects(serve: Path) -> list[Project]:
    root = _projects_root(serve)
    if not root.is_dir():
        return []
    items: list[Project] = []
    for child in sorted(root.iterdir()):
        path = child / "project.json"
        if path.is_file():
            items.append(_load_file(path))
    return items


def _load_file(path: Path) -> Project:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProjectError(f"无法解析 {path}: {exc}") from exc
    return Project.model_validate(raw)


def get_project(serve: Path, project_id: str) -> Project:
    path = _project_path(serve, project_id)
    if not path.is_file():
        raise ProjectError(f"Project 不存在:{project_id}")
    return _load_file(path)


def save_project(serve: Path, project: Project) -> Project:
    project.updated_at = _now()
    _atomic_json(_project_path(serve, project.id), project.model_dump())
    return project


def create_project(serve: Path, name: str) -> Project:
    name = (name or "").strip()
    if not name:
        raise ProjectError("名称不能为空")
    project = Project(id=_new_id("prj"), name=name, created_at=_now(), updated_at=_now())
    save_project(serve, project)
    return project


def edit_project(serve: Path, project_id: str, *, name: str | None = None) -> Project:
    project = get_project(serve, project_id)
    if name is not None:
        name = name.strip()
        if not name:
            raise ProjectError("名称不能为空")
        project.name = name
    return save_project(serve, project)


def link_workspace(serve: Path, project_id: str, slug: str) -> Project:
    return link_workspaces(serve, project_id, [slug])


def link_workspaces(serve: Path, project_id: str, slugs: list[str]) -> Project:
    """校验全部 slug 后再写入，失败不留下部分关联。"""
    cleaned: list[str] = []
    for raw in slugs:
        slug = (raw or "").strip()
        if not slug:
            raise ProjectError("workspace slug 不能为空")
        if not workspace_exists(serve, slug):
            raise ProjectError(f"workspace 不存在:{slug}")
        if slug not in cleaned:
            cleaned.append(slug)
    if not cleaned:
        raise ProjectError("至少指定一个 workspace")
    project = get_project(serve, project_id)
    for slug in cleaned:
        if slug not in project.workspace_slugs:
            project.workspace_slugs.append(slug)
    return save_project(serve, project)


def unlink_workspace(serve: Path, project_id: str, slug: str) -> Project:
    project = get_project(serve, project_id)
    project.workspace_slugs = [s for s in project.workspace_slugs if s != slug]
    return save_project(serve, project)


def set_workspaces(serve: Path, project_id: str, slugs: list[str]) -> Project:
    """一次提交替换关联集合；只接受已存在的 workspace。"""
    seen: list[str] = []
    for raw in slugs:
        slug = (raw or "").strip()
        if not slug:
            continue
        if not workspace_exists(serve, slug):
            raise ProjectError(f"workspace 不存在:{slug}")
        if slug not in seen:
            seen.append(slug)
    project = get_project(serve, project_id)
    project.workspace_slugs = seen
    return save_project(serve, project)


def add_datasource(
    serve: Path,
    project_id: str,
    *,
    url: str,
    kind: str | None = None,
    purpose: str = "",
    connection_id: str | None = None,
    reader: str | None = None,
) -> DataSource:
    from kairo.readers import ReadError, infer_source

    try:
        inferred = infer_source(url)
    except ReadError as exc:
        raise ProjectError(str(exc), code=exc.code) from exc
    if not inferred.live:
        raise ProjectError(str(f"{inferred.label} Reader 尚未接入"), code="unsupported_reader")
    internal_kind = inferred.kind
    if kind in ("spreadsheet", "smartsheet"):
        internal_kind = kind
    project = get_project(serve, project_id)
    ds = DataSource(
        id=_new_id("ds"),
        connection_id=connection_id or inferred.connection_id,
        url=url.strip(),
        kind=internal_kind,
        purpose=purpose.strip(),
        reader=reader or inferred.reader,
    )
    project.datasources.append(ds)
    save_project(serve, project)
    return ds


def remove_datasource(serve: Path, project_id: str, ds_id: str) -> Project:
    project = get_project(serve, project_id)
    before = len(project.datasources)
    project.datasources = [d for d in project.datasources if d.id != ds_id]
    if len(project.datasources) == before:
        raise ProjectError(f"数据源不存在:{ds_id}")
    return save_project(serve, project)


def _ds(project: Project, ds_id: str) -> DataSource:
    for item in project.datasources:
        if item.id == ds_id:
            return item
    raise ProjectError(f"数据源不存在:{ds_id}")


def read_project_datasource(serve: Path, project_id: str, ds_id: str) -> str:
    project = get_project(serve, project_id)
    ds = _ds(project, ds_id)
    conn = get_connection(ds.connection_id)
    return read_datasource(ds.url, ds.kind, ds.reader, conn)


def create_task(
    serve: Path,
    project_id: str,
    *,
    name: str,
    datasource_id: str,
    schedule: str = "once",
    interval_hours: int | None = None,
) -> TaskDef:
    name = (name or "").strip()
    if not name:
        raise ProjectError("Task 名称不能为空")
    if schedule not in ("once", "interval"):
        raise ProjectError(f"未知 schedule:{schedule}")
    project = get_project(serve, project_id)
    _ds(project, datasource_id)
    task = TaskDef(
        id=_new_id("tsk"),
        name=name,
        datasource_id=datasource_id,
        schedule=schedule,
        interval_hours=interval_hours,
        enabled=True,
        version=1,
    )
    project.tasks.append(task)
    save_project(serve, project)
    return task


def _task(project: Project, task_id: str) -> TaskDef:
    for item in project.tasks:
        if item.id == task_id:
            return item
    raise ProjectError(f"Task 不存在:{task_id}")


def edit_task(
    serve: Path,
    project_id: str,
    task_id: str,
    *,
    name: str | None = None,
    schedule: str | None = None,
    interval_hours: int | None = None,
    enabled: bool | None = None,
    datasource_id: str | None = None,
) -> TaskDef:
    project = get_project(serve, project_id)
    task = _task(project, task_id)
    changed = False
    if name is not None:
        name = name.strip()
        if not name:
            raise ProjectError("Task 名称不能为空")
        task.name = name
        changed = True
    if schedule is not None:
        if schedule not in ("once", "interval"):
            raise ProjectError(f"未知 schedule:{schedule}")
        task.schedule = schedule
        changed = True
    if interval_hours is not None:
        task.interval_hours = interval_hours
        changed = True
    if datasource_id is not None:
        _ds(project, datasource_id)
        task.datasource_id = datasource_id
        changed = True
    if enabled is not None:
        task.enabled = enabled
        changed = True
    if changed:
        task.version += 1
    save_project(serve, project)
    return task


def list_runs(serve: Path, project_id: str) -> list[RunRecord]:
    folder = _project_dir(serve, project_id) / "runs"
    if not folder.is_dir():
        return []
    items = []
    for path in sorted(folder.glob("*.json")):
        items.append(RunRecord.model_validate(json.loads(path.read_text(encoding="utf-8"))))
    items.sort(key=lambda r: r.created_at, reverse=True)
    return items


def get_run(serve: Path, project_id: str, run_id: str) -> RunRecord:
    path = _run_path(serve, project_id, run_id)
    if not path.is_file():
        raise ProjectError(f"Run 不存在:{run_id}")
    return RunRecord.model_validate(json.loads(path.read_text(encoding="utf-8")))


def read_artifact(serve: Path, project_id: str, run_id: str) -> str:
    run = get_run(serve, project_id, run_id)
    if run.status != "succeeded" or not run.artifact_path:
        raise ProjectError("该 Run 没有 Artifact")
    path = Path(serve) / run.artifact_path
    if not path.is_file():
        raise ProjectError("Artifact 文件缺失")
    return path.read_text(encoding="utf-8")


def run_task(serve: Path, project_id: str, task_id: str) -> RunRecord:
    project = get_project(serve, project_id)
    task = _task(project, task_id)
    ds = _ds(project, task.datasource_id)
    run_id = _new_id("run")
    created = _now()
    snapshot = {
        "task_id": task.id,
        "task_name": task.name,
        "task_version": task.version,
        "datasource_id": ds.id,
        "datasource_url": ds.url,
        "datasource_kind": ds.kind,
    }
    try:
        content = read_datasource(ds.url, ds.kind, ds.reader, get_connection(ds.connection_id))
    except ReadError as exc:
        record = RunRecord(
            id=run_id,
            project_id=project.id,
            status="failed",
            reason=exc.code,
            artifact_path=None,
            created_at=created,
            **snapshot,
        )
        _atomic_json(_run_path(serve, project.id, run_id), record.model_dump())
        return record
    rel = Path(".kairo") / "projects" / project.id / "artifacts" / f"{run_id}.md"
    body = (
        f"# {task.name}\n\n"
        f"- Run: `{run_id}`\n"
        f"- Task version: {task.version}\n"
        f"- Data source: {ds.url} ({ds.kind})"
        + (f" — {ds.purpose}" if ds.purpose else "")
        + "\n\n## Input\n\n"
        + content.strip()
        + "\n"
    )
    dest = Path(serve) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(body, encoding="utf-8")
    record = RunRecord(
        id=run_id,
        project_id=project.id,
        status="succeeded",
        reason=None,
        artifact_path=str(rel).replace("\\", "/"),
        created_at=created,
        **snapshot,
    )
    _atomic_json(_run_path(serve, project.id, run_id), record.model_dump())
    return record


def project_to_dict(project: Project) -> dict[str, Any]:
    data = project.model_dump()
    data["topic_slugs"] = list(data.get("workspace_slugs") or [])
    _assert_no_secrets(data)
    return data
