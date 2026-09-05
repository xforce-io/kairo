"""Project / Data Source / Task / Run / Artifact。存数在 serve root，不含凭据。"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from kairo.readers import ReadError
from kairo.settings import CONNECTION_TENCENT

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
    kind: str  # spreadsheet | smartsheet | document | smartpage
    purpose: str = ""
    name: str = ""
    reader: str = CONNECTION_TENCENT


class TaskDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    datasource_id: str = ""
    schedule: str = "once"  # once | interval
    interval_hours: int | None = None
    enabled: bool = True
    version: int = 1
    mode: str = "source_snapshot"  # source_snapshot | agent
    prompt: str = ""


class Project(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    topics: list[str] = Field(default_factory=list)  # renamed from workspace_slugs
    workspace_slugs: list[str] | None = None  # deprecated, for backward compat on read
    datasources: list[DataSource] = Field(default_factory=list)
    tasks: list[TaskDef] = Field(default_factory=list)
    # 早期调度版本已写入该字段。当前实现不解释其内容，但在读取及后续
    # Project 编辑时原样保留，避免升级后既有 Project 不可访问或丢失状态。
    schedule_states: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    
    def model_post_init(self, __context) -> None:
        """Migrate workspace_slugs to topics on read."""
        if self.workspace_slugs and not self.topics:
            self.topics = list(self.workspace_slugs)
        # Don't keep both fields populated
        if self.workspace_slugs == self.topics:
            self.workspace_slugs = None


class RunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    task_id: str
    task_name: str
    task_version: int
    datasource_id: str = ""
    datasource_url: str = ""
    datasource_kind: str = ""
    status: str  # running | succeeded | failed
    reason: str | None = None
    artifact_path: str | None = None
    created_at: str = ""
    schema_version: int = 1
    mode: str = "source_snapshot"
    task_snapshot: dict[str, Any] = Field(default_factory=dict)
    provider: str | None = None
    model: str | None = None
    skill_hash: str | None = None
    scope_topics: list[str] | None = None
    scope_datasources: list[str] | None = None
    started_at: str = ""
    finished_at: str | None = None
    scratch_dir: str | None = None
    worker_pid: int | None = None


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
            try:
                items.append(_load_file(path))
            except ProjectError:
                continue
    return items


def _load_file(path: Path) -> Project:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProjectError(f"无法解析 {path}: {exc}") from exc
    try:
        return Project.model_validate(raw)
    except ValidationError as exc:
        raise ProjectError(f"无法解析 {path}: {exc}") from exc


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
            raise ProjectError("Topic slug 不能为空")
        if not workspace_exists(serve, slug):
            raise ProjectError(f"Topic 不存在:{slug}")
        if slug not in cleaned:
            cleaned.append(slug)
    if not cleaned:
        raise ProjectError("至少指定一个 Topic")
    project = get_project(serve, project_id)
    for slug in cleaned:
        if slug not in project.topics:
            project.topics.append(slug)
    return save_project(serve, project)


def unlink_workspace(serve: Path, project_id: str, slug: str) -> Project:
    project = get_project(serve, project_id)
    project.topics = [s for s in project.topics if s != slug]
    return save_project(serve, project)


def set_workspaces(serve: Path, project_id: str, slugs: list[str]) -> Project:
    """一次提交替换关联集合；只接受已存在的 Topic。"""
    seen: list[str] = []
    for raw in slugs:
        slug = (raw or "").strip()
        if not slug:
            continue
        if not workspace_exists(serve, slug):
            raise ProjectError(f"Topic 不存在:{slug}")
        if slug not in seen:
            seen.append(slug)
    project = get_project(serve, project_id)
    project.topics = seen
    return save_project(serve, project)


def datasource_label(ds: DataSource) -> str:
    if (ds.name or "").strip():
        return ds.name.strip()
    if (ds.purpose or "").strip():
        return ds.purpose.strip()
    return ds.reader


def add_datasource(
    serve: Path,
    project_id: str,
    *,
    url: str,
    kind: str | None = None,
    purpose: str = "",
    name: str = "",
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
    if kind and kind != inferred.kind:
        raise ProjectError("数据源类型必须与链接推断结果一致", code="invalid_link")
    if connection_id and connection_id != inferred.connection_id:
        raise ProjectError("数据源连接必须由链接推断", code="invalid_link")
    if reader and reader != inferred.reader:
        raise ProjectError("数据源 Reader 必须由链接推断", code="invalid_link")
    project = get_project(serve, project_id)
    ds = DataSource(
        id=_new_id("ds"),
        connection_id=connection_id or inferred.connection_id,
        url=url.strip(),
        kind=inferred.kind,
        purpose=purpose.strip(),
        name=name.strip(),
        reader=reader or inferred.reader,
    )
    project.datasources.append(ds)
    save_project(serve, project)
    return ds


def edit_datasource(
    serve: Path,
    project_id: str,
    ds_id: str,
    *,
    name: str | None = None,
    purpose: str | None = None,
) -> DataSource:
    project = get_project(serve, project_id)
    ds = _ds(project, ds_id)
    if name is not None:
        ds.name = name.strip()
    if purpose is not None:
        ds.purpose = purpose.strip()
    save_project(serve, project)
    return ds


def remove_datasource(serve: Path, project_id: str, ds_id: str) -> Project:
    project = get_project(serve, project_id)
    before = len(project.datasources)
    project.datasources = [d for d in project.datasources if d.id != ds_id]
    if len(project.datasources) == before:
        raise ProjectError(f"数据源不存在:{ds_id}")
    from kairo.project_materials import drop_cache

    drop_cache(serve, project_id, ds_id)
    return save_project(serve, project)


def _ds(project: Project, ds_id: str) -> DataSource:
    for item in project.datasources:
        if item.id == ds_id:
            return item
    raise ProjectError(f"数据源不存在:{ds_id}")


def read_project_datasource(
    serve: Path, project_id: str, ds_id: str, *, refresh: bool = False
):
    from kairo.project_materials import read_cached_datasource

    return read_cached_datasource(serve, project_id, ds_id, refresh=refresh)


def create_task(
    serve: Path,
    project_id: str,
    *,
    name: str,
    datasource_id: str | None = None,
    prompt: str | None = None,
    schedule: str = "once",
    interval_hours: int | None = None,
) -> TaskDef:
    name = (name or "").strip()
    if not name:
        raise ProjectError("Task 名称不能为空", code="invalid_request")
    if schedule not in ("once", "interval"):
        raise ProjectError(f"未知 schedule:{schedule}", code="invalid_request")
    project = get_project(serve, project_id)
    ds_id = (datasource_id or "").strip()
    if ds_id and prompt is not None:
        raise ProjectError("不能同时指定 Data Source 与 prompt", code="invalid_request")
    if ds_id:
        _ds(project, ds_id)
        mode = "source_snapshot"
        prompt_val = ""
    elif prompt is not None:
        if not str(prompt).strip():
            raise ProjectError("prompt 不能为空", code="invalid_request")
        mode = "agent"
        prompt_val = str(prompt)
        ds_id = ""
    else:
        raise ProjectError("需要 prompt 或 Data Source", code="invalid_request")
    task = TaskDef(
        id=_new_id("tsk"),
        name=name,
        datasource_id=ds_id,
        schedule=schedule,
        interval_hours=interval_hours,
        enabled=True,
        version=1,
        mode=mode,
        prompt=prompt_val,
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
    prompt: str | None = None,
) -> TaskDef:
    project = get_project(serve, project_id)
    task = _task(project, task_id)
    changed = False
    if name is not None:
        name = name.strip()
        if not name:
            raise ProjectError("Task 名称不能为空", code="invalid_request")
        task.name = name
        changed = True
    if schedule is not None:
        if schedule not in ("once", "interval"):
            raise ProjectError(f"未知 schedule:{schedule}", code="invalid_request")
        task.schedule = schedule
        changed = True
    if interval_hours is not None:
        task.interval_hours = interval_hours
        changed = True
    if datasource_id is not None:
        if task.mode == "agent":
            raise ProjectError("agent Task 不能绑定 Data Source", code="invalid_request")
        _ds(project, datasource_id)
        task.datasource_id = datasource_id
        changed = True
    if prompt is not None:
        if task.mode != "agent":
            raise ProjectError("旧 Task 不能改为 prompt", code="invalid_request")
        if not str(prompt).strip():
            raise ProjectError("prompt 不能为空", code="invalid_request")
        task.prompt = str(prompt)
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
        try:
            items.append(reap_run(serve, project_id, path.stem))
        except ProjectError:
            continue
    items.sort(key=lambda r: r.created_at, reverse=True)
    return items


def _load_run_payload(serve: Path, project_id: str, run_id: str) -> dict[str, Any]:
    path = _run_path(serve, project_id, run_id)
    if not path.is_file():
        raise ProjectError(f"Run 不存在:{run_id}", code="not_found")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProjectError(f"无法解析 Run:{exc}", code="not_found") from exc
    version = int(raw.get("schema_version") or 1)
    if version not in (1, 2):
        raise ProjectError("Run 版本不支持", code="unsupported_run")
    return raw


def get_run(serve: Path, project_id: str, run_id: str) -> RunRecord:
    raw = _load_run_payload(serve, project_id, run_id)
    try:
        return RunRecord.model_validate(raw)
    except ValidationError as exc:
        raise ProjectError(f"无法解析 Run:{exc}", code="not_found") from exc


def _save_run(serve: Path, record: RunRecord) -> RunRecord:
    _atomic_json(_run_path(serve, record.project_id, record.id), record.model_dump())
    return record


def reap_run(serve: Path, project_id: str, run_id: str) -> RunRecord:
    record = get_run(serve, project_id, run_id)
    if record.status != "running":
        return record
    pid = record.worker_pid
    alive = False
    if pid:
        try:
            os.kill(pid, 0)
            alive = True
        except OSError:
            alive = False
    if alive:
        return record
    record.status = "failed"
    record.reason = "interrupted"
    record.finished_at = _now()
    record.artifact_path = None
    return _save_run(serve, record)


def read_artifact(serve: Path, project_id: str, run_id: str) -> str:
    run = get_run(serve, project_id, run_id)
    if run.status != "succeeded" or not run.artifact_path:
        raise ProjectError("该 Run 没有 Artifact")
    path = Path(serve) / run.artifact_path
    if not path.is_file():
        raise ProjectError("Artifact 文件缺失")
    return path.read_text(encoding="utf-8")


def run_task(
    serve: Path,
    project_id: str,
    task_id: str,
    *,
    background: bool = False,
    provider=None,
) -> RunRecord:
    project = get_project(serve, project_id)
    task = _task(project, task_id)
    mode = task.mode or "source_snapshot"
    if mode != "agent":
        return _run_source_snapshot(serve, project, task)
    return _run_agent_task(serve, project, task, background=background, provider=provider)


def _run_source_snapshot(serve: Path, project: Project, task: TaskDef) -> RunRecord:
    from kairo.project_materials import read_cached_datasource

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
        content = read_cached_datasource(serve, project.id, ds.id).content
    except ReadError as exc:
        record = RunRecord(
            id=run_id,
            project_id=project.id,
            status="failed",
            reason=exc.code,
            artifact_path=None,
            created_at=created,
            mode="source_snapshot",
            schema_version=1,
            **snapshot,
        )
        return _save_run(serve, record)
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
        mode="source_snapshot",
        schema_version=1,
        **snapshot,
    )
    return _save_run(serve, record)


def select_project_agent():
    from kairo.provider import select_provider

    if os.environ.get("KAIRO_PROJECT_AGENT"):
        name = os.environ["KAIRO_PROJECT_AGENT"]
        from kairo.provider import _BACKENDS, _codex_provider

        if name == "codex":
            candidate = _codex_provider()
        else:
            factory = _BACKENDS.get(name)
            if factory is None:
                return None
            candidate = factory()
        if not getattr(candidate, "supports_project_cli", False):
            return None
        return candidate
    if os.environ.get("KAIRO_STUB"):
        return None
    candidate = select_provider()
    if not getattr(candidate, "supports_project_cli", False):
        return None
    return candidate


def _run_agent_task(
    serve: Path,
    project: Project,
    task: TaskDef,
    *,
    background: bool,
    provider,
) -> RunRecord:
    import hashlib
    import threading

    from kairo.install import skill_source_file
    from kairo.project_materials import scratch_dir

    agent = provider if provider is not None else select_project_agent()
    run_id = _new_id("run")
    created = _now()
    skill_path = skill_source_file()
    skill_text = skill_path.read_text(encoding="utf-8") if skill_path and skill_path.is_file() else ""
    scratch = scratch_dir(serve, project.id, run_id)
    scratch.mkdir(parents=True, exist_ok=True)
    record = RunRecord(
        id=run_id,
        project_id=project.id,
        task_id=task.id,
        task_name=task.name,
        task_version=task.version,
        status="running",
        reason=None,
        artifact_path=None,
        created_at=created,
        schema_version=2,
        mode="agent",
        task_snapshot=task.model_dump(),
        provider=getattr(agent, "name", None) if agent is not None else None,
        model=getattr(agent, "model", None) if agent is not None else None,
        skill_hash=hashlib.sha256(skill_text.encode("utf-8")).hexdigest() if skill_text else None,
        scope_topics=list(project.topics),
        scope_datasources=[d.id for d in project.datasources],
        started_at=created,
        scratch_dir=str(scratch.relative_to(Path(serve))).replace("\\", "/"),
        worker_pid=os.getpid(),
    )
    _save_run(serve, record)
    if agent is None or not getattr(agent, "supports_project_cli", False):
        record.status = "failed"
        record.reason = "provider_unsupported"
        record.finished_at = _now()
        return _save_run(serve, record)
    if background:
        thread = threading.Thread(
            target=_execute_agent_run,
            args=(Path(serve), project.id, record.id, agent),
            daemon=True,
        )
        thread.start()
        return record
    return _execute_agent_run(Path(serve), project.id, record.id, agent)


_INPUT_CITE = re.compile(r"\[([^\]]+)\]\(input:([^)]+)\)")


def _execute_agent_run(serve: Path, project_id: str, run_id: str, agent) -> RunRecord:
    import shutil
    import tempfile

    from kairo.install import skill_source_file
    from kairo.project_materials import finalize_inputs, load_run_inputs, scratch_dir
    from kairo.provider import AgentConfig

    record = get_run(serve, project_id, run_id)
    try:
        import sys

        skill_path = skill_source_file()
        skill_text = skill_path.read_text(encoding="utf-8") if skill_path and skill_path.is_file() else ""
        work = Path(tempfile.mkdtemp(prefix=f"kairo-run-{record.id}-"))
        (work / "SKILL.md").write_text(skill_text, encoding="utf-8")
        cli = f"{sys.executable} -m kairo"
        root = Path(serve).resolve()
        prompt = (
            f"## Kairo Project Run\n"
            f"serve_root: {root}\n"
            f"project_id: {record.project_id}\n"
            f"run_id: {record.id}\n"
            f"cli: {cli}\n"
            f"output_file: artifact.md\n\n"
            f"加载本目录 SKILL.md 的 Project 运行章节。"
            f"必须使用 `{cli}`，不要用 PATH 上可能过期的 `kairo`。"
            f"先 `{cli} project context {record.project_id} --run {record.id} --root {root}` "
            f"获取目录，再 `{cli} project read PROJECT SOURCE --run {record.id} --root {root}` 按需读取。"
            f"禁止 step / re-step / accept / 写 Topic。"
            f"引用材料使用 [标题](input:INPUT_ID)。把最终 Markdown 写入 artifact.md。\n\n"
            f"## Task\n{record.task_snapshot.get('prompt') or ''}\n"
        )
        (work / "_prompt.md").write_text(prompt, encoding="utf-8")
        cache_root = _project_dir(serve, record.project_id) / "cache"
        cache_root.mkdir(parents=True, exist_ok=True)
        scratch = scratch_dir(serve, record.project_id, record.id)
        scratch.mkdir(parents=True, exist_ok=True)
        old_root = os.environ.get("KAIRO_SERVE_ROOT")
        old_path = os.environ.get("PATH")
        os.environ["KAIRO_SERVE_ROOT"] = str(Path(serve).resolve())
        py_bin = str(Path(sys.executable).resolve().parent)
        os.environ["PATH"] = py_bin + os.pathsep + (old_path or "")
        try:
            result = agent.run(
                AgentConfig(
                    persona=skill_text,
                    context=prompt,
                    artifact_dir=work,
                    model=getattr(agent, "model", "") or "",
                    artifact="_provider_last.md",
                    write_dirs=[cache_root, scratch],
                )
            )
        finally:
            if old_root is None:
                os.environ.pop("KAIRO_SERVE_ROOT", None)
            else:
                os.environ["KAIRO_SERVE_ROOT"] = old_root
            if old_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = old_path
        artifact_file = work / "artifact.md"
        if not artifact_file.is_file():
            text = result.result_text if result and result.result_text else ""
            if not text.strip():
                raise ProjectError("产物缺失", code="empty_artifact")
            artifact_file.write_text(text, encoding="utf-8")
        body = artifact_file.read_text(encoding="utf-8")
        if not body.strip():
            raise ProjectError("产物为空", code="empty_artifact")
        from kairo.project_materials import scratch_dir as _scratch_dir, validate_recorded_inputs

        inputs = load_run_inputs(serve, record.project_id, record.id, scratch=True)
        known = {str(item.get("input_id")) for item in inputs}
        cited = {m.group(2) for m in _INPUT_CITE.finditer(body)}
        unknown = cited - known
        if unknown:
            raise ProjectError("引用了未知 input_id", code="invalid_input_ref")
        scratch_folder = Path(record.scratch_dir) if record.scratch_dir else _scratch_dir(
            serve, record.project_id, record.id
        )
        if not scratch_folder.is_absolute():
            scratch_folder = Path(serve) / scratch_folder
        validate_recorded_inputs(serve, record.project_id, record.id, inputs, scratch_folder)
        inputs = finalize_inputs(serve, record.project_id, record.id)
        source_lines = _source_lines(inputs, cited)
        body = body.rstrip() + "\n\n## 来源\n\n" + source_lines + "\n"
        rel = Path(".kairo") / "projects" / record.project_id / "artifacts" / f"{record.id}.md"
        dest = Path(serve) / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body, encoding="utf-8")
        record.status = "succeeded"
        record.reason = None
        record.artifact_path = str(rel).replace("\\", "/")
        record.finished_at = _now()
        record.scratch_dir = None
        _save_run(serve, record)
        shutil.rmtree(work, ignore_errors=True)
        return record
    except Exception as exc:
        record = get_run(serve, record.project_id, record.id)
        record.status = "failed"
        record.reason = getattr(exc, "code", None) or "provider_failed"
        record.artifact_path = None
        record.finished_at = _now()
        return _save_run(serve, record)


def _source_lines(inputs: list[dict[str, Any]], cited: set[str]) -> str:
    if not inputs:
        return "本次未读取项目材料\n"
    lines = []
    for item in inputs:
        iid = str(item.get("input_id"))
        title = item.get("title") or item.get("source_id")
        version = item.get("version") or ""
        extra = "" if iid in cited else " — 已读取，正文未引用"
        lines.append(f"- [{title}](input:{iid}) `{version}`{extra}")
    return "\n".join(lines) + "\n"


def project_to_dict(project: Project) -> dict[str, Any]:
    data = project.model_dump()
    # Keep backward compat field
    data["topic_slugs"] = list(data.get("topics") or [])
    data["workspace_slugs"] = data.get("topics") or []
    _assert_no_secrets(data)
    return data
