"""kairo CLI(typer 薄壳)。M0:init / add / step / status。"""

from __future__ import annotations

from pathlib import Path

import typer

from kairo.engine import step as engine_step
from kairo.provider import select_provider
from kairo.workspace import Workspace

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
) -> None:
    """登记一条 reference 的所有形态(指针)。"""
    ws = Workspace(Path.cwd())
    rid = ws.add(files, ref_id=ref_id, role=role)
    typer.echo(f"added {rid}")


@app.command()
def step() -> None:
    """跑调和循环到收敛(provider 自动选:有 key→Claude,否则 stub;KAIRO_STUB 强制 stub)。"""
    ws = Workspace(Path.cwd())
    progressed = engine_step(ws, select_provider())
    typer.echo("stepped" if progressed else "no change")


@app.command()
def status() -> None:
    """列 references / 各文档融入状态。"""
    ws = Workspace(Path.cwd())
    state = ws.read_state()
    for ref_id in ws.list_reference_ids():
        roles = ",".join(f.role for f in ws.read_manifest(ref_id).forms)
        typer.echo(f"reference {ref_id}: [{roles}]")
    for target in ws.constitution.targets:
        ts = state.targets.get(target.path)
        typer.echo(f"target {target.path}: folded {len(ts.folded) if ts else 0}")
