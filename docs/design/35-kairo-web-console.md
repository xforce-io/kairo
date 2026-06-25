# 35 — 轻量本地 Web Console

- Issue: [#35](https://github.com/xforce-io/kairo/issues/35)
- 分支: `feat/35-web-console`
- 状态: 设计已确认,待实现计划
- 日期: 2026-06-25

## 1. 背景与目标

kairo 当前是纯 CLI 的增量知识构建引擎。workspace / reference / corpus / 产物全部以磁盘目录 + markdown 形式存在,缺少可视入口,带来四个痛点:

1. **看不见产物全貌** — understanding.md / assessment.md / digest 散在各目录,无法并排预览、随手翻看。
2. **管理操作繁琐** — `add` / `step` / `re-step` / `accept` 需要记忆命令、手敲。
3. **状态不透明** — step 收敛跑到哪、哪些 reference stale、哪些 blocked,命令行下不直观。
4. **多 workspace 难统管** — 本地多个 topic 工作区没有总入口与快速切换。

**目标**:提供一个**轻量本地 Web Console**,通过 `kairo serve <root>` 启动,用浏览器统管本地多个 workspace —— dashboard 总览、产物预览、reference/corpus 管理、从界面触发 step 并查看实时进度。

**非目标(v1 不做)**:多用户/鉴权、远程部署、协作编辑、在界面里直接编辑 markdown 正文(编辑仍走用户惯用编辑器,界面只读预览 + accept)、结构化的 step 事件协议(v1 用日志流即可)。

## 2. 核心设计决策

| 维度 | 决策 |
|---|---|
| 形态 | 本地 Web App,`kairo serve <root>` |
| 后端 | FastAPI,直接复用 kairo 现有库模块,不 shell-out 读数据 |
| 前端 | 服务端渲染 + HTMX + SSE,**无 node 构建链**,vendored 静态资源 |
| 解耦 | web 作为独立可选子包 `src/kairo/web/`,可选依赖 `kairo[web]` |
| core 侵入 | 仅新增一个 `kairo serve` 命令;业务逻辑零重写 |
| workspace 发现 | 扫 `<root>` 下一层,凡含 `constitution.yaml` 即认作 workspace |
| step 执行 | 子进程跑 `kairo step` + SSE 转发 stdout,完成后读 `state.json` 刷新 |
| 并发 | 单 workspace 串行锁;不同 workspace 可并行;任务状态纯内存 |
| 适用 | 单用户本地,无需鉴权 |

设计哲学:**结构大于逻辑、避免过度工程**。web 层只做"调度 + 呈现",所有业务逻辑留在 core;前端压缩到几乎没有独立构建产物。

## 3. 模块结构与解耦

```
src/kairo/
├── (现有 core: cli.py / engine.py / workspace.py / corpus.py / models.py ...)  ← 仅 cli.py 加一个 serve 命令
└── web/                          # 新增,独立子包
    ├── __init__.py
    ├── server.py                 # FastAPI app 工厂 + uvicorn 启动
    ├── discovery.py              # 扫父目录 → workspace 摘要列表
    ├── views.py                  # 路由 + Jinja 渲染
    ├── tasks.py                  # step 后台子进程 + SSE 进度 + TaskRegistry
    ├── render.py                 # markdown → html(markdown-it-py)
    ├── templates/                # Jinja 模板
    └── static/                   # htmx.min.js + app.css(无构建链)
```

解耦约束:

- **可选依赖**:`pyproject.toml` 新增
  `[project.optional-dependencies] web = ["fastapi", "uvicorn", "jinja2", "markdown-it-py"]`。
  不安装 `kairo[web]` 的用户零影响。
- **CLI 入口**:`cli.py` 仅新增薄命令 `kairo serve [root] [--port]`。`import kairo.web` 若缺依赖,给友好提示 `pip install kairo[web]`。这是 core 唯一改动。
- **web 调用 core**:discovery/views 调 `workspace.py` / `models.py` 读数据;写操作调 core 现有函数。
- **唯一允许的 core 重构**:若某操作目前只暴露在 CLI 命令体内、无可复用函数,实现时把它抽成一个 core 函数,由 CLI 与 web 共用。仅在必要时,且不改变现有行为。
- **静态资源 vendored**:htmx 单文件 + 一个手写 css,不引 npm/vite。

## 4. 数据发现层

`discovery.py` 在 `kairo serve <root>` 时扫 `<root>` 下一层子目录,凡含 `constitution.yaml` 即 workspace。每个产出**轻量摘要**(不读 markdown 正文,保证 dashboard 秒开):

```
WorkspaceSummary:
  slug            # 目录名,URL 用
  topic           # 来自 constitution.yaml
  path
  ref_count       # references/ 下条数
  stream_count / corpus_count
  blocked_count   # state.json 中 blocked:* 标记数
  stale_count     # 待 step 的产物数
  last_step       # 最近收敛时间(若 state.json 有记录)
```

发现层只做目录扫描 + 读 yaml/json 摘要,复用 core 的 `Workspace` / `models` 解析,不自行 parse。

## 5. API 面(HTMX 风格)

多数端点返回 HTML 片段;少数返回 JSON / SSE。

| 方法 & 路径 | 作用 | 返回 |
|---|---|---|
| `GET /` | dashboard:全部 workspace 摘要卡片 | HTML |
| `GET /w/{slug}` | 单 workspace 主视图(产物 + reference 列表) | HTML |
| `GET /w/{slug}/doc/{name}` | 渲染 understanding/assessment/digest/transcript | HTML |
| `GET /w/{slug}/ref/{id}` | 单 reference 详情(manifest + forms + 派生物入口) | HTML |
| `POST /w/{slug}/ref` | 新增 reference(文件上传或路径登记)→ core add | HTML 片段 |
| `POST /w/{slug}/corpus` | 登记 corpus(目录/文件指针) | HTML 片段 |
| `POST /w/{slug}/accept` | accept 某文档手改 | HTML 片段 |
| `POST /w/{slug}/step` | 启动 step / re-step 后台任务 → task_id | HTML(进度区) |
| `GET /w/{slug}/step/{task_id}/stream` | SSE 流式进度 | text/event-stream |
| `POST /w/{slug}/step/{task_id}/cancel` | 取消(kill 子进程组) | HTML 片段 |
| `GET /healthz` | 存活探针 | JSON |

约定:

- 写操作(add/corpus/accept/step)统一调用 core 库函数,web 不重写逻辑。
- **add reference 两种入口**:浏览器文件上传(存临时区 → core add),或填本地路径登记。corpus 通常是目录指针,走填路径。
- 写操作后用 HTMX 局部刷新对应区块,不整页重载。

## 6. step 流式执行模型

v1 工程重心。采用**子进程**而非进程内线程。

```
POST /w/{slug}/step
  → tasks.py 在 workspace 目录下 spawn:  kairo step  (或 re-step <target>)
  → 注册 task_id,把进程句柄 + 日志缓冲存入内存 TaskRegistry
  → 返回 HTMX 进度区块,内含连到 stream 端点的 SSE 容器

GET /w/{slug}/step/{task_id}/stream   (SSE)
  → 先回放已缓冲日志行(支持刷新/重连)
  → 再逐行 tail 子进程 stdout/stderr,每行一个 SSE event
  → 进程退出 → 推终止 event(exit_code)+ 触发前端刷新产物状态区
```

为何子进程:

- **隔离** — provider(claude-code)会 shell-out、可能长跑甚至卡死;子进程崩溃不拖垮 server,取消即 kill pid。
- **零侵入** — 完全复用 `kairo step` 现有行为,无需给 engine 加事件回调 seam。
- **进度来源** — step 本就往 stdout 打日志,直接转发。v1 给"实时日志流 + 完成后结构化刷新",已解决状态不透明痛点。

收敛后结构化更新:子进程结束后,web 重新读该 workspace 的 `state.json`,HTMX 局部刷新产物状态 / blocked 标记 / stale 计数。**运行中看日志流,结束后看结构化结果**。

并发与生命周期:

- **TaskRegistry** — 内存字典 `task_id → {slug, popen, status, log_buffer}`。纯内存,server 重启则运行中任务丢失(本地单用户可接受)。
- **单 workspace 串行** — 同一 workspace 已有 step 在跑时拒绝第二次(返回"正在运行"),避免并发改同一 `state.json`。不同 workspace 可并行。
- **取消** — kill 子进程组。
- **日志缓冲上限** — 每任务保留最近 N 行(如 2000),防内存增长。

## 7. 前端页面结构

三个核心页面 + HTMX 局部片段,无路由框架、无构建链。

### ① Dashboard `GET /`
顶部 `<root>` 路径 + workspace 总数;网格卡片,每个 workspace 一张:topic、ref/stream/corpus 计数、**stale 徽标**、**blocked 红标**、最近收敛时间。卡片点进 workspace 视图。

### ② Workspace 视图 `GET /w/{slug}`
三栏:
- **左:产物导航** — understanding / assessment / 各 reference 的 digest·transcript·prose,带 stale/blocked 状态点。
- **中:阅读区** — markdown 渲染(markdown-it-py),点左侧文档在此呈现。
- **右:操作面板** — `+ Reference`(上传/路径)、`+ Corpus`(路径)、`Step` 按钮(下方展开实时日志流 SSE 区)、`Accept`(文档 blocked:manual-edit 时)、reference 列表(可展开看 manifest/forms)。

### ③ Reference 详情(片段)
manifest 元数据 + forms 表(role/origin/hash)+ 各派生物跳转。

交互约定:写操作 HTMX `hx-swap` 局部刷新;step 日志区 `hx-ext="sse"` 自动追加行,完成事件触发状态区刷新;样式为一个手写 `app.css`(~200 行),克制中性,不引 UI 框架。

## 8. 测试策略

- `discovery.py` — 造临时目录树,断言扫描结果(纯函数,易测)。
- API — FastAPI `TestClient`,断言路由返回 + 写操作正确调用 core(mock core 函数)。
- `tasks.py` — 用 echo/sleep 假命令替代 `kairo step`,断言 SSE 收到行 + 终止事件 + 串行锁生效。
- 不测前端 JS(HTMX 行为),保持轻量。

## 9. 风险与权衡

| 风险 | 缓解 |
|---|---|
| 任务状态纯内存,server 重启丢失运行中任务 | 本地单用户工具可接受;文档写明。必要时未来落盘。 |
| 子进程进度仅日志文本,非结构化 | v1 用"日志流 + 完成后读 state"已够;结构化事件留待未来。 |
| add reference 文件上传的临时区与 core add 衔接 | 实现时明确临时落盘路径 + 调 core add 的契约,加测试覆盖。 |
| 若部分 CLI 逻辑无可复用函数 | 仅在必要时抽成 core 函数,CLI 与 web 共用,不改现有行为。 |

## 10. 交叉链接

- Issue: [#35](https://github.com/xforce-io/kairo/issues/35)
- PR: 待创建后回填
