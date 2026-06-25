# Kairo Web Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 kairo 加一个轻量本地 Web Console(`kairo serve <root>`),用浏览器统管本地多个 workspace —— dashboard 总览、产物预览、reference/corpus 管理、从界面触发 step 并看实时进度。

**Architecture:** web 作为独立可选子包 `src/kairo/web/`(可选依赖 `kairo[web]`),FastAPI + Jinja + HTMX,服务端渲染、无 node 构建链。web 层只做"调度 + 呈现":读数据复用 `Workspace`/`models`/`corpus`,step 以子进程跑 `kairo step` + SSE 转发 stdout,完成后读 `state.json` 刷新。core 仅新增 `serve` 命令、`__main__.py` 与一个只读的 `engine.pending()` 助手。

**Tech Stack:** Python 3.11+,FastAPI,uvicorn,Jinja2,markdown-it-py,python-multipart,HTMX(vendored 单文件)。测试 pytest + FastAPI `TestClient`(需 httpx)。

## Global Constraints

- Python `>=3.11`(`pyproject.toml` requires-python,不降低)。
- web 全部依赖只进 `[project.optional-dependencies] web`,**core 运行时依赖不新增**;不装 `kairo[web]` 的用户零影响。
- core 业务逻辑零重写;唯一允许的 core 改动:`cli.py` 加 `serve` 命令、新增 `src/kairo/__main__.py`、`engine.py` 抽 `_build_rules` + 加只读 `pending()`(不改 `step`/`re_step`/`accept` 的现有行为)。
- 不引入 node/npm/vite/前端构建链;静态资源为 vendored 文件(htmx 单文件 + 手写 css)。
- 所有源码文件头部沿用现有风格:`"""<中文一句话职责>。"""` + `from __future__ import annotations`。
- 写操作(add/corpus/accept/step)一律调用 core,web 不重写逻辑。
- 单用户本地,无鉴权;任务状态纯内存(server 重启丢运行中任务,可接受)。

---

## File Structure

```
pyproject.toml                       # 改:加 [project.optional-dependencies] web + dev 加 httpx
src/kairo/
├── __main__.py                      # 新:python -m kairo 入口(子进程 step 用)
├── engine.py                        # 改:抽 _build_rules;加 pending(ws)(只读)
├── cli.py                           # 改:加 serve 命令
└── web/
    ├── __init__.py                  # 新:空(子包标记)
    ├── render.py                    # 新:markdown → html
    ├── discovery.py                 # 新:WorkspaceSummary + scan_workspaces(root)
    ├── tasks.py                     # 新:TaskRegistry + StepTask + stream_events
    ├── views.py                     # 新:APIRouter,全部路由 + 渲染
    ├── server.py                    # 新:create_app(root) + run(root, port)
    ├── templates/                   # 新:base/dashboard/workspace/_doc/_ref/_step
    └── static/                      # 新:htmx.min.js + app.css
tests/
├── test_engine_pending.py          # 新
├── test_web_discovery.py           # 新
├── test_web_render.py              # 新
├── test_web_api.py                 # 新(dashboard / workspace view / doc / ref)
├── test_web_write.py               # 新(add ref / corpus / accept)
└── test_web_tasks.py               # 新(TaskRegistry + stream_events + step 端点)
```

每个文件单一职责:`discovery` 只算摘要、`render` 只转 markdown、`tasks` 只管子进程与 SSE、`views` 只接路由调上述模块、`server` 只组装 app。

---

## Task 1: 核心只读助手 engine.pending()

dashboard 的 stale 计数要复用真实规则判定(不能另写一套)。`rule.discover(state)` 与 `item.is_stale(state)` 都是纯函数、不碰 provider(provider 只在 `item.run` 用),所以可用 `provider=None` 安全枚举 stale 项。抽 `_build_rules` 让 `step` 与 `pending` 共用同一份规则列表,保持 DRY。

**Files:**
- Modify: `src/kairo/engine.py`
- Test: `tests/test_engine_pending.py`

**Interfaces:**
- Consumes: `Workspace`(`ws.read_state()`, `ws.constitution`),`rules.TransformRule/NormalizeRule/DigestRule/ComposeRule`,`rules.WorkItem`
- Produces:
  - `engine._build_rules(ws, provider) -> list`(内部助手,返回规则实例列表)
  - `engine.pending(ws) -> list[rules.WorkItem]`(当前 stale 的 WorkItem 列表;只读,不写 state、不跑 provider)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_engine_pending.py
import pytest
from kairo.engine import pending, step
from kairo.provider import select_provider
from kairo.workspace import Workspace


