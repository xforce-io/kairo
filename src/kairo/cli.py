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
from kairo.workspace import AddError, Workspace

app = typer.Typer(help="step 驱动的增量知识构建引擎")


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
    ws = Workspace(Path.cwd())
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
    """跑调和循环到收敛(provider 自动选:有 key→Claude,否则 stub;KAIRO_STUB 强制 stub)。"""
    ws = Workspace(Path.cwd())
    progressed = engine_step(ws, select_provider())
    typer.echo("stepped" if progressed else "no change")


@app.command(name="re-step")
def re_step(
    target: str = typer.Argument(None, help="文档 / reference id;省略=全量"),
) -> None:
    """强制重算(文档级=整篇重综合,丢手改)。"""
    ws = Workspace(Path.cwd())
    engine_re_step(ws, select_provider(), target)
    typer.echo(f"re-stepped {target or '(all)'}")


@app.command()
def accept(doc: str = typer.Argument(..., help="要接受手改的文档")) -> None:
    """接受手改、钉为新基线,解除 blocked: manual-edit。"""
    ws = Workspace(Path.cwd())
    engine_accept(ws, doc)
    typer.echo(f"accepted {doc}")


@app.command()
def status() -> None:
    """列 references / 各文档融入状态。"""
    ws = Workspace(Path.cwd())
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
    ws = Workspace(Path.cwd())
    path = write_stream_index(ws)
    typer.echo(f"wrote {path.relative_to(ws.root)}")


@app.command()
def history() -> None:
    """列版本快照。"""
    ws = Workspace(Path.cwd())
    for seq in list_snapshots(ws):
        typer.echo(seq)


@app.command()
def rollback(seq: str = typer.Argument(..., help="要回退到的快照 seq")) -> None:
    """回退文档 + targets 段到某版本(references/ 不动,下次 step 重融更晚 digest)。"""
    ws = Workspace(Path.cwd())
    history_rollback(ws, seq)
    typer.echo(f"rolled back to {seq}")


@app.command()
def diff(seq: str = typer.Argument(None, help="对比的快照;省略=最近")) -> None:
    """工作态 vs 版本文档差异(自带,不依赖 git)。"""
    ws = Workspace(Path.cwd())
    typer.echo(diff_worktree(ws, seq) or "(no changes)")
