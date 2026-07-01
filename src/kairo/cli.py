"""kairo CLI(typer 薄壳)。M0:init / add / step / status。"""

from __future__ import annotations

from pathlib import Path

import typer

from kairo.engine import accept as engine_accept
from kairo.engine import re_step as engine_re_step
from kairo.engine import step as engine_step
from kairo.history import diff_worktree, list_snapshots
from kairo.history import rollback as history_rollback
from kairo.provider import select_provider
from kairo.rules import ComposeRule
from kairo.stream_index import write_stream_index
from kairo.workspace import AddError, Workspace, WorkspaceNotFound

_EPILOG = (
    '快速上手:kairo init "<topic>" → kairo add <file>'
    "(--corpus 标基线,默认 stream 观测)→ kairo step(调和到收敛)。\n\n"
    "产出两层:understanding.md(事实) / assessment.md(判断)。\n\n"
    "心智与协议(两层产出、stream/corpus、fold)定义在 constitution.yaml。"
)

app = typer.Typer(help="step 驱动的增量知识构建引擎", epilog=_EPILOG)


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


@app.command()
def init(topic: str = typer.Argument("main", help="本 workspace 的 topic")) -> None:
    """把当前目录初始化为 topic-workspace + 默认宪法。"""
    Workspace.init(Path.cwd(), topic=topic)
    typer.echo(f"initialized workspace (topic={topic})")


@app.command()
def add(
    files: list[Path],
    ref_id: str = typer.Option(None, "--id", help="覆盖派生 id"),
    role: str = typer.Option(None, "--role", help="覆盖按扩展名猜测的 role"),
    corpus: bool = typer.Option(
        False, "--corpus", help="标为基线参考资料(corpus);默认会议流(stream)"
    ),
) -> None:
    """登记一条 reference 的所有形态(指针)。目录 + --corpus 登记为目录指针。"""
    ws = _open_ws()
    try:
        rid = ws.add(
            files, ref_id=ref_id, role=role, source_class="corpus" if corpus else None
        )
    except AddError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    typer.echo(f"added {rid}")


@app.command()
def step() -> None:
    """跑调和循环到收敛(provider 自动选:endpoint 配置→Claude CLI→stub;KAIRO_STUB 强制 stub)。"""
    ws = _open_ws()
    progressed = engine_step(ws, select_provider())
    typer.echo("stepped" if progressed else "no change")


@app.command(name="re-step")
def re_step(
    target: str = typer.Argument(None, help="文档 / reference id;省略=全量"),
) -> None:
    """强制重算(文档级=整篇重综合,丢手改)。"""
    ws = _open_ws()
    engine_re_step(ws, select_provider(), target)
    typer.echo(f"re-stepped {target or '(all)'}")


@app.command()
def accept(doc: str = typer.Argument(..., help="要接受手改的文档")) -> None:
    """接受手改、钉为新基线,解除 blocked: manual-edit。"""
    ws = _open_ws()
    engine_accept(ws, doc)
    typer.echo(f"accepted {doc}")


@app.command()
def status() -> None:
    """列 references / 各文档融入状态。"""
    ws = _open_ws()
    state = ws.read_state()
    compose = ComposeRule(ws, None)  # 仅用于 corpus 漂移检测(不调 provider)
    for ref_id in ws.list_reference_ids():
        roles = ",".join(f.role for f in ws.read_manifest(ref_id).forms)
        blocked = [
            f"{k.rsplit('/', 1)[-1]}:{v.reason}"
            for k, v in state.products.items()
            if k.startswith(f"references/{ref_id}/") and v.status == "blocked"
        ]
        flag = f"  ⚠ {','.join(blocked)}" if blocked else ""
        typer.echo(f"reference {ref_id}: [{roles}]{flag}")
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
