"""kairo CLI(typer 薄壳)。init / add / step / list / glossary / serve 等。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import typer

from kairo.engine import ProseError
from kairo.engine import accept as engine_accept
from kairo.engine import delete_reference as engine_delete_reference
from kairo.engine import generate_prose as engine_generate_prose
from kairo.engine import re_step as engine_re_step
from kairo.engine import retry_reference as engine_retry_reference
from kairo.engine import run_workspace as engine_run_workspace
from kairo.engine import step as engine_step
from kairo.engine import workspace_run_plan
from kairo.history import diff_worktree, list_snapshots
from kairo.history import rollback as history_rollback
from kairo.provider import select_provider
from kairo.rules import ComposeRule
from kairo.stream_index import write_stream_index
from kairo.workspace import AddError, Workspace, WorkspaceNotFound, delete_workspace

_EPILOG = (
    '快速上手:kairo init "<topic>" → kairo add <file>'
    "(--corpus 标基线,默认 stream 观测)→ kairo step(调和到收敛)。\n\n"
    "多 workspace:kairo list [root] / kairo new \"topic\" / kairo serve [root]。\n\n"
    "产出两层:understanding.md(事实) / assessment.md(判断)。\n\n"
    "心智与协议(两层产出、stream/corpus、fold)定义在 constitution.yaml。"
)

app = typer.Typer(help="step 驱动的增量知识构建引擎", epilog=_EPILOG)
glossary_app = typer.Typer(help="真名册:list / add / rm(workspace 或 shared)")
app.add_typer(glossary_app, name="glossary")


def _open_ws() -> Workspace:
    """打开当前目录的工作区;非工作区给友好提示并非零退出(不吐 traceback)。"""
    try:
        return Workspace.open(Path.cwd())
    except WorkspaceNotFound:
        typer.secho(
            '当前目录不是 kairo 工作区,先运行 kairo init "<topic>"',
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1) from None


def _serve_root(root: Path | None = None) -> Path:
    """解析 serve root:显式参数 → KAIRO_SERVE_ROOT → cwd。"""
    if root is not None:
        return Path(root).expanduser().resolve()
    env = os.environ.get("KAIRO_SERVE_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return Path.cwd().resolve()


def _validate_topic_name(topic: str) -> str:
    """与 Web 新建 workspace 同构的 topic/slug 校验。"""
    topic = topic.strip()
    if not topic:
        raise ValueError("topic 不能为空")
    if len(topic) > 64:
        raise ValueError("topic 过长(≤64)")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in topic):
        raise ValueError("topic 含控制字符")
    if "/" in topic or "\\" in topic or topic.startswith(".") or topic in (".", ".."):
        raise ValueError(f"非法 topic:{topic!r}")
    return topic


@app.command()
def init(topic: str = typer.Argument("main", help="本 workspace 的 topic")) -> None:
    """把当前目录初始化为 topic-workspace + 默认宪法。"""
    Workspace.init(Path.cwd(), topic=topic)
    typer.echo(f"initialized workspace (topic={topic})")


@app.command(name="list")
def list_cmd(
    root: Path = typer.Argument(
        None,
        help="含多个 workspace 的根目录;默认 KAIRO_SERVE_ROOT 或 cwd",
    ),
    as_json: bool = typer.Option(False, "--json", help="JSON 输出(agent 友好)"),
) -> None:
    """#95:列出 serve root 下各 workspace 摘要(与 Web dashboard 同源 discovery)。"""
    from kairo.web.discovery import scan_workspaces

    serve = _serve_root(root)
    if not serve.is_dir():
        typer.secho(f"目录不存在:{serve}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    items = scan_workspaces(serve)
    if as_json:
        typer.echo(
            json.dumps(
                [
                    {
                        "slug": s.slug,
                        "topic": s.topic,
                        "path": s.path,
                        "stream": s.stream_count,
                        "corpus": s.corpus_count,
                        "stale": s.stale_count,
                        "blocked": s.blocked_count,
                    }
                    for s in items
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if not items:
        typer.echo(f"(no workspaces under {serve})")
        return
    typer.echo(f"root={serve}")
    hdr = f"{'SLUG':<24} {'STREAM':>6} {'CORPUS':>6} {'STALE':>5} {'BLOCK':>5}  TOPIC"
    typer.echo(hdr)
    for s in items:
        typer.echo(
            f"{s.slug:<24} {s.stream_count:>6} {s.corpus_count:>6} "
            f"{s.stale_count:>5} {s.blocked_count:>5}  {s.topic}"
        )


@app.command()
def new(
    topic: str = typer.Argument(..., help="新 workspace 的 topic(亦作目录名)"),
    root: Path = typer.Option(
        None, "--root", "-r", help="serve root;默认 KAIRO_SERVE_ROOT 或 cwd"
    ),
) -> None:
    """#95:在 serve root 下新建 workspace 目录并 init(对标 Web 新建)。"""
    try:
        topic = _validate_topic_name(topic)
    except ValueError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    serve = _serve_root(root)
    serve.mkdir(parents=True, exist_ok=True)
    dest = (serve / topic).resolve()
    if dest.parent != serve.resolve():
        typer.secho(f"非法 topic:{topic!r}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if dest.exists():
        typer.secho(f"已存在:{dest}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    Workspace.init(dest, topic=topic)
    typer.echo(f"created {dest}")


@app.command(name="rm-ws")
def rm_ws(
    slug: str = typer.Argument(..., help="要删除的 workspace 目录名(slug)"),
    root: Path = typer.Option(
        None, "--root", "-r", help="serve root;默认 KAIRO_SERVE_ROOT 或 cwd"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认"),
) -> None:
    """#95:删除 serve root 下某个 workspace(不碰 root glossary 与其它 ws)。"""
    serve = _serve_root(root)
    if not yes:
        typer.confirm(f"永久删除 workspace {slug!r} under {serve}?", abort=True)
    try:
        delete_workspace(serve, slug)
    except WorkspaceNotFound:
        typer.secho(f"workspace 不存在:{slug}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    except ValueError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    typer.echo(f"deleted {slug}")


@app.command()
def add(
    files: list[Path],
    ref_id: str = typer.Option(
        None,
        "--id",
        "--to",
        help="指定 ref id;指向已有 id 时追加形态(attach,对标 Web)",
    ),
    role: str = typer.Option(None, "--role", help="覆盖按扩展名猜测的 role"),
    corpus: bool = typer.Option(
        False, "--corpus", help="标为基线参考资料(corpus);默认会议流(stream)"
    ),
    copy: bool = typer.Option(
        False,
        "--copy",
        help="先复制进工作区(.kairo/uploads 或既有 ref 目录)再登记;默认只记路径指针",
    ),
) -> None:
    """登记 reference。文件=指针/可选 copy;目录 stream=一条多形态;目录 --corpus=基线树指针。

    追加到已有参考:`kairo add photo.png --to <ref_id> --copy`(与 Web attach 同路径)。
    """
    ws = _open_ws()
    try:
        rid = ws.add(
            files,
            ref_id=ref_id,
            role=role,
            source_class="corpus" if corpus else None,
            copy=copy,
        )
    except AddError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    typer.echo(f"added {rid}")


@app.command()
def title(
    ref_id: str = typer.Argument(..., help="reference id"),
    name: str = typer.Argument(..., help="新展示名(仅人读,不动 id/目录)"),
) -> None:
    """重命名一条参考的 title(对标 Web 改名;不改 ref_id / 产物溯源)。"""
    ws = _open_ws()
    if ref_id not in ws.list_reference_ids():
        typer.secho(f"reference 不存在:{ref_id}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    try:
        ws.set_title(ref_id, name)
    except ValueError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    typer.echo(f"titled {ref_id} → {name}")


@app.command()
def step() -> None:
    """跑调和循环到收敛(provider 自动选:grok CLI→endpoint→claude CLI→stub;KAIRO_STUB 强制 stub)。

    注意:不自动重试 asr-failed 等终态 blocked;需要时用 run / retry-ref。
    """
    ws = _open_ws()
    progressed = engine_step(ws, select_provider())
    typer.echo("stepped" if progressed else "no change")


@app.command(name="run")
def run_cmd() -> None:
    """#75 推进工作区:有 blocked 则先清终态再 step(与 Web 主按钮一致)。"""
    ws = _open_ws()
    plan = workspace_run_plan(ws)
    if plan["mode"] == "clean":
        typer.echo("up to date")
        return
    progressed = engine_run_workspace(ws, select_provider())
    typer.echo("ran" if progressed else "no change")


@app.command(name="re-step")
def re_step(
    target: str = typer.Argument(None, help="文档 / reference id;省略=全量"),
) -> None:
    """强制重算(文档级=整篇重综合;reference=清派生产物含 blocked 后重跑)。"""
    ws = _open_ws()
    engine_re_step(ws, select_provider(), target)
    typer.echo(f"re-stepped {target or '(all)'}")


@app.command(name="retry-ref")
def retry_ref(ref_id: str = typer.Argument(..., help="reference id")) -> None:
    """重新处理一条参考:清除 transcript/digest 等派生产物(含 asr-failed)后 step。"""
    ws = _open_ws()
    if ref_id not in ws.list_reference_ids():
        typer.secho(f"reference 不存在:{ref_id}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    progressed = engine_retry_reference(ws, select_provider(), ref_id)
    typer.echo("retried" if progressed else "no change")


@app.command(name="rm-ref")
def rm_ref(
    ref_id: str = typer.Argument(..., help="reference id"),
    recompose: bool = typer.Option(
        False, "--recompose", help="删除后立即用剩余参考整篇重综合产物"
    ),
) -> None:
    """#77:永久删除一条参考(摘 folded;默认不改写产物正文)。"""
    ws = _open_ws()
    if ref_id not in ws.list_reference_ids():
        typer.secho(f"reference 不存在:{ref_id}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    try:
        engine_delete_reference(
            ws,
            ref_id,
            recompose=recompose,
            provider=select_provider() if recompose else None,
        )
    except ValueError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    typer.echo(f"deleted {ref_id}" + (" + recomposed" if recompose else ""))


@app.command()
def prose(ref_id: str = typer.Argument(..., help="reference id")) -> None:
    """为单条参考生成可读文稿 prose.md(旁路 normalize 开关,不改 constitution)。"""
    ws = _open_ws()
    try:
        key = engine_generate_prose(ws, select_provider(), ref_id)
    except ProseError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    typer.echo(f"wrote {key}")


@app.command()
def accept(doc: str = typer.Argument(..., help="要接受手改的文档")) -> None:
    """接受手改、钉为新基线,解除 blocked: manual-edit。"""
    ws = _open_ws()
    engine_accept(ws, doc)
    typer.echo(f"accepted {doc}")


@app.command()
def status() -> None:
    """列 references / 各文档融入状态;顶部摘要 topic 与 run plan(stale/blocked)。"""
    ws = _open_ws()
    state = ws.read_state()
    plan = workspace_run_plan(ws)
    typer.echo(
        f"workspace {ws.root.name}  topic={ws.constitution.topic}  "
        f"plan={plan['mode']}  stale={plan['pending_count']}  blocked={plan['blocked_count']}"
    )
    compose = ComposeRule(ws, None)  # 仅用于 corpus 漂移检测(不调 provider)
    for ref_id in ws.list_reference_ids():
        man = ws.read_manifest(ref_id)
        roles = ",".join(f.role for f in man.forms)
        title_s = f" «{man.title}»" if man.title and man.title != ref_id else ""
        blocked = [
            f"{k.rsplit('/', 1)[-1]}:{v.reason}"
            for k, v in state.products.items()
            if k.startswith(f"references/{ref_id}/") and v.status == "blocked"
        ]
        flag = f"  ⚠ {','.join(blocked)}" if blocked else ""
        typer.echo(f"reference {ref_id}{title_s}: [{roles}]{flag}")
    for target in ws.constitution.targets:
        ts = state.targets.get(target.path)
        if ts is None:
            typer.echo(f"target {target.path}: (未生成)")
            continue
        drift = len(ts.folded) - len(ts.last_major_folded)
        flag = f"  ⚠ blocked:{ts.reason}" if ts.status == "blocked" else ""
        if compose.corpus_drifted(target.path, state):
            flag += "  ⚠ corpus 已变,可 re-step 重算"
        typer.echo(
            f"target {target.path}: folded {len(ts.folded)};距上次 A 已 {drift} 条{flag}"
        )


@app.command()
def index() -> None:
    """(重)生成 references/MEETINGS.md —— 按 class 列出 stream(观测)导航索引。"""
    ws = _open_ws()
    path = write_stream_index(ws)
    typer.echo(f"wrote {path.relative_to(ws.root)}")


@app.command()
def history() -> None:
    """列版本快照。"""
    ws = _open_ws()
    for seq in list_snapshots(ws):
        typer.echo(seq)


@app.command()
def rollback(seq: str = typer.Argument(..., help="要回退到的快照 seq")) -> None:
    """回退文档 + targets 段到某版本(references/ 不动,下次 step 重融更晚 digest)。"""
    ws = _open_ws()
    history_rollback(ws, seq)
    typer.echo(f"rolled back to {seq}")


@app.command()
def diff(seq: str = typer.Argument(None, help="对比的快照;省略=最近")) -> None:
    """工作态 vs 版本文档差异(自带,不依赖 git)。"""
    ws = _open_ws()
    typer.echo(diff_worktree(ws, seq) or "(no changes)")


@app.command()
def serve(
    root: Path = typer.Argument(None, help="包含多个 workspace 的根目录;默认 KAIRO_SERVE_ROOT 或 cwd"),
    port: int = typer.Option(8787, "--port", "-p", help="监听端口(默认 8787,避开常见 8000/alfred 8765)"),
) -> None:
    """启动本地 Web Console,浏览器统管 root 下的多个 workspace。"""
    serve_root = _serve_root(root)
    try:
        from kairo.web.server import run as web_run
    except ImportError:
        typer.secho(
            "未安装 web 依赖。请运行:pip install 'kairo[web]'",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1) from None
    typer.echo(f"kairo console: http://127.0.0.1:{port}  (root={serve_root})")
    web_run(serve_root, port=port)


@glossary_app.command("list")
def glossary_list(
    root: Path = typer.Option(
        None, "--root", "-r", help="shared 所在 serve root;默认 ws 父目录 / 环境变量"
    ),
) -> None:
    """列出 machine + shared + workspace 真名册(分层面;空层标注)。"""
    from kairo.glossary import (
        load_glossary_file,
        machine_glossary_path,
        root_glossary_path,
    )

    layers: list[tuple[str, list]] = []
    machine = load_glossary_file(machine_glossary_path())
    layers.append(("machine", machine))

    try:
        ws = Workspace.open(Path.cwd())
        in_ws = True
    except WorkspaceNotFound:
        ws = None
        in_ws = False

    if in_ws:
        serve = root.expanduser().resolve() if root else ws.root.parent
        shared = load_glossary_file(root_glossary_path(serve))
        layers.append(("shared", shared))
        layers.append(("workspace", ws.constitution.glossary))
    else:
        serve = _serve_root(root)
        shared = load_glossary_file(root_glossary_path(serve))
        layers.append(("shared", shared))

    for label, entries in layers:
        typer.echo(f"[{label}] ({len(entries)})")
        if not entries:
            typer.echo("  (empty)")
            continue
        for i, e in enumerate(entries):
            extra = []
            if e.note:
                extra.append(e.note)
            if e.aka:
                extra.append("aka:" + "/".join(e.aka))
            if e.tags:
                extra.append("tags:" + ",".join(e.tags))
            suffix = f"  — {' | '.join(extra)}" if extra else ""
            typer.echo(f"  {i}: {e.name}{suffix}")


@glossary_app.command("add")
def glossary_add(
    name: str = typer.Argument(..., help="规范名"),
    note: str = typer.Option("", "--note", help="grounding 说明"),
    aka: str = typer.Option("", "--aka", help="别名,逗号分隔"),
    tags: str = typer.Option("", "--tags", help="标签,逗号分隔"),
    scope: str = typer.Option(
        "workspace", "--scope", help="workspace(默认) 或 shared(root glossary.yaml)"
    ),
    root: Path = typer.Option(
        None, "--root", "-r", help="scope=shared 时的 serve root"
    ),
) -> None:
    """追加一条真名册;scope=workspace 写 constitution,shared 写 root/glossary.yaml。"""
    from kairo.glossary import (
        add_entry,
        load_glossary_file,
        root_glossary_path,
        save_glossary_file,
    )

    aka_parts = [a.strip() for a in aka.split(",") if a.strip()] if aka else []
    tag_parts = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    try:
        if scope == "workspace":
            ws = _open_ws()
            ws.add_glossary_entry(name, note=note, aka=aka_parts, tags=tag_parts)
            typer.echo(f"added workspace glossary: {name}")
            return
        if scope != "shared":
            raise ValueError(f"未知 scope:{scope!r}(workspace|shared)")
        # shared:在 ws 内默认父目录,否则 --root / KAIRO_SERVE_ROOT
        if root is not None:
            serve = _serve_root(root)
        else:
            try:
                serve = Workspace.open(Path.cwd()).root.parent
            except WorkspaceNotFound:
                serve = _serve_root(None)
        path = root_glossary_path(serve)
        entries = add_entry(
            load_glossary_file(path), name, note=note, aka=aka_parts, tags=tag_parts
        )
        save_glossary_file(path, entries)
        typer.echo(f"added shared glossary: {name} → {path}")
    except ValueError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None


@glossary_app.command("rm")
def glossary_rm(
    index: int = typer.Argument(..., help="条目索引(见 glossary list)"),
    scope: str = typer.Option(
        "workspace", "--scope", help="workspace(默认) 或 shared"
    ),
    root: Path = typer.Option(
        None, "--root", "-r", help="scope=shared 时的 serve root"
    ),
) -> None:
    """按索引删除一条真名册。"""
    from kairo.glossary import (
        load_glossary_file,
        remove_entry,
        root_glossary_path,
        save_glossary_file,
    )

    try:
        if scope == "workspace":
            ws = _open_ws()
            name = ws.constitution.glossary[index].name
            ws.remove_glossary_entry(index)
            typer.echo(f"removed workspace glossary[{index}]: {name}")
            return
        if scope != "shared":
            raise ValueError(f"未知 scope:{scope!r}(workspace|shared)")
        if root is not None:
            serve = _serve_root(root)
        else:
            try:
                serve = Workspace.open(Path.cwd()).root.parent
            except WorkspaceNotFound:
                serve = _serve_root(None)
        path = root_glossary_path(serve)
        entries = load_glossary_file(path)
        name = entries[index].name
        save_glossary_file(path, remove_entry(entries, index))
        typer.echo(f"removed shared glossary[{index}]: {name}")
    except (ValueError, IndexError) as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
