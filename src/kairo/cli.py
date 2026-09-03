"""kairo CLI(typer 薄壳)。init / add / step / list / glossary / serve 等。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import typer

from kairo.install import connect_skill, doctor_lines
from kairo.engine import ProseError
from kairo.engine import accept as engine_accept
from kairo.engine import delete_reference as engine_delete_reference
from kairo.engine import generate_prose as engine_generate_prose
from kairo.engine import has_provider_failed
from kairo.engine import promote_oversized_degraded
from kairo.engine import re_step as engine_re_step
from kairo.engine import retry_reference as engine_retry_reference
from kairo.engine import run_workspace as engine_run_workspace
from kairo.engine import step as engine_step
from kairo.engine import workspace_run_plan
from kairo.history import diff_worktree, list_snapshots
from kairo.history import rollback as history_rollback
from kairo.provider import select_provider
from kairo.rules import (
    REASON_COMPOSE_MIGRATION_REQUIRED,
    REASON_COMPOSE_OVER_BUDGET,
    ComposeRule,
    effective_compose_block_reason,
)
from kairo.stream_index import write_stream_index
from kairo.archive import ArchiveError, NeedChoice, archive_markdown
from kairo.review import (
    ReviewError,
    collect_digests,
    generate_review_body,
    occupied_span,
    prepare_range,
    resolve_review_workspace,
    write_review_reference,
)
from kairo.timeline import (
    MAX_RANGE_DAYS,
    filter_range,
    format_cli_timeline,
    item_as_json,
    parse_calendar_date,
    scan_timeline,
)
from kairo.workspace import AddError, Workspace, WorkspaceNotFound, delete_workspace

_EPILOG = (
    '快速上手:kairo init "<topic>" → kairo add <file>'
    "(--corpus 标基线,默认 stream 观测)→ kairo step(调和到收敛)。\n\n"
    "多 workspace:kairo list [root] / kairo new \"topic\" / kairo serve [root]。\n\n"
    "产出 understanding.md(中立事实)。\n\n"
    "心智与协议(stream/corpus、fold)定义在 constitution.yaml。"
)

app = typer.Typer(help="step 驱动的增量知识构建引擎", epilog=_EPILOG)
glossary_app = typer.Typer(help="兼容别名：知识条目 list / add / rm(workspace 或 shared)")
knowledge_app = typer.Typer(help="知识条目：list / add / rm(workspace 或 global)")
app.add_typer(glossary_app, name="glossary")
app.add_typer(knowledge_app, name="knowledge")
backup_app = typer.Typer(help="remote 完整备份:push / verify / restore")
app.add_typer(backup_app, name="backup")
project_app = typer.Typer(help="Project：创建、关联 Topic、查看")
app.add_typer(project_app, name="project")
tag_app = typer.Typer(help="Ref Tag：add / rm / list")
app.add_typer(tag_app, name="tag")
include_app = typer.Typer(help="Topic 包含规则：set / clear / show")
app.add_typer(include_app, name="include")
settings_app = typer.Typer(help="本机 Settings：分区与连接健康")
app.add_typer(settings_app, name="settings")
datasource_app = typer.Typer(help="Project 数据源")
app.add_typer(datasource_app, name="datasource")
task_app = typer.Typer(help="Task / Run / Artifact")
app.add_typer(task_app, name="task")
artifact_app = typer.Typer(help="阅读 Artifact")
app.add_typer(artifact_app, name="artifact")


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


def _serve_root(root: Path | None = None, *, follow: bool = True) -> Path:
    """解析 serve root:显式参数 → KAIRO_SERVE_ROOT → cwd。

    public-read 经 current 跟随数据根时 follow=False,避免启动时 resolve 钉死 generation。
    """
    if root is not None:
        path = Path(root).expanduser()
    else:
        env = os.environ.get("KAIRO_SERVE_ROOT")
        path = Path(env).expanduser() if env else Path.cwd()
    return path.resolve() if follow else path


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
def archive(
    session: Path = typer.Argument(..., help="会话 Markdown 路径; - 表示 stdin"),
    root: Path = typer.Option(
        None, "--root", "-r", help="serve root;默认 KAIRO_SERVE_ROOT 或 cwd"
    ),
    workspace: str = typer.Option(
        None, "--workspace", help="目标 workspace slug;续接时可省略,取回执中的值"
    ),
    create: bool = typer.Option(False, "--create", help="在 --workspace 下新建归档"),
    bind: str = typer.Option(
        None, "--bind", help="覆盖该 workspace 中已有归档 reference"
    ),
    title: str = typer.Option(None, "--title", help="仅新建时的展示名"),
    as_json: bool = typer.Option(False, "--json", help="成功时 stdout 为 JSON 对象"),
) -> None:
    """把 coding agent 会话 Markdown 归档到指定 workspace(#136)。"""
    serve = _serve_root(root)
    if str(session) == "-":
        text = sys.stdin.read()
    else:
        try:
            text = session.expanduser().read_text(encoding="utf-8")
        except OSError as e:
            typer.secho(f"无法读取会话文件:{e}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from None
    try:
        rec = archive_markdown(
            text,
            serve_root=serve,
            workspace=workspace,
            create=create,
            bind=bind,
            title=title,
        )
    except NeedChoice as e:
        typer.echo(
            json.dumps(
                {
                    "ok": False,
                    "reason": e.reason,
                    "workspaces": e.workspaces,
                    "archives": e.archives,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        raise typer.Exit(2) from None
    except ArchiveError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    except OSError as e:
        typer.secho(f"归档写入失败:{e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "ok": True,
                    "receipt": rec.envelope(),
                    "workspace": rec.workspace,
                    "reference": rec.reference,
                    "form_index": rec.form_index,
                    "version": rec.version,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    typer.echo(rec.envelope())


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
    occurred_at: str = typer.Option(
        None, "--occurred", help="发生日 YYYY-MM-DD;不改 id"
    ),
    root: Path = typer.Option(
        None, "--root", "-r", help="serve root;非 Topic 目录时写入全局库"
    ),
) -> None:
    """登记 Ref。cwd 为 Topic 则 home 在该 Topic;否则写入全局库。"""
    from kairo.refs import add_global_ref
    from kairo.workspace import AddError, Workspace, WorkspaceNotFound

    try:
        ws = Workspace.open(Path.cwd())
    except WorkspaceNotFound:
        ws = None
    try:
        if occurred_at and corpus:
            raise AddError("fold=false 不能设发生时间")
        if ws is not None:
            rid = ws.add(
                files,
                ref_id=ref_id,
                role=role,
                source_class="corpus" if corpus else None,
                copy=copy,
                occurred_at=occurred_at,
            )
        else:
            rid = add_global_ref(
                _serve_root(root),
                files,
                ref_id=ref_id,
                role=role,
                source_class="corpus" if corpus else None,
                copy=copy,
                occurred_at=occurred_at,
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
def occurred(
    ref_id: str = typer.Argument(..., help="reference id"),
    day: str = typer.Argument(None, help="发生日 YYYY-MM-DD"),
    clear: bool = typer.Option(False, "--clear", help="清空手改发生时间"),
) -> None:
    """修正或清空一条参考的发生时间(不改 id,不 step)。"""
    ws = _open_ws()
    if ref_id not in ws.list_reference_ids():
        typer.secho(f"reference 不存在:{ref_id}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if clear:
        if day:
            typer.secho("--clear 与日期互斥", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        parsed = None
    else:
        parsed = parse_calendar_date(day)
        if parsed is None:
            typer.secho(f"非法发生时间:{day}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
    try:
        ws.set_occurred(ref_id, parsed)
    except ValueError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    typer.echo(f"occurred {ref_id} → {parsed.isoformat() if parsed else '-'}")


@app.command()
def timeline(
    root: Path = typer.Argument(
        None,
        help="含多个 workspace 的根目录;默认 KAIRO_SERVE_ROOT 或 cwd",
    ),
    day: str = typer.Option(None, "--day", help="只列出该发生日"),
    recent: bool = typer.Option(False, "--recent", help="按录入时间倒序"),
    from_day: str = typer.Option(None, "--from", help="区间起(发生日)"),
    to_day: str = typer.Option(None, "--to", help="区间止(发生日)"),
    as_json: bool = typer.Option(False, "--json", help="JSON 输出"),
    tags: list[str] = typer.Option(None, "--tag", help="按 Tag 筛选,可重复;多 Tag 为 AND"),
) -> None:
    """跨 Topic 按发生日列出全部可访问 Ref;--recent 按录入时间。"""
    if day and recent:
        typer.secho("--day 与 --recent 互斥", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if (from_day or to_day) and (day or recent):
        typer.secho("--from/--to 与 --day/--recent 互斥", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    parsed = None
    start = end = None
    if from_day or to_day:
        if not from_day or not to_day:
            typer.secho("--from 与 --to 必须成对", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        start = parse_calendar_date(from_day)
        end = parse_calendar_date(to_day)
        if start is None or end is None:
            typer.secho("非法发生时间", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        if start > end:
            start, end = end, start
    if day:
        parsed = parse_calendar_date(day)
        if parsed is None:
            typer.secho(f"非法发生时间:{day}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
    serve = _serve_root(root)
    if not serve.is_dir():
        typer.secho(f"目录不存在:{serve}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    items = scan_timeline(serve)
    from kairo.timeline import filter_by_tags

    if tags:
        items = filter_by_tags(items, list(tags))
    if start is not None and end is not None:
        items = filter_range(items, start, end)
    if as_json:
        if parsed is not None:
            items = [it for it in items if it.occurred_at == parsed]
        if recent:
            items = sorted(items, key=lambda it: it.added_at, reverse=True)
        typer.echo(json.dumps([item_as_json(it) for it in items], ensure_ascii=False))
        return
    typer.echo(format_cli_timeline(items, recent=recent, day=parsed), nl=False)


@app.command()
def review(
    from_day: str = typer.Option(..., "--from", help="区间起"),
    to_day: str = typer.Option(..., "--to", help="区间止"),
    workspace: str = typer.Option(None, "--workspace", "-w", help="可选落点 slug;缺省写入「总结」"),
    root: Path = typer.Option(
        None,
        "--root",
        "-r",
        help="serve root;默认 KAIRO_SERVE_ROOT 或 cwd",
    ),
) -> None:
    """按发生日闭区间生成回顾,默认写入「总结」workspace 的一条 stream reference。"""
    start = parse_calendar_date(from_day)
    end = parse_calendar_date(to_day)
    if start is None or end is None:
        typer.secho("非法发生时间", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if start > end:
        start, end = end, start
    serve = _serve_root(root)
    try:
        found = prepare_range(scan_timeline(serve), start, end, root=serve)
        with_d, without = collect_digests(serve, found)
        body = generate_review_body(
            with_d, without, artifact_dir=serve / ".kairo" / "review-work"
        )
        ws = resolve_review_workspace(serve, workspace or "")
        occ = occupied_span([it for it, _ in with_d]) or (start, end)
        rid = write_review_reference(ws, occ[0], occ[1], body, occurred=end)
    except WorkspaceNotFound:
        typer.secho(f"workspace 不存在:{workspace}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    except ReviewError as e:
        msg = {
            "range-too-long": f"一次最多 {MAX_RANGE_DAYS} 天",
            "empty-range": "这段时间没有观测",
            "no-digest": "还没有纪要，写不出回顾",
            "empty": "回顾生成失败",
        }.get(str(e), str(e))
        typer.secho(msg, fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from e
    typer.echo(f"review {rid} → {ws.root.name}")


def _exit_if_run_failed(ws: Workspace) -> None:
    """provider 或终态 target blocked 后非零退出,避免 CLI/Web 假成功。"""
    promote_oversized_degraded(ws)
    if has_provider_failed(ws):
        typer.secho(
            "Error: provider-failed — see kairo status / Web blocks",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    plan = workspace_run_plan(ws)
    blocked = [item for item in plan["blocked_targets"] if not item["retryable"]]
    if blocked:
        item = blocked[0]
        hint = (
            " — run `kairo re-step understanding.md` after confirming full "
            "re-synthesis will compress history; failures keep the old version"
            if item["reason"]
            in (REASON_COMPOSE_MIGRATION_REQUIRED, REASON_COMPOSE_OVER_BUDGET)
            else " — see kairo status"
        )
        typer.secho(
            f"Error: {item['path']} blocked:{item['reason']}{hint}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    product_blocked = [
        item for item in plan["blocked_refs"] if not item.get("retryable", True)
    ]
    if product_blocked:
        item = product_blocked[0]
        reason = item["blocks"][0]["reason"] if item["blocks"] else "blocked"
        typer.secho(
            f"Error: {item['ref_id']} blocked:{reason} — see kairo status",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)


@app.command()
def step() -> None:
    """跑调和循环到收敛(provider auto:codex→grok→claude→endpoint→stub;材料路径跳过不兼容者)。

    注意:不自动重试 asr-failed 等终态 blocked;需要时用 run / retry-ref。
    provider-failed 时非零退出(#105),便于 Web Run 显示失败而非 Running/假成功。
    """
    ws = _open_ws()
    progressed = engine_step(ws, select_provider(require_read_dirs=True))
    typer.echo("stepped" if progressed else "no change")
    _exit_if_run_failed(ws)


@app.command(name="run")
def run_cmd() -> None:
    """#75 推进工作区:有 blocked 则先清终态再 step(与 Web 主按钮一致)。"""
    ws = _open_ws()
    plan = workspace_run_plan(ws)
    if plan["mode"] == "clean":
        typer.echo("up to date")
        return
    if plan["mode"] == "attention":
        _exit_if_run_failed(ws)
    progressed = engine_run_workspace(
        ws, select_provider(require_read_dirs=True)
    )
    typer.echo("ran" if progressed else "no change")
    _exit_if_run_failed(ws)


@app.command(name="re-step")
def re_step(
    target: str = typer.Argument(None, help="文档 / reference id;省略=全量"),
) -> None:
    """强制重算(文档级=整篇重综合;reference=清派生产物含 blocked 后重跑)。"""
    ws = _open_ws()
    engine_re_step(ws, select_provider(require_read_dirs=True), target)
    _exit_if_run_failed(ws)
    typer.echo(f"re-stepped {target or '(all)'}")


@app.command(name="retry-ref")
def retry_ref(ref_id: str = typer.Argument(..., help="reference id")) -> None:
    """重新处理一条参考:清除 transcript/digest 等派生产物(含 asr-failed)后 step。"""
    ws = _open_ws()
    if ref_id not in ws.list_reference_ids():
        typer.secho(f"reference 不存在:{ref_id}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    progressed = engine_retry_reference(
        ws, select_provider(require_read_dirs=True), ref_id
    )
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
            provider=select_provider(require_read_dirs=True) if recompose else None,
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


def _format_block_diag(reason: str | None, diagnostic) -> str:
    """#98:blocked 原因 + 可选 stage/provider/summary 安全诊断。"""
    base = reason or "blocked"
    if diagnostic is None:
        return base
    bits = [base]
    if diagnostic.stage:
        bits.append(f"stage={diagnostic.stage}")
    if diagnostic.provider:
        bits.append(f"provider={diagnostic.provider}")
    if diagnostic.summary:
        bits.append(diagnostic.summary)
    return " ".join(bits)


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
            f"{k.rsplit('/', 1)[-1]}:{_format_block_diag(v.reason, v.diagnostic)}"
            for k, v in state.products.items()
            if k.startswith(f"references/{ref_id}/") and v.status == "blocked"
        ]
        flag = f"  ⚠ {','.join(blocked)}" if blocked else ""
        typer.echo(f"reference {ref_id}{title_s}: [{roles}]{flag}")
    for target in ws.constitution.live_targets():
        ts = state.targets.get(target.path)
        if ts is None:
            typer.echo(f"target {target.path}: (未生成)")
            continue
        drift = len(ts.folded) - len(ts.last_major_folded)
        reason = effective_compose_block_reason(ws, target.path, ts)
        flag = (
            f"  ⚠ blocked:{_format_block_diag(reason, ts.diagnostic)}"
            if ts.status == "blocked"
            else ""
        )
        if reason in (
            REASON_COMPOSE_MIGRATION_REQUIRED,
            REASON_COMPOSE_OVER_BUDGET,
        ):
            flag += "；确认压缩历史正文后运行 kairo re-step understanding.md（失败保留旧版）"
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
    mode: str = typer.Option(
        "console",
        "--mode",
        help="console=本地 Console(默认); public-read=匿名公开只读面(#118)",
    ),
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help="监听地址;console 仅回环;public-read 可 0.0.0.0(#155)",
    ),
) -> None:
    """启动 Web 服务。默认本地 Console;``--mode public-read`` 仅挂匿名公开只读面。"""
    if mode not in {"console", "public-read"}:
        typer.secho(
            f"未知 mode: {mode}(期望 console 或 public-read)",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)
    loopback = {"127.0.0.1", "::1", "localhost"}
    if mode == "console" and host not in loopback:
        typer.secho("console 只能绑定回环地址", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)
    serve_root = _serve_root(root, follow=(mode != "public-read"))
    if mode == "public-read" and not Path(serve_root).is_dir():
        typer.secho(f"数据根不是目录:{serve_root}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)
    try:
        from kairo.web.server import run as web_run
    except ImportError:
        typer.secho(
            "未安装 web 依赖。请运行:uv tool install 'git+https://github.com/xforce-io/kairo.git[web]'",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1) from None
    label = "public-read" if mode == "public-read" else "console"
    typer.echo(f"kairo {label}: http://{host}:{port}  (root={serve_root})")
    web_run(serve_root, port=port, mode=mode, host=host)  # type: ignore[arg-type]


@app.command()
def doctor() -> None:
    """本机体检:版本 / provider / ASR / web extra / skill 挂载。只读,不改配置。"""
    for line in doctor_lines():
        typer.echo(line)


@app.command()
def connect() -> None:
    """把自带 operator skill 挂到 ~/.agents/skills/kairo,并对已装的 Claude/Cursor/Codex/Pi 建链。"""
    try:
        for line in connect_skill():
            typer.echo(line)
    except FileNotFoundError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None


def _stamp_serve_workspaces(serve: Path) -> None:
    from kairo.workspace import stamp_serve_workspaces

    stamp_serve_workspaces(serve)


def _backup_fail(exc) -> None:
    typer.secho(f"{exc.stage}: {exc.message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(exc.code) from None


@backup_app.command("push")
def backup_push(
    remote: str = typer.Argument(..., help="config.toml [remote.<name>]"),
    root: Path = typer.Argument(
        None, help="serve root;默认 KAIRO_SERVE_ROOT 或 cwd"
    ),
) -> None:
    """把 serve root 完整备份到 remote 并原子切换 current(#154/#156)。"""
    from kairo.backup import BackupError, push_named

    try:
        result = push_named(remote, _serve_root(root))
    except BackupError as exc:
        _backup_fail(exc)
    typer.echo(
        f"{result.status} remote={remote} backup_id={result.backup_id} "
        f"files={result.files} bytes={result.bytes}"
    )


@backup_app.command("status")
def backup_status(
    remote: str = typer.Argument(..., help="config.toml [remote.<name>]"),
) -> None:
    """显示该 remote 最近结果(#156)。"""
    from kairo.backup import BackupError, read_result

    try:
        data = read_result(remote)
    except BackupError as exc:
        _backup_fail(exc)
    if data is None:
        typer.echo(f"empty remote={remote}")
        return
    typer.echo(
        f"status={data.get('status')} last_attempt_at={data.get('last_attempt_at')} "
        f"last_success_at={data.get('last_success_at') or '-'} "
        f"backup_id={data.get('backup_id') or '-'}"
    )


@backup_app.command("verify")
def backup_verify(
    remote: str = typer.Argument(..., help="config.toml [remote.<name>]"),
    backup_id: str = typer.Option(None, "--backup-id", help="默认 current"),
) -> None:
    """校验 remote 上 current 或指定 generation(#154)。"""
    from kairo.backup import BackupError, load_remote, verify_remote

    try:
        spec = load_remote(remote)
        result = verify_remote(spec, backup_id)
    except BackupError as exc:
        _backup_fail(exc)
    typer.echo(
        f"ok backup_id={result.backup_id} files={result.files} bytes={result.bytes}"
    )


@backup_app.command("restore")
def backup_restore(
    remote: str = typer.Argument(..., help="config.toml [remote.<name>]"),
    dest: Path = typer.Argument(..., help="空目标目录"),
    backup_id: str = typer.Option(None, "--backup-id", help="默认 current"),
) -> None:
    """把 remote generation 恢复到空目录(#154)。"""
    from kairo.backup import BackupError, load_remote, restore_remote

    try:
        spec = load_remote(remote)
        result = restore_remote(spec, dest, backup_id)
    except BackupError as exc:
        _backup_fail(exc)
    typer.echo(
        f"restored backup_id={result.backup_id} dest={dest} "
        f"files={result.files} bytes={result.bytes}"
    )


@glossary_app.command("list")
def glossary_list(
    root: Path = typer.Option(
        None, "--root", "-r", help="shared 所在 serve root;默认 ws 父目录 / 环境变量"
    ),
) -> None:
    """列出 global + workspace 知识；glossary 是兼容命令名。"""
    from kairo.glossary import machine_migration_hint, resolve_serve_root
    from kairo.knowledge import KnowledgeError, effective_entries

    try:
        hint = machine_migration_hint()
        if hint:
            typer.secho(hint, fg=typer.colors.YELLOW, err=True)
        try:
            ws = Workspace.open(Path.cwd())
            in_ws = True
        except WorkspaceNotFound:
            ws = None
            in_ws = False

        layers: list[tuple[str, list]] = []
        if in_ws:
            serve = resolve_serve_root(ws_root=ws.root, explicit=root)
            from kairo.knowledge import load_global, load_workspace

            layers.append(("global", load_global(serve)[0].entries))
            layers.append(("workspace", load_workspace(ws.root)[0].entries))
        else:
            serve = resolve_serve_root(explicit=root)
            from kairo.knowledge import load_global

            layers.append(("global", load_global(serve)[0].entries))

        for label, entries in layers:
            typer.echo(f"[{label}] ({len(entries)})")
            if label == "global":
                typer.echo("[shared] compatibility name for global")
            if not entries:
                typer.echo("  (empty)")
                continue
            for i, e in enumerate(entries):
                extra = []
                if e.description:
                    extra.append(e.description)
                if e.aliases:
                    extra.append("aka:" + "/".join(a.value for a in e.aliases))
                if e.tags:
                    extra.append("tags:" + ",".join(e.tags))
                suffix = f"  — {' | '.join(extra)}" if extra else ""
                typer.echo(f"  {i}: {e.title} [{e.id}]{suffix}")
        if in_ws:
            items = effective_entries(serve, ws.root)
            from kairo.glossary import current_effective_hash

            typer.echo(f"[effective] ({len(items)}) {current_effective_hash(ws.root, serve_root=serve)}")
            for it in items:
                typer.echo(f"  {it.scope}: {it.title} [{it.id}]")
    except KnowledgeError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None


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
    """追加一条知识条目；旧 glossary 命令写入同一 KnowledgeStore。"""
    from kairo.glossary import parse_scope, resolve_serve_root
    from kairo.knowledge import KnowledgeAlias, KnowledgeError, migrate_global, migrate_workspace, new_entry, save_global, save_workspace, validate_entries

    aka_parts = [a.strip() for a in aka.split(",") if a.strip()] if aka else []
    tag_parts = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    try:
        chosen = "shared" if scope.strip() == "global" else parse_scope(scope)
        if chosen == "workspace":
            ws = _open_ws()
            document = migrate_workspace(ws.root)
            entry = new_entry(title=name, scope="workspace", description=note, aliases=[KnowledgeAlias(value=value) for value in aka_parts], tags=tag_parts)
            validate_entries([*document.entries, entry], scope="workspace")
            document.entries.append(entry)
            save_workspace(ws.root, document)
            typer.echo(f"added workspace knowledge: {name}")
            return
        try:
            ws = Workspace.open(Path.cwd())
            serve = resolve_serve_root(ws_root=ws.root, explicit=root)
        except WorkspaceNotFound:
            serve = resolve_serve_root(explicit=root)
        document = migrate_global(serve)
        entry = new_entry(title=name, scope="global", description=note, aliases=[KnowledgeAlias(value=value) for value in aka_parts], tags=tag_parts)
        validate_entries([*document.entries, entry], scope="global")
        document.entries.append(entry)
        save_global(serve, document)
        _stamp_serve_workspaces(serve)
        typer.echo(f"added global knowledge: {name}")
    except (KnowledgeError, ValueError) as e:
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
    """按索引删除一条知识；旧 glossary 命令是兼容别名。"""
    from kairo.glossary import parse_scope, resolve_serve_root
    from kairo.knowledge import KnowledgeError, migrate_global, migrate_workspace, save_global, save_workspace

    try:
        chosen = "shared" if scope.strip() == "global" else parse_scope(scope)
        if chosen == "workspace":
            ws = _open_ws()
            document = migrate_workspace(ws.root)
            name = document.entries[index].title
            document.entries.pop(index)
            save_workspace(ws.root, document)
            typer.echo(f"removed workspace knowledge[{index}]: {name}")
            return
        try:
            ws = Workspace.open(Path.cwd())
            serve = resolve_serve_root(ws_root=ws.root, explicit=root)
        except WorkspaceNotFound:
            serve = resolve_serve_root(explicit=root)
        document = migrate_global(serve)
        name = document.entries[index].title
        document.entries.pop(index)
        save_global(serve, document)
        _stamp_serve_workspaces(serve)
        typer.echo(f"removed global knowledge[{index}]: {name}")
    except (KnowledgeError, ValueError, IndexError) as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None


@knowledge_app.command("list")
def knowledge_list(root: Path = typer.Option(None, "--root", "-r", help="global 所在 serve root")) -> None:
    """列出知识（等价于兼容命令 `glossary list`）。"""
    glossary_list(root=root)


@knowledge_app.command("add")
def knowledge_add(
    title: str = typer.Argument(..., help="规范标题"),
    description: str = typer.Option("", "--description", "--note", help="简短说明"),
    aliases: str = typer.Option("", "--aliases", "--aka", help="别名，逗号分隔"),
    tags: str = typer.Option("", "--tags", help="标签，逗号分隔"),
    scope: str = typer.Option("workspace", "--scope", help="workspace 或 global（shared 兼容）"),
    root: Path = typer.Option(None, "--root", "-r", help="scope=global 时的 serve root"),
) -> None:
    """创建知识条目。"""
    glossary_add(name=title, note=description, aka=aliases, tags=tags, scope=scope, root=root)


@knowledge_app.command("rm")
def knowledge_rm(
    index: int = typer.Argument(..., help="条目索引（见 knowledge list）"),
    scope: str = typer.Option("workspace", "--scope", help="workspace 或 global（shared 兼容）"),
    root: Path = typer.Option(None, "--root", "-r", help="scope=global 时的 serve root"),
) -> None:
    """删除知识条目。"""
    glossary_rm(index=index, scope=scope, root=root)


def _dump(as_json: bool, payload) -> None:
    if as_json:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


def _cli_root(root: Path | None) -> Path:
    return _serve_root(root)


@tag_app.command("add")
def tag_add_cmd(
    ref_id: str = typer.Argument(...),
    tag: str = typer.Argument(...),
    home: str = typer.Option("", "--home", help="Topic slug;省略表示全局 Ref"),
    root: Path = typer.Option(None, "--root", "-r"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    from kairo.refs import RefError, add_tag

    try:
        tags = add_tag(_cli_root(root), home=home, ref_id=ref_id, tag=tag)
    except RefError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    if as_json:
        typer.echo(json.dumps({"ok": True, "tags": tags}, ensure_ascii=False))
        return
    typer.echo(f"tagged {ref_id} +{tag}")


@tag_app.command("rm")
def tag_rm_cmd(
    ref_id: str = typer.Argument(...),
    tag: str = typer.Argument(...),
    home: str = typer.Option("", "--home", help="Topic slug;省略表示全局 Ref"),
    root: Path = typer.Option(None, "--root", "-r"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    from kairo.refs import RefError, remove_tag

    try:
        tags = remove_tag(_cli_root(root), home=home, ref_id=ref_id, tag=tag)
    except RefError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    if as_json:
        typer.echo(json.dumps({"ok": True, "tags": tags}, ensure_ascii=False))
        return
    typer.echo(f"untagged {ref_id} -{tag}")


@tag_app.command("list")
def tag_list_cmd(
    root: Path = typer.Option(None, "--root", "-r"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    from kairo.refs import list_tags

    tags = list_tags(_cli_root(root))
    if as_json:
        typer.echo(json.dumps({"ok": True, "tags": tags}, ensure_ascii=False))
        return
    typer.echo("\n".join(tags) if tags else "(no tags)")


@include_app.command("set")
def include_set_cmd(
    tags: list[str] = typer.Argument(..., help="包含的 Tag,命中任一即进入"),
    root: Path = typer.Option(None, "--root", "-r"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    from kairo.refs import RefError, set_include_tags

    ws = _open_ws()
    serve = ws.root.parent
    try:
        saved = set_include_tags(serve, ws.root.name, list(tags))
    except RefError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    if as_json:
        typer.echo(json.dumps({"ok": True, "include_tags": saved}, ensure_ascii=False))
        return
    typer.echo("include " + " ".join(saved or []))


@include_app.command("clear")
def include_clear_cmd(
    root: Path = typer.Option(None, "--root", "-r"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    from kairo.refs import RefError, set_include_tags

    ws = _open_ws()
    try:
        saved = set_include_tags(ws.root.parent, ws.root.name, [])
    except RefError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    if as_json:
        typer.echo(json.dumps({"ok": True, "include_tags": saved}, ensure_ascii=False))
        return
    typer.echo("include cleared")


@include_app.command("show")
def include_show_cmd(as_json: bool = typer.Option(False, "--json")) -> None:
    from kairo.refs import include_tags_of, topic_members

    ws = _open_ws()
    rules = include_tags_of(ws)
    members = topic_members(ws.root.parent, ws.root.name)
    payload = {
        "include_tags": rules,
        "members": [{"home": m.home, "id": m.id, "title": m.title} for m in members],
    }
    if as_json:
        typer.echo(json.dumps(payload, ensure_ascii=False))
        return
    if rules is None:
        typer.echo("include (compat home)")
    elif not rules:
        typer.echo("include (empty)")
    else:
        typer.echo("include " + " ".join(rules))
    for m in members:
        typer.echo(f"  {m.home or 'global'}  {m.id}")


@project_app.command("list")
def project_list(
    root: Path = typer.Option(None, "--root", "-r"),
    as_json: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    from kairo.projects import list_projects, project_to_dict

    items = [project_to_dict(p) for p in list_projects(_cli_root(root))]
    _dump(as_json, items)


@project_app.command("create")
def project_create(
    name: str = typer.Argument(...),
    root: Path = typer.Option(None, "--root", "-r"),
    as_json: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    from kairo.projects import ProjectError, create_project, project_to_dict

    try:
        project = create_project(_cli_root(root), name)
    except ProjectError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    _dump(as_json, project_to_dict(project))


@project_app.command("show")
def project_show(
    project_id: str = typer.Argument(...),
    root: Path = typer.Option(None, "--root", "-r"),
    as_json: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    from kairo.projects import ProjectError, get_project, project_to_dict

    try:
        _dump(as_json, project_to_dict(get_project(_cli_root(root), project_id)))
    except ProjectError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None


@project_app.command("edit")
def project_edit_cmd(
    project_id: str = typer.Argument(...),
    name: str = typer.Option(..., "--name"),
    root: Path = typer.Option(None, "--root", "-r"),
    as_json: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    from kairo.projects import ProjectError, edit_project, project_to_dict

    try:
        _dump(as_json, project_to_dict(edit_project(_cli_root(root), project_id, name=name)))
    except ProjectError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None


@project_app.command("link")
def project_link(
    project_id: str = typer.Argument(...),
    slugs: list[str] = typer.Argument(..., help="一个或多个已有 workspace slug"),
    root: Path = typer.Option(None, "--root", "-r"),
    as_json: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    from kairo.projects import ProjectError, link_workspaces, project_to_dict

    serve = _cli_root(root)
    try:
        project = link_workspaces(serve, project_id, slugs)
        _dump(as_json, project_to_dict(project))
    except ProjectError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None


@project_app.command("unlink")
def project_unlink(
    project_id: str = typer.Argument(...),
    slug: str = typer.Argument(...),
    root: Path = typer.Option(None, "--root", "-r"),
    as_json: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    from kairo.projects import ProjectError, project_to_dict, unlink_workspace

    try:
        _dump(as_json, project_to_dict(unlink_workspace(_cli_root(root), project_id, slug)))
    except ProjectError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None


@settings_app.command("show")
def settings_show(as_json: bool = typer.Option(True, "--json/--no-json")) -> None:
    from kairo.settings import SettingsError, as_public_dict

    try:
        _dump(as_json, as_public_dict())
    except SettingsError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None


@settings_app.command("set")
def settings_set_cmd(
    path: str = typer.Argument(..., help="如 connections.tencent-docs.authorized"),
    value: str = typer.Argument(...),
    as_json: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    from kairo.settings import SettingsError, as_public_dict, set_dotted

    try:
        set_dotted(path, value)
        _dump(as_json, as_public_dict())
    except SettingsError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None


@datasource_app.command("add")
def datasource_add(
    project_id: str = typer.Argument(...),
    url: str = typer.Option(..., "--url"),
    kind: str = typer.Option(None, "--kind", help="可选；默认由 URL 推断，不必填 spreadsheet"),
    purpose: str = typer.Option("", "--purpose"),
    root: Path = typer.Option(None, "--root", "-r"),
    as_json: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    from kairo.projects import ProjectError, add_datasource

    try:
        ds = add_datasource(_cli_root(root), project_id, url=url, kind=kind, purpose=purpose)
    except ProjectError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    _dump(as_json, ds.model_dump())


@datasource_app.command("read")
def datasource_read_cmd(
    project_id: str = typer.Argument(...),
    ds_id: str = typer.Argument(...),
    root: Path = typer.Option(None, "--root", "-r"),
    as_json: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    from kairo.projects import ProjectError, read_project_datasource
    from kairo.readers import ReadError

    try:
        text = read_project_datasource(_cli_root(root), project_id, ds_id)
    except (ProjectError, ReadError) as e:
        payload = {"ok": False, "code": getattr(e, "code", "error"), "error": str(e)}
        _dump(as_json, payload)
        raise typer.Exit(1) from None
    _dump(as_json, {"ok": True, "content": text})


@datasource_app.command("rm")
def datasource_rm(
    project_id: str = typer.Argument(...),
    ds_id: str = typer.Argument(...),
    root: Path = typer.Option(None, "--root", "-r"),
    as_json: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    from kairo.projects import ProjectError, project_to_dict, remove_datasource

    try:
        _dump(as_json, project_to_dict(remove_datasource(_cli_root(root), project_id, ds_id)))
    except ProjectError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None


@task_app.command("create")
def task_create_cmd(
    project_id: str = typer.Argument(...),
    name: str = typer.Option(..., "--name"),
    datasource_id: str = typer.Option(..., "--datasource"),
    schedule: str = typer.Option("once", "--schedule"),
    interval_hours: int = typer.Option(None, "--interval-hours"),
    root: Path = typer.Option(None, "--root", "-r"),
    as_json: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    from kairo.projects import ProjectError, create_task

    try:
        task = create_task(
            _cli_root(root),
            project_id,
            name=name,
            datasource_id=datasource_id,
            schedule=schedule,
            interval_hours=interval_hours,
        )
    except ProjectError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    _dump(as_json, task.model_dump())


@task_app.command("edit")
def task_edit_cmd(
    project_id: str = typer.Argument(...),
    task_id: str = typer.Argument(...),
    name: str = typer.Option(None, "--name"),
    schedule: str = typer.Option(None, "--schedule"),
    enabled: bool = typer.Option(None, "--enabled/--disabled"),
    root: Path = typer.Option(None, "--root", "-r"),
    as_json: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    from kairo.projects import ProjectError, edit_task

    try:
        task = edit_task(
            _cli_root(root),
            project_id,
            task_id,
            name=name,
            schedule=schedule,
            enabled=enabled,
        )
    except ProjectError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    _dump(as_json, task.model_dump())


@task_app.command("enable")
def task_enable_cmd(
    project_id: str = typer.Argument(...),
    task_id: str = typer.Argument(...),
    root: Path = typer.Option(None, "--root", "-r"),
    as_json: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    from kairo.projects import ProjectError, edit_task

    try:
        task = edit_task(_cli_root(root), project_id, task_id, enabled=True)
    except ProjectError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    _dump(as_json, task.model_dump())


@task_app.command("disable")
def task_disable_cmd(
    project_id: str = typer.Argument(...),
    task_id: str = typer.Argument(...),
    root: Path = typer.Option(None, "--root", "-r"),
    as_json: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    from kairo.projects import ProjectError, edit_task

    try:
        task = edit_task(_cli_root(root), project_id, task_id, enabled=False)
    except ProjectError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    _dump(as_json, task.model_dump())


@task_app.command("run")
def task_run_cmd(
    project_id: str = typer.Argument(...),
    task_id: str = typer.Argument(...),
    root: Path = typer.Option(None, "--root", "-r"),
    as_json: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    from kairo.projects import ProjectError, run_task

    try:
        record = run_task(_cli_root(root), project_id, task_id)
    except ProjectError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    _dump(as_json, record.model_dump())
    if record.status != "succeeded":
        raise typer.Exit(1)


@artifact_app.command("show")
def artifact_show(
    project_id: str = typer.Argument(...),
    run_id: str = typer.Argument(...),
    root: Path = typer.Option(None, "--root", "-r"),
    as_json: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    from kairo.projects import ProjectError, get_run, read_artifact

    serve = _cli_root(root)
    try:
        run = get_run(serve, project_id, run_id)
        body = read_artifact(serve, project_id, run_id)
    except ProjectError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    _dump(as_json, {"run": run.model_dump(), "artifact": body})