def test_pending_counts_stale_then_empty_after_step(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = Workspace.init(tmp_path, topic="t")
    (tmp_path / "m.txt").write_text("会议内容")
    ws.add([tmp_path / "m.txt"])
    # step 前:digest + 两个 target 待办 → 有 stale
    assert len(pending(ws)) > 0
    step(ws, select_provider())
    # 收敛后:无 stale
    assert pending(ws) == []


def test_pending_does_not_mutate_state(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = Workspace.init(tmp_path, topic="t")
    (tmp_path / "m.txt").write_text("x")
    ws.add([tmp_path / "m.txt"])
    before = ws.state_path.read_text()
    pending(ws)
    assert ws.state_path.read_text() == before  # 只读,不落盘
```

- [ ] **Step 2: Run test to verify it fails**

Run: `KAIRO_STUB=1 uv run pytest tests/test_engine_pending.py -v`
Expected: FAIL with `ImportError: cannot import name 'pending'`

- [ ] **Step 3: Refactor engine to extract `_build_rules` and add `pending`**

把 `engine.py` 中 `step()` 里构造规则列表的两段抽成 `_build_rules`,并让 `step` 改用它(行为不变),新增只读 `pending`:

```python
# src/kairo/engine.py — 在 import 段下方、step() 之上新增:
def _build_rules(ws, provider) -> list:
    """构造调和规则列表(transform 声明驱动 + Normalize/Digest/Compose)。
    discover/is_stale 不碰 provider,故 pending() 可传 provider=None 只读枚举。"""
    transform_rules = [
        TransformRule(ws, t.consumes, t.produces, t.backend)
        for t in ws.constitution.transforms
    ]
    return [
        *transform_rules,
        NormalizeRule(ws, provider),
        DigestRule(ws, provider),
        ComposeRule(ws, provider),
    ]


def pending(ws) -> list:
    """当前 stale 的 WorkItem(只读:不跑 provider、不写 state)。dashboard 算待办数用。"""
    state = ws.read_state()
    items = []
    for rule in _build_rules(ws, None):
        items.extend(item for item in rule.discover(state) if item.is_stale(state))
    return items
```

然后把 `step()` 体内原有的 `transform_rules = [...]` 与 `rules = [...]` 两段替换为:

```python
    rules = _build_rules(ws, provider)
```

(其余 `step` 逻辑、`re_step`、`accept` 一字不改。)

- [ ] **Step 4: Run tests to verify they pass (含回归)**

Run: `KAIRO_STUB=1 uv run pytest tests/test_engine_pending.py tests/test_engine.py tests/test_cli.py -v`
Expected: PASS(新测试通过 + 既有 engine/cli 测试全绿,证明 `step` 行为不变)

- [ ] **Step 5: Commit**

```bash
git add src/kairo/engine.py tests/test_engine_pending.py
git commit -m "feat(engine): 抽 _build_rules + 加只读 pending() 供 web dashboard 算 stale (#35)"
```

---

## Task 2: web 子包 + 可选依赖 + markdown 渲染

建立 `kairo.web` 子包与可选依赖,落地最小可测单元 `render.py`。

**Files:**
- Modify: `pyproject.toml`
- Create: `src/kairo/web/__init__.py`
- Create: `src/kairo/web/render.py`
- Test: `tests/test_web_render.py`

**Interfaces:**
- Produces: `kairo.web.render.render_markdown(text: str) -> str`(CommonMark + 表格 → HTML 字符串)

- [ ] **Step 1: 加可选依赖 + dev 测试依赖**

`pyproject.toml`:在 `[project]` 之后新增依赖组,并给 dev 加 `httpx`(TestClient 需要):

```toml
[project.optional-dependencies]
web = [
    "fastapi>=0.110",
    "uvicorn>=0.29",
    "jinja2>=3.1",
    "markdown-it-py>=3.0",
    "python-multipart>=0.0.9",
]

[dependency-groups]
dev = ["pytest>=8", "httpx>=0.27"]
```

(原 `[dependency-groups] dev = ["pytest>=8"]` 整行替换为上面带 httpx 的版本。)

- [ ] **Step 2: 安装 web + dev 依赖**

Run: `uv sync --extra web`
Expected: 成功安装 fastapi/uvicorn/jinja2/markdown-it-py/python-multipart/httpx

- [ ] **Step 3: Write the failing test**

```python
# tests/test_web_render.py
from kairo.web.render import render_markdown


def test_render_heading_and_paragraph():
    html = render_markdown("# 标题\n\n正文一段。")
    assert "<h1>" in html and "标题" in html
    assert "<p>" in html


def test_render_table():
    html = render_markdown("| a | b |\n|---|---|\n| 1 | 2 |")
    assert "<table>" in html
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run pytest tests/test_web_render.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kairo.web'`

- [ ] **Step 5: Create package + render module**

```python
# src/kairo/web/__init__.py
"""kairo web console(独立可选子包,kairo[web])。"""
```

```python
# src/kairo/web/render.py
"""markdown → html(产物预览用)。"""

from __future__ import annotations

from markdown_it import MarkdownIt

_md = MarkdownIt("commonmark", {"html": False, "linkify": True}).enable("table")


def render_markdown(text: str) -> str:
    """渲染 markdown 为 HTML;禁用原始 HTML(防注入),开表格 + 自动链接。"""
    return _md.render(text)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_web_render.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/kairo/web/__init__.py src/kairo/web/render.py tests/test_web_render.py
git commit -m "feat(web): 子包骨架 + 可选依赖 kairo[web] + markdown 渲染 (#35)"
```

---

## Task 3: workspace 发现层

扫 `<root>` 下一层,凡含 `constitution.yaml` 且可 `Workspace.open` 的子目录即 workspace,产出轻量摘要。

**Files:**
- Create: `src/kairo/web/discovery.py`
- Test: `tests/test_web_discovery.py`

**Interfaces:**
- Consumes: `kairo.workspace.Workspace`(`open`/`constitution`/`read_state`/`list_reference_ids`/`read_manifest`),`kairo.engine.pending`
- Produces:
  - `discovery.WorkspaceSummary`(dataclass:`slug, topic, path, ref_count, stream_count, corpus_count, blocked_count, stale_count`)
  - `discovery.scan_workspaces(root: Path) -> list[WorkspaceSummary]`(按 slug 排序)
  - `discovery.summarize(ws: Workspace) -> WorkspaceSummary`(单个 workspace 摘要)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_web_discovery.py
from kairo.web.discovery import scan_workspaces
from kairo.workspace import Workspace


def _mk(root, name, topic):
    ws = Workspace.init(root / name, topic=topic)
    return ws


def test_scan_finds_workspaces_sorted(tmp_path):
    _mk(tmp_path, "b-ws", "beta")
    _mk(tmp_path, "a-ws", "alpha")
    (tmp_path / "not-a-ws").mkdir()  # 无 constitution.yaml,跳过
    out = scan_workspaces(tmp_path)
    assert [s.slug for s in out] == ["a-ws", "b-ws"]
    assert out[0].topic == "alpha"


def test_summary_counts_refs_and_classes(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = _mk(tmp_path, "ws", "t")
    (tmp_path / "m.txt").write_text("会议")
    cdir = tmp_path / "corpus_src"
    cdir.mkdir()
    (cdir / "x.md").write_text("基线")
    ws.add([tmp_path / "m.txt"])              # stream
    ws.add([cdir], source_class="corpus")     # corpus tree
    out = scan_workspaces(tmp_path)
    s = next(x for x in out if x.slug == "ws")
    assert s.ref_count == 2
    assert s.stream_count == 1 and s.corpus_count == 1
    assert s.stale_count > 0  # step 前有待办


def test_summary_stale_zero_after_step(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    from kairo.engine import step
    from kairo.provider import select_provider
    ws = _mk(tmp_path, "ws", "t")
    (tmp_path / "m.txt").write_text("x")
    ws.add([tmp_path / "m.txt"])
    step(ws, select_provider())
    s = scan_workspaces(tmp_path)[0]
    assert s.stale_count == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `KAIRO_STUB=1 uv run pytest tests/test_web_discovery.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kairo.web.discovery'`

- [ ] **Step 3: Write discovery module**

```python
# src/kairo/web/discovery.py
"""workspace 发现层:扫父目录 → 各 workspace 轻量摘要(dashboard 用,不读正文)。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kairo.engine import pending
from kairo.workspace import Workspace, WorkspaceNotFound


@dataclass
class WorkspaceSummary:
    slug: str
    topic: str
    path: str
    ref_count: int
    stream_count: int
    corpus_count: int
    blocked_count: int
    stale_count: int


def summarize(ws: Workspace) -> WorkspaceSummary:
    con = ws.constitution
    state = ws.read_state()
    stream = corpus = 0
    for ref_id in ws.list_reference_ids():
        cls = ws.read_manifest(ref_id).source_class
        sc = con.source_classes.get(cls)
        if sc is not None and not sc.fold:
            corpus += 1
        else:
            stream += 1
    blocked = sum(1 for p in state.products.values() if p.status == "blocked")
    blocked += sum(1 for t in state.targets.values() if t.status == "blocked")
    return WorkspaceSummary(
        slug=ws.root.name,
        topic=con.topic,
        path=str(ws.root),
        ref_count=stream + corpus,
        stream_count=stream,
        corpus_count=corpus,
        blocked_count=blocked,
        stale_count=len(pending(ws)),
    )


def scan_workspaces(root: Path) -> list[WorkspaceSummary]:
    """扫 root 下一层子目录,凡含 constitution.yaml 且可打开者即 workspace。"""
    root = Path(root)
    out: list[WorkspaceSummary] = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        if not (d / "constitution.yaml").exists():
            continue
        try:
            ws = Workspace.open(d)
        except WorkspaceNotFound:
            continue
        out.append(summarize(ws))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `KAIRO_STUB=1 uv run pytest tests/test_web_discovery.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/kairo/web/discovery.py tests/test_web_discovery.py
git commit -m "feat(web): workspace 发现层 + 轻量摘要(stale/blocked/分类计数) (#35)"
```

---

## Task 4: app 工厂 + dashboard + 静态资源

组装 FastAPI app(持有 root + 模板 + 静态 + 任务注册表),落地 `/healthz` 与 dashboard `GET /`。

**Files:**
- Create: `src/kairo/web/server.py`
- Create: `src/kairo/web/views.py`
- Create: `src/kairo/web/templates/base.html`
- Create: `src/kairo/web/templates/dashboard.html`
- Create: `src/kairo/web/static/app.css`
- Create: `src/kairo/web/static/htmx.min.js`
- Test: `tests/test_web_api.py`

**Interfaces:**
- Consumes: `discovery.scan_workspaces`
- Produces:
  - `server.create_app(root: Path) -> fastapi.FastAPI`(`app.state.root`/`app.state.templates`/`app.state.registry` 就位,挂 `/static`,含 `views.router`)
  - `server.run(root: Path, port: int = 8000) -> None`(uvicorn 启动)
  - `views.router`(APIRouter):`GET /healthz` → `{"ok": True}`;`GET /` → dashboard HTML

- [ ] **Step 1: Write the failing test**

```python
# tests/test_web_api.py
from fastapi.testclient import TestClient

from kairo.web.server import create_app
from kairo.workspace import Workspace


def _client(root):
    return TestClient(create_app(root))


def test_healthz(tmp_path):
    r = _client(tmp_path).get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_dashboard_lists_workspaces(tmp_path):
    Workspace.init(tmp_path / "alpha-ws", topic="阿尔法")
    Workspace.init(tmp_path / "beta-ws", topic="贝塔")
    r = _client(tmp_path).get("/")
    assert r.status_code == 200
    assert "alpha-ws" in r.text and "beta-ws" in r.text
    assert "阿尔法" in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_web_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kairo.web.server'`

- [ ] **Step 3: Create static assets**

下载 htmx 单文件(无构建链):

```bash
curl -L https://unpkg.com/htmx.org@2.0.3/dist/htmx.min.js -o src/kairo/web/static/htmx.min.js
```

```css
/* src/kairo/web/static/app.css */
:root { --fg:#1a1a1a; --muted:#777; --line:#e3e3e3; --warn:#c0392b; --accent:#2d6cdf; }
* { box-sizing: border-box; }
body { font: 15px/1.6 -apple-system, "Segoe UI", sans-serif; color: var(--fg); margin: 0; }
header.top { padding: 12px 20px; border-bottom: 1px solid var(--line); display: flex; gap: 16px; align-items: baseline; }
header.top a { color: inherit; text-decoration: none; font-weight: 600; }
.muted { color: var(--muted); }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 14px; padding: 20px; }
.card { border: 1px solid var(--line); border-radius: 10px; padding: 14px; text-decoration: none; color: inherit; }
.card:hover { border-color: var(--accent); }
.card h3 { margin: 0 0 6px; }
.badge { display: inline-block; font-size: 12px; padding: 1px 7px; border-radius: 10px; background: #f0f0f0; margin-right: 6px; }
.badge.warn { background: #fdecea; color: var(--warn); }
.layout { display: grid; grid-template-columns: 240px 1fr 280px; height: calc(100vh - 49px); }
.layout > * { overflow: auto; padding: 14px; }
.nav-doc { display: block; padding: 4px 6px; border-radius: 6px; color: inherit; text-decoration: none; }
.nav-doc:hover { background: #f4f4f4; }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; background: #bbb; }
.dot.stale { background: #e0a200; } .dot.blocked { background: var(--warn); }
.panel button { display: block; width: 100%; margin: 6px 0; padding: 8px; cursor: pointer; }
#step-log { background: #111; color: #c8e6c9; font: 12px/1.5 monospace; padding: 10px; border-radius: 8px; white-space: pre-wrap; max-height: 320px; overflow: auto; }
```

- [ ] **Step 4: Create templates**

```html
<!-- src/kairo/web/templates/base.html -->
<!doctype html>
<html lang="zh">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}kairo console{% endblock %}</title>
  <link rel="stylesheet" href="/static/app.css">
  <script src="/static/htmx.min.js"></script>
</head>
<body>
  <header class="top">
    <a href="/">kairo console</a>
    <span class="muted">{% block subtitle %}{% endblock %}</span>
  </header>
  {% block body %}{% endblock %}
</body>
</html>
```

```html
<!-- src/kairo/web/templates/dashboard.html -->
{% extends "base.html" %}
{% block subtitle %}{{ root }} · {{ items|length }} workspaces{% endblock %}
{% block body %}
<div class="grid">
  {% for s in items %}
  <a class="card" href="/w/{{ s.slug }}">
    <h3>{{ s.topic }}</h3>
    <div class="muted">{{ s.slug }}</div>
    <div style="margin-top:8px">
      <span class="badge">{{ s.stream_count }} 观测</span>
      <span class="badge">{{ s.corpus_count }} 基线</span>
      {% if s.stale_count %}<span class="badge warn">{{ s.stale_count }} 待 step</span>{% endif %}
      {% if s.blocked_count %}<span class="badge warn">⚠ {{ s.blocked_count }}</span>{% endif %}
    </div>
  </a>
  {% else %}
  <p class="muted">该目录下没有 workspace(子目录需含 constitution.yaml)。</p>
  {% endfor %}
</div>
{% endblock %}
```

- [ ] **Step 5: Create views.py and server.py**

```python
# src/kairo/web/views.py
"""web console 路由(APIRouter):dashboard / workspace / 产物预览 / 写操作 / step。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from kairo.web.discovery import scan_workspaces

router = APIRouter()


@router.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"ok": True})


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    root = request.app.state.root
    items = scan_workspaces(root)
    return request.app.state.templates.TemplateResponse(
        request, "dashboard.html", {"items": items, "root": str(root)}
    )
```

```python
# src/kairo/web/server.py
"""FastAPI app 工厂 + uvicorn 启动。app.state 持有 root / 模板 / 任务注册表。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from kairo.web.tasks import TaskRegistry
from kairo.web.views import router

_HERE = Path(__file__).parent


def create_app(root: Path) -> FastAPI:
    app = FastAPI(title="kairo console")
    app.state.root = Path(root)
    app.state.templates = Jinja2Templates(directory=str(_HERE / "templates"))
    app.state.registry = TaskRegistry()
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")
    app.include_router(router)
    return app


def run(root: Path, port: int = 8000) -> None:
    import uvicorn

    uvicorn.run(create_app(Path(root)), host="127.0.0.1", port=port)
```

> 注:`server.py` 依赖 `kairo.web.tasks.TaskRegistry`(Task 7 实现)。本任务先写一个最小占位以便 import 成立——在 Task 7 用完整实现替换:

```python
# src/kairo/web/tasks.py  —— 本任务的临时最小占位(Task 7 整体替换)
"""step 后台任务与 SSE(占位,Task 7 完善)。"""

from __future__ import annotations


class TaskRegistry:
    def __init__(self) -> None:
        self._tasks: dict = {}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_web_api.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/kairo/web/server.py src/kairo/web/views.py src/kairo/web/tasks.py src/kairo/web/templates src/kairo/web/static
git add tests/test_web_api.py
git commit -m "feat(web): app 工厂 + dashboard + 静态资源(htmx/css) (#35)"
```

---

## Task 5: workspace 视图 + 产物预览 + reference 详情

三栏 workspace 视图,左侧产物导航(带 stale/blocked 状态点),中间 markdown 预览,右侧操作面板雏形 + reference 列表。

**Files:**
- Modify: `src/kairo/web/views.py`
- Create: `src/kairo/web/templates/workspace.html`
- Create: `src/kairo/web/templates/_doc.html`
- Create: `src/kairo/web/templates/_ref.html`
- Modify: `tests/test_web_api.py`

**Interfaces:**
- Consumes: `kairo.workspace.Workspace`,`kairo.web.render.render_markdown`
- Produces(新路由):
  - `GET /w/{slug}` → workspace 视图 HTML(404 若非 workspace)
  - `GET /w/{slug}/doc?path=<relpath>` → 渲染该 workspace 内某 `.md` 产物为 HTML 片段(路径越界/非 .md/不存在 → 404)
  - `GET /w/{slug}/ref/{ref_id}` → reference 详情片段(manifest + forms,各 `.md` form 带预览链接)
  - 内部助手 `views._open(request, slug) -> Workspace`、`views._safe_doc(ws, relpath) -> Path`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_web_api.py 追加
import pytest


def _ws_with_step(root, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    from kairo.engine import step
    from kairo.provider import select_provider
    ws = Workspace.init(root / "ws", topic="主题")
    (root / "m.txt").write_text("王强会议:落地优先级")
    ws.add([root / "m.txt"])
    step(ws, select_provider())
    return ws


def test_workspace_view_lists_targets_and_refs(tmp_path, monkeypatch):
    _ws_with_step(tmp_path, monkeypatch)
    r = TestClient(create_app(tmp_path)).get("/w/ws")
    assert r.status_code == 200
    assert "understanding.md" in r.text and "assessment.md" in r.text


def test_workspace_view_404_for_unknown(tmp_path):
    r = TestClient(create_app(tmp_path)).get("/w/nope")
    assert r.status_code == 404


def test_doc_renders_markdown(tmp_path, monkeypatch):
    _ws_with_step(tmp_path, monkeypatch)
    c = TestClient(create_app(tmp_path))
    r = c.get("/w/ws/doc", params={"path": "understanding.md"})
    assert r.status_code == 200
    assert "落地优先级" in r.text  # stub 把正文带进 understanding


def test_doc_rejects_path_traversal(tmp_path, monkeypatch):
    _ws_with_step(tmp_path, monkeypatch)
    c = TestClient(create_app(tmp_path))
    assert c.get("/w/ws/doc", params={"path": "../m.txt"}).status_code == 404
    assert c.get("/w/ws/doc", params={"path": "/etc/hosts"}).status_code == 404


def test_ref_detail_shows_forms(tmp_path, monkeypatch):
    _ws_with_step(tmp_path, monkeypatch)
    ref_id = next(iter(__import__("os").listdir(tmp_path / "ws" / "references")))
    r = TestClient(create_app(tmp_path)).get(f"/w/ws/ref/{ref_id}")
    assert r.status_code == 200 and "transcript" in r.text or "digest" in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `KAIRO_STUB=1 uv run pytest tests/test_web_api.py -v`
Expected: FAIL(新增用例 404/AttributeError —— 路由未实现)

- [ ] **Step 3: Add helpers + routes to views.py**

在 `views.py` import 段补充并新增路由:

```python
from pathlib import Path

from fastapi import HTTPException

from kairo.web.render import render_markdown
from kairo.workspace import Workspace, WorkspaceNotFound


def _open(request: Request, slug: str) -> Workspace:
    try:
        return Workspace.open(Path(request.app.state.root) / slug)
    except WorkspaceNotFound:
        raise HTTPException(status_code=404, detail="workspace not found")


def _safe_doc(ws: Workspace, relpath: str) -> Path:
    """把 workspace 相对路径解析为 .md 绝对路径;越界/非 md/不存在 → 404。"""
    target = (ws.root / relpath).resolve()
    root = ws.root.resolve()
    if root not in target.parents or target.suffix != ".md" or not target.is_file():
        raise HTTPException(status_code=404, detail="doc not found")
    return target


def _target_states(ws: Workspace):
    """各 target 的 (path, status) —— 给左栏状态点。"""
    state = ws.read_state()
    out = []
    for t in ws.constitution.targets:
        ts = state.targets.get(t.path)
        status = ts.status if ts else "missing"
        out.append({"path": t.path, "status": status})
    return out


@router.get("/w/{slug}", response_class=HTMLResponse)
def workspace_view(request: Request, slug: str) -> HTMLResponse:
    ws = _open(request, slug)
    refs = []
    for ref_id in ws.list_reference_ids():
        man = ws.read_manifest(ref_id)
        refs.append({"id": ref_id, "title": man.title, "cls": man.source_class})
    return request.app.state.templates.TemplateResponse(
        request,
        "workspace.html",
        {
            "slug": slug,
            "topic": ws.constitution.topic,
            "targets": _target_states(ws),
            "refs": refs,
        },
    )


@router.get("/w/{slug}/doc", response_class=HTMLResponse)
def doc_view(request: Request, slug: str, path: str) -> HTMLResponse:
    ws = _open(request, slug)
    target = _safe_doc(ws, path)
    return request.app.state.templates.TemplateResponse(
        request, "_doc.html", {"title": path, "html": render_markdown(target.read_text())}
    )


@router.get("/w/{slug}/ref/{ref_id}", response_class=HTMLResponse)
def ref_view(request: Request, slug: str, ref_id: str) -> HTMLResponse:
    ws = _open(request, slug)
    if ref_id not in ws.list_reference_ids():
        raise HTTPException(status_code=404, detail="reference not found")
    man = ws.read_manifest(ref_id)
    forms = [
        {
            "role": f.role,
            "location": f.location,
            "origin": f.origin,
            "is_md": f.location.endswith(".md"),
        }
        for f in man.forms
    ]
    return request.app.state.templates.TemplateResponse(
        request,
        "_ref.html",
        {"slug": slug, "ref_id": ref_id, "title": man.title, "cls": man.source_class, "forms": forms},
    )
```

- [ ] **Step 4: Create templates**

```html
<!-- src/kairo/web/templates/workspace.html -->
{% extends "base.html" %}
{% block subtitle %}{{ topic }} · {{ slug }}{% endblock %}
{% block body %}
<div class="layout">
  <aside class="nav">
    <strong>产物</strong>
    {% for t in targets %}
    <a class="nav-doc" href="#" hx-get="/w/{{ slug }}/doc?path={{ t.path }}" hx-target="#reader">
      <span class="dot {% if t.status == 'blocked' %}blocked{% elif t.status == 'missing' %}stale{% endif %}"></span>{{ t.path }}
    </a>
    {% endfor %}
    <strong style="display:block;margin-top:12px">References</strong>
    {% for r in refs %}
    <a class="nav-doc" href="#" hx-get="/w/{{ slug }}/ref/{{ r.id }}" hx-target="#refbox">
      {{ r.title }} <span class="muted">· {{ r.cls }}</span>
    </a>
    {% endfor %}
  </aside>
  <main id="reader"><p class="muted">点左侧产物预览。</p></main>
  <aside class="panel">
    <button hx-post="/w/{{ slug }}/step" hx-target="#step-area" hx-swap="innerHTML">Step</button>
    <div id="step-area"></div>
    <div id="refbox" class="muted" style="margin-top:12px">选 reference 看详情。</div>
  </aside>
</div>
{% endblock %}
```

```html
<!-- src/kairo/web/templates/_doc.html -->
<article class="doc">
  <h2 class="muted">{{ title }}</h2>
  {{ html|safe }}
</article>
```

```html
<!-- src/kairo/web/templates/_ref.html -->
<div class="ref">
  <strong>{{ title }}</strong> <span class="muted">· {{ cls }} · {{ ref_id }}</span>
  <table>
    <tr><th>role</th><th>origin</th><th></th></tr>
    {% for f in forms %}
    <tr>
      <td>{{ f.role }}</td><td class="muted">{{ f.origin }}</td>
      <td>{% if f.is_md %}<a href="#" hx-get="/w/{{ slug }}/doc?path={{ f.location }}" hx-target="#reader">预览</a>{% endif %}</td>
    </tr>
    {% endfor %}
  </table>
</div>
```

- [ ] **Step 5: Run test to verify it passes**

Run: `KAIRO_STUB=1 uv run pytest tests/test_web_api.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/kairo/web/views.py src/kairo/web/templates/workspace.html src/kairo/web/templates/_doc.html src/kairo/web/templates/_ref.html tests/test_web_api.py
git commit -m "feat(web): workspace 视图 + markdown 预览 + reference 详情(含路径越界防护) (#35)"
```

---

## Task 6: 写操作 —— add reference / 登记 corpus / accept

界面写操作,全部调 core(`Workspace.add` / `engine.accept`),不重写逻辑。

**Files:**
- Modify: `src/kairo/web/views.py`
- Create: `src/kairo/web/templates/_refs_list.html`
- Test: `tests/test_web_write.py`

**Interfaces:**
- Consumes: `Workspace.add(files, source_class=...)`,`kairo.engine.accept(ws, doc)`,`kairo.provider`(无需)
- Produces(新路由,均返回刷新后的 reference 列表片段或状态片段):
  - `POST /w/{slug}/ref`(表单:`file`=上传文件 或 `path`=本地路径登记)→ 调 `ws.add`,返回 `_refs_list.html`
  - `POST /w/{slug}/corpus`(表单:`path`=目录/文件路径)→ 调 `ws.add(..., source_class="corpus")`
  - `POST /w/{slug}/accept`(表单:`doc`)→ 调 `engine.accept`,返回该文档状态片段
  - 内部助手 `views._save_upload(ws, upload) -> Path`(把上传写入 `<ws>/.kairo/uploads/<filename>` 再交给 add)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_web_write.py
import io

from fastapi.testclient import TestClient

from kairo.web.server import create_app
from kairo.workspace import Workspace


def _client(root):
    return TestClient(create_app(root))


def test_add_ref_by_path(tmp_path):
    Workspace.init(tmp_path / "ws", topic="t")
    src = tmp_path / "note.txt"
    src.write_text("一条笔记")
    r = _client(tmp_path).post("/w/ws/ref", data={"path": str(src)})
    assert r.status_code == 200
    assert "note" in r.text  # 列表片段含新 reference
    ws = Workspace.open(tmp_path / "ws")
    assert len(ws.list_reference_ids()) == 1


def test_add_ref_by_upload(tmp_path):
    Workspace.init(tmp_path / "ws", topic="t")
    files = {"file": ("meeting.txt", io.BytesIO("上传内容".encode()), "text/plain")}
    r = _client(tmp_path).post("/w/ws/ref", files=files)
    assert r.status_code == 200
    ws = Workspace.open(tmp_path / "ws")
    assert len(ws.list_reference_ids()) == 1


def test_add_corpus_dir(tmp_path):
    Workspace.init(tmp_path / "ws", topic="t")
    cdir = tmp_path / "baseline"
    cdir.mkdir()
    (cdir / "x.md").write_text("基线")
    r = _client(tmp_path).post("/w/ws/corpus", data={"path": str(cdir)})
    assert r.status_code == 200
    ws = Workspace.open(tmp_path / "ws")
    man = ws.read_manifest(ws.list_reference_ids()[0])
    assert man.source_class == "corpus"


def test_accept_clears_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    from kairo.engine import step
    from kairo.provider import select_provider
    ws = Workspace.init(tmp_path / "ws", topic="t")
    (tmp_path / "m.txt").write_text("内容")
    ws.add([tmp_path / "m.txt"])
    step(ws, select_provider())
    # 手改 understanding,再 step → blocked:manual-edit
    (tmp_path / "ws" / "understanding.md").write_text("手改了")
    step(ws, select_provider())
    assert ws.read_state().targets["understanding.md"].status == "blocked"
    r = _client(tmp_path).post("/w/ws/accept", data={"doc": "understanding.md"})
    assert r.status_code == 200
    assert Workspace.open(tmp_path / "ws").read_state().targets["understanding.md"].status == "ok"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `KAIRO_STUB=1 uv run pytest tests/test_web_write.py -v`
Expected: FAIL(路由未实现,405/404)

- [ ] **Step 3: Add write routes to views.py**

在 `views.py` 补 import 与路由:

```python
from fastapi import File, Form, UploadFile

from kairo.engine import accept as engine_accept
from kairo.workspace import AddError


def _refs_fragment(request: Request, ws: Workspace, slug: str) -> HTMLResponse:
    refs = []
    for ref_id in ws.list_reference_ids():
        man = ws.read_manifest(ref_id)
        refs.append({"id": ref_id, "title": man.title, "cls": man.source_class})
    return request.app.state.templates.TemplateResponse(
        request, "_refs_list.html", {"slug": slug, "refs": refs}
    )


def _save_upload(ws: Workspace, upload: UploadFile) -> Path:
    dest_dir = ws.root / ".kairo" / "uploads"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / Path(upload.filename or "upload.bin").name
    dest.write_bytes(upload.file.read())
    return dest


@router.post("/w/{slug}/ref", response_class=HTMLResponse)
def add_ref(
    request: Request,
    slug: str,
    path: str = Form(None),
    file: UploadFile = File(None),
) -> HTMLResponse:
    ws = _open(request, slug)
    if file is not None:
        src = _save_upload(ws, file)
    elif path:
        src = Path(path)
    else:
        raise HTTPException(status_code=400, detail="need file or path")
    try:
        ws.add([src])
    except AddError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _refs_fragment(request, ws, slug)


@router.post("/w/{slug}/corpus", response_class=HTMLResponse)
def add_corpus(request: Request, slug: str, path: str = Form(...)) -> HTMLResponse:
    ws = _open(request, slug)
    try:
        ws.add([Path(path)], source_class="corpus")
    except AddError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _refs_fragment(request, ws, slug)


@router.post("/w/{slug}/accept", response_class=HTMLResponse)
def accept_doc(request: Request, slug: str, doc: str = Form(...)) -> HTMLResponse:
    ws = _open(request, slug)
    engine_accept(ws, doc)
    state = ws.read_state()
    ts = state.targets.get(doc)
    status = ts.status if ts else "missing"
    return HTMLResponse(f'<span class="dot {status}"></span>{doc}: {status}')
```

- [ ] **Step 4: Create `_refs_list.html`**

```html
<!-- src/kairo/web/templates/_refs_list.html -->
{% for r in refs %}
<a class="nav-doc" href="#" hx-get="/w/{{ slug }}/ref/{{ r.id }}" hx-target="#refbox">
  {{ r.title }} <span class="muted">· {{ r.cls }}</span>
</a>
{% endfor %}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `KAIRO_STUB=1 uv run pytest tests/test_web_write.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/kairo/web/views.py src/kairo/web/templates/_refs_list.html tests/test_web_write.py
git commit -m "feat(web): 写操作 add reference / 登记 corpus / accept(全调 core) (#35)"
```

---

## Task 7: tasks.py —— TaskRegistry + 子进程 step + SSE 事件流

step 以子进程执行,逐行缓冲 stdout;单 workspace 串行锁;SSE 事件流(回放缓冲 + 终止事件)。本任务整体替换 Task 4 的占位 `tasks.py`。

**Files:**
- Modify(整体替换): `src/kairo/web/tasks.py`
- Test: `tests/test_web_tasks.py`

**Interfaces:**
- Produces:
  - `tasks.StepTask`(dataclass:`task_id, slug, lines, done, exit_code`)
  - `tasks.TaskRegistry.is_running(slug) -> bool`
  - `tasks.TaskRegistry.start(slug, cwd, argv) -> StepTask`(spawn 子进程 + 后台读线程;`slug` 已在跑则 raise `RuntimeError`)
  - `tasks.TaskRegistry.get(task_id) -> StepTask | None`
  - `tasks.TaskRegistry.cancel(task_id) -> bool`
  - `tasks.stream_events(task: StepTask) -> Iterator[str]`(SSE 文本块:每行 `data:`,结束 `event: done`)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_web_tasks.py
import sys
import time

from kairo.web.tasks import StepTask, TaskRegistry, stream_events


def _wait(task, timeout=10):
    end = time.time() + timeout
    while not task.done and time.time() < end:
        time.sleep(0.02)
    assert task.done, "task did not finish"


def test_start_captures_lines_and_exit(tmp_path):
    reg = TaskRegistry()
    argv = [sys.executable, "-c", "print('a'); print('b')"]
    t = reg.start("ws", tmp_path, argv)
    _wait(t)
    assert t.lines == ["a", "b"]
    assert t.exit_code == 0


def test_serial_lock_rejects_second(tmp_path):
    reg = TaskRegistry()
    slow = [sys.executable, "-c", "import time; time.sleep(2)"]
    reg.start("ws", tmp_path, slow)
    try:
        reg.start("ws", tmp_path, slow)
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


def test_different_slugs_run_concurrently(tmp_path):
    reg = TaskRegistry()
    a = reg.start("a", tmp_path, [sys.executable, "-c", "print('x')"])
    b = reg.start("b", tmp_path, [sys.executable, "-c", "print('y')"])
    _wait(a); _wait(b)
    assert a.lines == ["x"] and b.lines == ["y"]


def test_stream_events_replays_then_done():
    t = StepTask(task_id="t1", slug="ws")
    t.lines = ["line1", "line2"]
    t.done = True
    t.exit_code = 0
    out = list(stream_events(t))
    assert "data: line1\n\n" in out
    assert "data: line2\n\n" in out
    assert out[-1] == "event: done\ndata: 0\n\n"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_web_tasks.py -v`
Expected: FAIL with `ImportError: cannot import name 'StepTask'`

- [ ] **Step 3: Replace tasks.py with full implementation**

```python
# src/kairo/web/tasks.py
"""step 后台任务:子进程跑 step + 逐行缓冲 stdout;单 workspace 串行;SSE 事件流。

任务状态纯内存(server 重启丢运行中任务,本地单用户可接受)。
"""

from __future__ import annotations

import subprocess
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class StepTask:
    task_id: str
    slug: str
    lines: list[str] = field(default_factory=list)
    done: bool = False
    exit_code: int | None = None
    proc: subprocess.Popen | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class TaskRegistry:
    """task_id → StepTask;并维护每个 slug 的在跑任务(串行锁)。"""

    def __init__(self, max_lines: int = 2000) -> None:
        self._tasks: dict[str, StepTask] = {}
        self._running_by_slug: dict[str, str] = {}
        self._max_lines = max_lines
        self._guard = threading.Lock()

    def is_running(self, slug: str) -> bool:
        with self._guard:
            tid = self._running_by_slug.get(slug)
            return tid is not None and not self._tasks[tid].done

    def start(self, slug: str, cwd: Path, argv: list[str]) -> StepTask:
        with self._guard:
            tid = self._running_by_slug.get(slug)
            if tid is not None and not self._tasks[tid].done:
                raise RuntimeError(f"step already running for {slug}")
            task_id = uuid.uuid4().hex[:12]
            proc = subprocess.Popen(
                argv,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            task = StepTask(task_id=task_id, slug=slug, proc=proc)
            self._tasks[task_id] = task
            self._running_by_slug[slug] = task_id
        threading.Thread(target=self._pump, args=(task,), daemon=True).start()
        return task

    def _pump(self, task: StepTask) -> None:
        assert task.proc is not None and task.proc.stdout is not None
        for raw in task.proc.stdout:
            line = raw.rstrip("\n")
            with task.lock:
                task.lines.append(line)
                if len(task.lines) > self._max_lines:
                    del task.lines[: len(task.lines) - self._max_lines]
        task.proc.wait()
        with task.lock:
            task.exit_code = task.proc.returncode
            task.done = True

    def get(self, task_id: str) -> StepTask | None:
        return self._tasks.get(task_id)

    def cancel(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task is None or task.proc is None or task.done:
            return False
        task.proc.terminate()
        return True


def stream_events(task: StepTask) -> Iterator[str]:
    """SSE:先回放已缓冲行,再 tail 新行,进程结束推 done(exit_code)。"""
    idx = 0
    while True:
        with task.lock:
            new = task.lines[idx:]
            done = task.done
            code = task.exit_code
        for line in new:
            yield f"data: {line}\n\n"
        idx += len(new)
        if done:
            yield f"event: done\ndata: {code}\n\n"
            return
        time.sleep(0.1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_web_tasks.py tests/test_web_api.py -v`
Expected: PASS(tasks 测试通过 + app 工厂仍能 import 完整 TaskRegistry)

- [ ] **Step 5: Commit**

```bash
git add src/kairo/web/tasks.py tests/test_web_tasks.py
git commit -m "feat(web): TaskRegistry 子进程 step + 串行锁 + SSE 事件流 (#35)"
```

---

## Task 8: step 端点 + SSE 流 + 取消 + `python -m kairo`

把 step 触发/进度/取消接到 `tasks.py`;新增 `__main__.py` 让子进程用 `python -m kairo` 稳定调起 CLI。

**Files:**
- Create: `src/kairo/__main__.py`
- Modify: `src/kairo/web/views.py`
- Create: `src/kairo/web/templates/_step.html`
- Test: `tests/test_web_tasks.py`(追加端点测试)

**Interfaces:**
- Consumes: `request.app.state.registry`(`TaskRegistry`),`tasks.stream_events`
- Produces(新路由):
  - `POST /w/{slug}/step`(可选表单 `target`)→ 起任务(已在跑返回提示片段),返回 `_step.html`(含 SSE 容器,绑定到 stream 端点)
  - `GET /w/{slug}/step/{task_id}/stream` → `StreamingResponse`(`text/event-stream`)
  - `POST /w/{slug}/step/{task_id}/cancel` → 取消,返回片段
- 子进程命令:`[sys.executable, "-m", "kairo", "step"]`,有 `target` 时 `["re-step", target]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_web_tasks.py 追加
from fastapi.testclient import TestClient

from kairo.web.server import create_app
from kairo.workspace import Workspace


def test_step_endpoint_runs_and_streams(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = Workspace.init(tmp_path / "ws", topic="t")
    (tmp_path / "m.txt").write_text("会议内容")
    ws.add([tmp_path / "m.txt"])
    c = TestClient(create_app(tmp_path))
    r = c.post("/w/ws/step")
    assert r.status_code == 200
    # 片段含 SSE 容器 + task_id 指向 stream 端点
    assert "/stream" in r.text
    # 拉一次 SSE,应能读到 done 事件
    import re
    m = re.search(r"/w/ws/step/([0-9a-f]+)/stream", r.text)
    assert m
    tid = m.group(1)
    body = c.get(f"/w/ws/step/{tid}/stream").text
    assert "event: done" in body
    # 收敛后产物生成
    assert (tmp_path / "ws" / "understanding.md").is_file()


def test_step_rejects_concurrent(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = Workspace.init(tmp_path / "ws", topic="t")
    (tmp_path / "m.txt").write_text("x")
    ws.add([tmp_path / "m.txt"])
    c = TestClient(create_app(tmp_path))
    # 直接占用该 slug 的串行锁(注入一个慢任务),再请求 step
    import sys
    app = c.app
    app.state.registry.start("ws", tmp_path / "ws", [sys.executable, "-c", "import time; time.sleep(2)"])
    r = c.post("/w/ws/step")
    assert r.status_code == 200
    assert "正在运行" in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `KAIRO_STUB=1 uv run pytest tests/test_web_tasks.py -v`
Expected: FAIL(step 端点未实现)

- [ ] **Step 3: Create `__main__.py`**

```python
# src/kairo/__main__.py
"""python -m kairo 入口(web console 子进程调起 step 用)。"""

from kairo.cli import app

if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Add step routes to views.py**

`views.py` 补 import 与路由:

```python
import sys

from fastapi.responses import StreamingResponse

from kairo.web.tasks import stream_events


@router.post("/w/{slug}/step", response_class=HTMLResponse)
def start_step(request: Request, slug: str, target: str = Form(None)) -> HTMLResponse:
    ws = _open(request, slug)
    reg = request.app.state.registry
    if reg.is_running(slug):
        return HTMLResponse('<p class="muted">⏳ 正在运行,请等待当前 step 结束。</p>')
    argv = [sys.executable, "-m", "kairo"] + (["re-step", target] if target else ["step"])
    task = reg.start(slug, ws.root, argv)
    return request.app.state.templates.TemplateResponse(
        request, "_step.html", {"slug": slug, "task_id": task.task_id}
    )


@router.get("/w/{slug}/step/{task_id}/stream")
def step_stream(request: Request, slug: str, task_id: str) -> StreamingResponse:
    task = request.app.state.registry.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return StreamingResponse(stream_events(task), media_type="text/event-stream")


@router.post("/w/{slug}/step/{task_id}/cancel", response_class=HTMLResponse)
def cancel_step(request: Request, slug: str, task_id: str) -> HTMLResponse:
    ok = request.app.state.registry.cancel(task_id)
    return HTMLResponse('<p class="muted">已取消。</p>' if ok else '<p class="muted">无法取消(已结束)。</p>')
```

- [ ] **Step 5: Create `_step.html`**

step 完成后,HTMX 用 SSE 的 `done` 事件触发 dashboard/状态刷新(重载 workspace 视图的产物区)。

```html
<!-- src/kairo/web/templates/_step.html -->
<div hx-ext="sse" sse-connect="/w/{{ slug }}/step/{{ task_id }}/stream">
  <div id="step-log" sse-swap="message" hx-swap="beforeend"></div>
  <div sse-swap="done"
       hx-get="/w/{{ slug }}"
       hx-trigger="sse:done"
       hx-target="body"
       hx-select=".layout"
       hx-swap="outerHTML"
       class="muted">step 进行中…(完成后自动刷新状态)</div>
  <button hx-post="/w/{{ slug }}/step/{{ task_id }}/cancel" hx-target="#step-area">取消</button>
</div>
```

> 注:SSE 扩展需 htmx sse 扩展。在 `base.html` 的 htmx 之后引入 vendored 扩展:
> `curl -L https://unpkg.com/htmx-ext-sse@2.2.2/dist/sse.js -o src/kairo/web/static/sse.js`
> 并在 `base.html` 加 `<script src="/static/sse.js"></script>`(在 `htmx.min.js` 之后)。

- [ ] **Step 6: Run tests to verify they pass**

Run: `KAIRO_STUB=1 uv run pytest tests/test_web_tasks.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/kairo/__main__.py src/kairo/web/views.py src/kairo/web/templates/_step.html src/kairo/web/static/sse.js src/kairo/web/templates/base.html tests/test_web_tasks.py
git commit -m "feat(web): step 触发 + SSE 流式进度 + 取消 + python -m kairo 入口 (#35)"
```

---

## Task 9: `kairo serve` 命令 + 端到端冒烟

core CLI 加薄命令 `serve`;缺 web 依赖时给友好提示;补一个端到端冒烟测试。

**Files:**
- Modify: `src/kairo/cli.py`
- Modify: `README.md`(加 web console 一节)
- Test: `tests/test_cli.py`(追加 serve 友好提示用例)

**Interfaces:**
- Consumes: `kairo.web.server.run`(延迟 import,缺依赖时 `ImportError` → 友好提示)
- Produces: CLI 命令 `kairo serve [root] [--port]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py 追加
def test_cli_serve_missing_web_dep_friendly(monkeypatch):
    """缺 kairo[web] 依赖时 serve 给友好提示、非零退出,不吐 traceback。"""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name.startswith("kairo.web"):
            raise ImportError("no web")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = runner.invoke(app, ["serve", "--port", "0"])
    assert result.exit_code != 0
    assert "kairo[web]" in result.output
    assert "Traceback" not in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_cli_serve_missing_web_dep_friendly -v`
Expected: FAIL(无 serve 命令 → exit_code/输出不符)

- [ ] **Step 3: Add `serve` command to cli.py**

```python
# src/kairo/cli.py 追加(在文件末尾):
@app.command()
def serve(
    root: Path = typer.Argument(Path.cwd, help="包含多个 workspace 的根目录"),
    port: int = typer.Option(8000, "--port", "-p", help="监听端口"),
) -> None:
    """启动本地 Web Console,浏览器统管 root 下的多个 workspace。"""
    try:
        from kairo.web.server import run as web_run
    except ImportError:
        typer.secho(
            "未安装 web 依赖。请运行:pip install 'kairo[web]'",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1) from None
    typer.echo(f"kairo console: http://127.0.0.1:{port}  (root={root})")
    web_run(root, port=port)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py::test_cli_serve_missing_web_dep_friendly -v`
Expected: PASS

- [ ] **Step 5: Manual smoke test(人工验收)**

```bash
mkdir -p /tmp/kairo-demo/topic-a && cd /tmp/kairo-demo/topic-a
uv run kairo init "演示" && echo "王强会议:落地优先级" > m.txt && KAIRO_STUB=1 uv run kairo add m.txt
cd /tmp/kairo-demo && KAIRO_STUB=1 uv run kairo serve . --port 8765
```
浏览器开 `http://127.0.0.1:8765`:应见 `topic-a` 卡片 → 进入 → 点 Step 看日志流 → 完成后预览 understanding.md。验毕 Ctrl-C。

- [ ] **Step 6: Update README**

在 `README.md` 末尾加一节:

```markdown
## Web Console(可选)

    pip install 'kairo[web]'
    kairo serve <包含多个 workspace 的根目录>

浏览器统管本地 workspace:dashboard 总览、产物预览、reference/corpus 登记、从界面触发 step 看实时进度。
```

- [ ] **Step 7: Run full test suite**

Run: `KAIRO_STUB=1 uv run pytest -q`
Expected: 全绿(既有 105+ 测试 + 新增 web 测试)

- [ ] **Step 8: Commit**

```bash
git add src/kairo/cli.py README.md tests/test_cli.py
git commit -m "feat(cli): kairo serve 启动 web console + 缺依赖友好提示 (#35)"
```

---

## Self-Review

**1. Spec coverage**(对照 `docs/design/35-kairo-web-console.md`):
- §3 模块结构(独立子包 + 可选依赖 + core 仅加 serve)→ Task 2/4/9 + 全程约束 ✓
- §4 发现层 WorkspaceSummary → Task 3 ✓
- §5 API 面(dashboard/workspace/doc/ref/写操作/step/stream/cancel/healthz)→ Task 4/5/6/8 ✓
- §6 step 子进程 + SSE + 单 workspace 串行 + 完成后读 state 刷新 → Task 7/8 ✓
- §7 三页面(dashboard/workspace 三栏/ref 详情)→ Task 4/5 ✓
- §8 测试策略(discovery 纯函数 / TestClient / 假命令测 SSE+串行锁)→ 各任务测试 ✓
- §9 风险(路径越界、上传契约、core 抽函数)→ Task 5 越界测试、Task 6 `_save_upload`、Task 1 `_build_rules` ✓

**2. Placeholder scan:** Task 4 的 `tasks.py` 占位是**刻意的临时骨架**,Task 7 明确整体替换并有测试守护;非交付占位。无 TBD/TODO/“类似上文”。

**3. Type consistency:** `WorkspaceSummary` 字段在 Task 3 定义、dashboard.html(Task 4)消费一致;`StepTask`(`task_id/slug/lines/done/exit_code`)在 Task 7 定义、Task 8 端点与模板一致;`engine.pending` 在 Task 1 定义、Task 3 消费一致;`Workspace.add(..., source_class=)` 签名与 `workspace.py` 实际一致;`engine.accept(ws, doc)` 与实际一致。

**4. core 行为不变保障:** Task 1 在回归步骤跑 `test_engine.py` + `test_cli.py`;Task 9 跑全量套件。
