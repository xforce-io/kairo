"""#427: same-serve-root kairo run is single-flight."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from typer.testing import CliRunner

from kairo.cli import app
from kairo.refs import serve_root_of
from kairo.run_lock import RunOccupiedError, acquire_run_lock, format_occupancy_error
from kairo.workspace import Workspace

runner = CliRunner()


def _hold_script(serve: Path, ready: Path, release: Path, target: str) -> str:
    return (
        "import time\n"
        "from pathlib import Path\n"
        "from kairo.run_lock import acquire_run_lock\n"
        f"serve = Path({str(serve)!r})\n"
        f"ready = Path({str(ready)!r})\n"
        f"release = Path({str(release)!r})\n"
        f"with acquire_run_lock(serve, target={target!r}):\n"
        "    ready.write_text(str(__import__('os').getpid()), encoding='utf-8')\n"
        "    while not release.is_file():\n"
        "        time.sleep(0.05)\n"
    )


def _src_pythonpath() -> str:
    src = Path(__file__).resolve().parents[1] / "src"
    return str(src) + os.pathsep + os.environ.get("PYTHONPATH", "")


def _spawn_holder(serve: Path, tmp_path: Path, target: str = "--ref holder"):
    ready = tmp_path / "holder.ready"
    release = tmp_path / "holder.release"
    env = os.environ.copy()
    env["PYTHONPATH"] = _src_pythonpath()
    proc = subprocess.Popen(
        [sys.executable, "-c", _hold_script(serve, ready, release, target)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    deadline = time.time() + 5
    while time.time() < deadline:
        if ready.is_file() and ready.read_text(encoding="utf-8").strip():
            break
        if proc.poll() is not None:
            out, err = proc.communicate()
            raise AssertionError(f"holder exited {proc.returncode}: {out} {err}")
        time.sleep(0.05)
    else:
        proc.kill()
        raise AssertionError("holder did not publish occupancy")
    return proc, ready, release


def _cli_env(serve: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["KAIRO_STUB"] = "1"
    env["KAIRO_SERVE_ROOT"] = str(serve)
    env.pop("KAIRO_ASR_CMD", None)
    env.pop("KAIRO_ASR_ORIGIN", None)
    env["PYTHONPATH"] = _src_pythonpath()
    return env


def _invoke_run(args: list[str], cwd: Path, serve: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "kairo", *args],
        cwd=str(cwd),
        env=_cli_env(serve),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def _topic_with_ref(tmp_path: Path):
    serve = tmp_path / "serve"
    serve.mkdir()
    ws = Workspace.init(serve / "alpha", topic="alpha")
    src = tmp_path / "note.txt"
    src.write_text("single-flight material", encoding="utf-8")
    ref_id = ws.add([src])
    return serve, ws, ref_id


def test_second_process_refuses_with_occupancy(tmp_path):
    serve = tmp_path / "root"
    serve.mkdir()
    holder, ready, release = _spawn_holder(serve, tmp_path, target="--ref first")
    try:
        assert holder.poll() is None
        occupant_pid = int(ready.read_text(encoding="utf-8"))
        try:
            acquire_run_lock(serve, target="--ref second")
            raise AssertionError("second acquire must fail")
        except RunOccupiedError as exc:
            text = str(exc)
            assert "already occupying" in text
            assert f"pid: {occupant_pid}" in text
            assert "started:" in text
            assert "target: --ref first" in text
            assert exc.occupancy["pid"] == occupant_pid
        assert holder.poll() is None
        try:
            os.kill(occupant_pid, 0)
        except OSError as exc:
            raise AssertionError("first holder must stay alive") from exc
    finally:
        release.write_text("go", encoding="utf-8")
        assert holder.wait(timeout=5) == 0
    with acquire_run_lock(serve, target="--ref after") as lock:
        assert lock.occupancy["target"] == "--ref after"
    assert holder.returncode == 0


def test_second_cli_run_refuses_and_does_not_kill_first(tmp_path, monkeypatch):
    serve, ws, ref_id = _topic_with_ref(tmp_path)
    holder, ready, release = _spawn_holder(serve_root_of(ws), tmp_path, target=f"--ref {ref_id}")
    try:
        result = _invoke_run(["run", "--ref", ref_id], ws.root, serve)
        assert result.returncode not in (0, 2), result.stdout + result.stderr
        err = result.stderr
        assert "already occupying" in err
        assert "pid:" in err
        assert "started:" in err
        assert f"target: --ref {ref_id}" in err
        assert holder.poll() is None
        occupant_pid = int(ready.read_text(encoding="utf-8"))
        try:
            os.kill(occupant_pid, 0)
        except OSError as exc:
            raise AssertionError("first instance must not be SIGKILL'd") from exc
        digest = ws.root / "references" / ref_id / "digest.md"
        assert not digest.exists()
    finally:
        release.write_text("go", encoding="utf-8")
        assert holder.wait(timeout=5) == 0
    after = _invoke_run(["run", "--ref", ref_id], ws.root, serve)
    assert after.returncode == 0, after.stdout + after.stderr
    assert (ws.root / "references" / ref_id / "digest.md").is_file()


def test_run_all_shares_serve_root_lock(tmp_path):
    serve, ws, _ref_id = _topic_with_ref(tmp_path)
    holder, _ready, release = _spawn_holder(serve, tmp_path, target="--ref first")
    try:
        result = _invoke_run(["run", "--all"], serve, serve)
        assert result.returncode != 0, result.stdout + result.stderr
        assert "already occupying" in result.stderr
        assert "target: --ref first" in result.stderr
        assert holder.poll() is None
    finally:
        release.write_text("go", encoding="utf-8")
        assert holder.wait(timeout=5) == 0


def test_in_process_cli_refuses_when_lock_held(tmp_path, monkeypatch):
    serve, ws, ref_id = _topic_with_ref(tmp_path)
    monkeypatch.setenv("KAIRO_STUB", "1")
    monkeypatch.chdir(ws.root)
    with acquire_run_lock(serve, target="--ref other"):
        # Same-process flock does not block itself; this checks the printed contract
        # via a child CLI, and the occupancy formatter for the in-process holder.
        occupancy = {"pid": 4242, "started_at": "2026-09-23T00:00:00Z", "target": "--ref other"}
        text = format_occupancy_error(occupancy, serve)
        assert "pid: 4242" in text
        assert "started: 2026-09-23T00:00:00Z" in text
        assert "target: --ref other" in text
        child = _invoke_run(["run", "--ref", ref_id], ws.root, serve)
        assert child.returncode == 1
        assert "pid:" in child.stderr
        assert "occupying" in child.stderr
    sequential = runner.invoke(app, ["run", "--ref", ref_id])
    assert sequential.exit_code == 0, sequential.output


def test_bare_run_still_exits_2_without_taking_lock(tmp_path, monkeypatch):
    serve, ws, _ref_id = _topic_with_ref(tmp_path)
    monkeypatch.chdir(ws.root)
    holder, _ready, release = _spawn_holder(serve, tmp_path)
    try:
        bare = runner.invoke(app, ["run"])
        assert bare.exit_code == 2
        assert "需要 --ref" in bare.output
        assert holder.poll() is None
    finally:
        release.write_text("go", encoding="utf-8")
        assert holder.wait(timeout=5) == 0
