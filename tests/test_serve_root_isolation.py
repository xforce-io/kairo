"""#398: 全局 conftest 隔离 KAIRO_SERVE_ROOT,无 --root 的 CLI 不得写进继承根。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFTEST = REPO_ROOT / "tests" / "conftest.py"

# 内嵌用例跑在子进程:autouse 已 delenv,本进程再 setenv poison 测不到守卫。
_PROBE = """\
import json
import os
from pathlib import Path

from typer.testing import CliRunner

from kairo.cli import app


def test_probe(monkeypatch):
    assert "KAIRO_SERVE_ROOT" not in os.environ
    cwd = Path(os.environ["KAIRO_ISOLATION_PROBE_CWD"])
    poison = Path(os.environ["KAIRO_ISOLATION_PROBE_POISON"])
    monkeypatch.chdir(cwd)
    result = CliRunner().invoke(app, ["project", "create", "隔离探针"])
    assert result.exit_code == 0, result.output
    pid = json.loads(result.output)["id"]
    assert (cwd / ".kairo" / "projects" / pid / "project.json").is_file()
    poison_projects = poison / ".kairo" / "projects"
    assert not poison_projects.exists() or list(poison_projects.iterdir()) == []
"""


def _prj_ids(root: Path) -> set[str]:
    projects = root / ".kairo" / "projects"
    if not projects.is_dir():
        return set()
    return {p.name for p in projects.iterdir() if p.is_dir() and p.name.startswith("prj-")}


def test_autouse_unsets_kairo_serve_root():
    """autouse 后本进程看不到 KAIRO_SERVE_ROOT。"""
    assert "KAIRO_SERVE_ROOT" not in os.environ


def test_project_create_writes_cwd_not_inherited_serve_root(tmp_path):
    """子进程带着 poison 的 KAIRO_SERVE_ROOT 跑内嵌用例:create 只写临时 cwd。"""
    poison = tmp_path / "poison"
    cwd = tmp_path / "cwd"
    nested = tmp_path / "nested"
    poison.mkdir()
    cwd.mkdir()
    nested.mkdir()
    (nested / "conftest.py").write_text(CONFTEST.read_text(encoding="utf-8"), encoding="utf-8")
    (nested / "test_probe.py").write_text(_PROBE, encoding="utf-8")

    before = _prj_ids(poison)
    env = os.environ.copy()
    env["KAIRO_SERVE_ROOT"] = str(poison)
    env["KAIRO_ISOLATION_PROBE_CWD"] = str(cwd)
    env["KAIRO_ISOLATION_PROBE_POISON"] = str(poison)
    src = str(REPO_ROOT / "src")
    env["PYTHONPATH"] = src if not env.get("PYTHONPATH") else src + os.pathsep + env["PYTHONPATH"]

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(nested / "test_probe.py"), "-q", "--tb=short"],
        cwd=nested,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _prj_ids(poison) == before
    assert _prj_ids(cwd)
