import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from kairo.models import Target, TargetState
from kairo.web.discovery import activity_label, last_activity, scan_workspaces, summarize
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


def test_summary_ignores_legacy_judgment_block(tmp_path):
    ws = _mk(tmp_path, "ws", "t")
    con = ws.constitution
    con.targets.append(Target(path="assessment.md", layer="judgment"))
    (ws.root / "constitution.yaml").write_text(
        yaml.safe_dump(con.model_dump(), allow_unicode=True, sort_keys=False)
    )
    ws = Workspace.open(ws.root)
    state = ws.read_state()
    state.targets["assessment.md"] = TargetState(
        status="blocked", reason="provider-failed"
    )
    ws.write_state(state)
    assert summarize(ws).blocked_count == 0


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


def _utime(path: Path, ts: float) -> None:
    os.utime(path, (ts, ts))


def test_last_activity_is_max_of_owned_paths(tmp_path):
    ws = _mk(tmp_path, "ws", "t")
    early = time.time() - 3600
    late = time.time() - 60
    _utime(ws.root / "constitution.yaml", early)
    _utime(ws.root / ".kairo" / "state.json", early)
    (ws.root / "understanding.md").write_text("facts")
    _utime(ws.root / "understanding.md", late)
    got = last_activity(ws)
    assert abs(got.timestamp() - late) < 2


def test_last_activity_ignores_digest_and_ds_store(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_STUB", "1")
    ws = _mk(tmp_path, "ws", "t")
    src = tmp_path / "m.txt"
    src.write_text("会议")
    rid = ws.add([src])
    owned = [
        ws.root / "constitution.yaml",
        ws.root / ".kairo" / "state.json",
        ws.root / "references" / rid / "manifest.yaml",
    ]
    early = time.time() - 7200
    for p in owned:
        _utime(p, early)
    digest = ws.root / "references" / rid / "digest.md"
    digest.write_text("noise")
    _utime(digest, time.time())
    (ws.root / ".DS_Store").write_bytes(b"x")
    _utime(ws.root / ".DS_Store", time.time())
    got = last_activity(ws)
    assert abs(got.timestamp() - early) < 2


def test_scan_keeps_slug_order_and_exposes_last_activity(tmp_path):
    older = _mk(tmp_path, "z-ws", "zulu")
    newer = _mk(tmp_path, "a-ws", "alpha")
    _utime(older.root / "constitution.yaml", time.time() - 8000)
    _utime(newer.root / "constitution.yaml", time.time() - 10)
    out = scan_workspaces(tmp_path)
    assert [s.slug for s in out] == ["a-ws", "z-ws"]
    assert out[0].last_activity.timestamp() > out[1].last_activity.timestamp()


def test_activity_label_today_yesterday_and_iso():
    now = datetime(2026, 8, 26, 15, 30, tzinfo=timezone(timedelta(hours=8)))
    today = now.replace(hour=9, minute=10)
    yest = now - timedelta(days=1)
    older = now - timedelta(days=5)
    assert activity_label(today, now=now, today="today {t}", yesterday="yesterday") == "today 09:10"
    assert activity_label(yest, now=now, today="today {t}", yesterday="yesterday") == "yesterday"
    assert activity_label(older, now=now, today="today {t}", yesterday="yesterday") == "2026-08-21"


def test_summarize_includes_last_activity(tmp_path):
    ws = _mk(tmp_path, "ws", "t")
    s = summarize(ws)
    assert s.last_activity.tzinfo is not None
    assert abs(s.last_activity.timestamp() - last_activity(ws).timestamp()) < 1
