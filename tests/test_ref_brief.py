"""#362 Ref brief:产出契约、digest 旁路、Timeline 列表呈现与 CLI 补齐。"""

import hashlib

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from kairo.brief import (
    BriefError,
    brief_after_digest,
    brief_stale,
    check_brief,
    generate_brief,
)
from kairo.cli import app
from kairo.web.server import create_app
from kairo.workspace import Workspace

runner = CliRunner()


def _ref_with_digest(ws, tmp_path, ref_id, *, digest="# 纪要\n\n扫门分流已跑通。\n", occurred="2026-09-11"):
    source = tmp_path / f"{ref_id}.txt"
    source.write_text("会议正文", encoding="utf-8")
    ws.add([source], ref_id=ref_id, title=ref_id, occurred_at=occurred)
    path = ws.references_dir() / ref_id / "digest.md"
    path.write_text(digest, encoding="utf-8")
    return path


def _serve_root(tmp_path):
    root = tmp_path / "root"
    wsdir = root / "alpha"
    wsdir.mkdir(parents=True)
    return root, Workspace.init(wsdir, topic="能源梳理")


# ---- unit:契约校验与过期判定 ----


def test_check_brief_rejects_empty_multiline_and_overlong():
    with pytest.raises(BriefError):
        check_brief("   ")
    with pytest.raises(BriefError):
        check_brief("第一句。\n第二句。")
    with pytest.raises(BriefError):
        check_brief("很长" * 26)
    assert check_brief("  扫门分流已跑通，设备类型解析旧 bug 未定改期  ") == "扫门分流已跑通，设备类型解析旧 bug 未定改期"


def test_brief_stale_tracks_digest_hash(tmp_path):
    _, ws = _serve_root(tmp_path)
    _ref_with_digest(ws, tmp_path, "r1")
    man = ws.read_manifest("r1")
    assert brief_stale(man, "digest 正文") is True
    man.brief = "一句话"
    man.brief_hash = hashlib.sha256("digest 正文".encode()).hexdigest()[:12]
    assert brief_stale(man, "digest 正文") is False
    assert brief_stale(man, "digest 正文改了") is True


# ---- S2:digest 旁路 ----


def test_generate_brief_writes_manifest(tmp_path):
    _, ws = _serve_root(tmp_path)
    digest = _ref_with_digest(ws, tmp_path, "r1")
    got = generate_brief(ws, "r1", generator=lambda body: " 扫门分流已跑通 \n")
    assert got == "扫门分流已跑通"
    man = ws.read_manifest("r1")
    assert man.brief == "扫门分流已跑通"
    assert man.brief_hash == hashlib.sha256(digest.read_text().encode()).hexdigest()[:12]
    assert brief_stale(man, digest.read_text()) is False


def test_overlong_brief_gets_one_corrective_retry(tmp_path):
    _, ws = _serve_root(tmp_path)
    _ref_with_digest(ws, tmp_path, "r1")
    calls = []

    def twice(body):
        calls.append(body)
        return "很长" * 40 if len(calls) == 1 else "扫门分流已跑通"

    assert generate_brief(ws, "r1", generator=twice) == "扫门分流已跑通"
    assert len(calls) == 2
    assert "超过上限" in calls[1]  # 纠正指令带实测字数
    assert ws.read_manifest("r1").brief == "扫门分流已跑通"


def test_still_overlong_after_retry_fails_without_writing(tmp_path):
    _, ws = _serve_root(tmp_path)
    _ref_with_digest(ws, tmp_path, "r1")
    calls = []

    def always_long(body):
        calls.append(body)
        return "很长" * 40

    with pytest.raises(BriefError):
        generate_brief(ws, "r1", generator=always_long)
    assert len(calls) == 2  # 只纠正一次,不无限重试
    assert ws.read_manifest("r1").brief is None


def test_multiline_output_is_not_retried(tmp_path):
    _, ws = _serve_root(tmp_path)
    _ref_with_digest(ws, tmp_path, "r1")
    calls = []

    def multi(body):
        calls.append(body)
        return "第一句。\n第二句。"

    with pytest.raises(BriefError):
        generate_brief(ws, "r1", generator=multi)
    assert len(calls) == 1


def test_brief_failure_leaves_manifest_and_digest_intact(tmp_path, capsys):
    _, ws = _serve_root(tmp_path)
    digest = _ref_with_digest(ws, tmp_path, "r1")
    before = digest.read_text()

    def broken(body):
        raise RuntimeError("provider down")

    with pytest.raises(BriefError):
        generate_brief(ws, "r1", generator=broken)
    assert ws.read_manifest("r1").brief is None
    assert digest.read_text() == before

    # 契约不合(多行)同样不写盘。
    with pytest.raises(BriefError):
        generate_brief(ws, "r1", generator=lambda body: "第一句。\n第二句。")
    assert ws.read_manifest("r1").brief is None

    # 旁路吞掉异常,只留 stderr 诊断,digest 不受影响。
    brief_after_digest(ws, "r1", provider=None)
    assert "brief skipped" in capsys.readouterr().err
    assert ws.read_manifest("r1").brief is None
    assert digest.read_text() == before


def test_manifest_without_brief_key_is_supported(tmp_path):
    _, ws = _serve_root(tmp_path)
    _ref_with_digest(ws, tmp_path, "r1")
    raw = (ws.references_dir() / "r1" / "manifest.yaml").read_text()
    assert "brief:" not in raw  # exclude_none:空值不落键
    generate_brief(ws, "r1", generator=lambda body: "一句话结论")
    assert "brief:" in (ws.references_dir() / "r1" / "manifest.yaml").read_text()


# ---- S1:Timeline 列表呈现 ----


def test_list_shows_brief_and_calendar_does_not(tmp_path):
    root, ws = _serve_root(tmp_path)
    _ref_with_digest(ws, tmp_path, "2026-09-11-with-brief")
    _ref_with_digest(ws, tmp_path, "2026-09-11-no-brief")
    generate_brief(ws, "2026-09-11-with-brief", generator=lambda body: "扫门分流已跑通")

    client = TestClient(create_app(root))
    listing = client.get("/timeline?mode=recent")
    assert listing.status_code == 200
    assert '<span class="tl-brief">扫门分流已跑通</span>' in listing.text
    assert listing.text.count('class="tl-brief"') == 1  # 无 brief 的行不渲染,也无占位
    assert listing.text.count("has-brief") == 1

    calendar = client.get("/timeline?day=2026-09-11")
    assert calendar.status_code == 200
    assert "2026-09-11-with-brief" in calendar.text
    assert "tl-brief" not in calendar.text


# ---- S3:CLI 补齐 ----


def test_cli_brief_backfills_skips_and_keeps_digest(tmp_path, monkeypatch):
    root, ws = _serve_root(tmp_path)
    d1 = _ref_with_digest(ws, tmp_path, "r1")
    d2 = _ref_with_digest(ws, tmp_path, "r2", digest="# 纪要二\n\n数仓 OOM 已缓解。\n")
    _no_digest = tmp_path / "plain.txt"
    _no_digest.write_text("无纪要", encoding="utf-8")
    ws.add([_no_digest], ref_id="r3", title="r3")
    hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (d1, d2)}

    monkeypatch.setattr(
        "kairo.brief.provider_generator", lambda provider: (lambda body: "一句话结论")
    )
    monkeypatch.setattr("kairo.cli.select_provider", lambda: object())

    first = runner.invoke(app, ["brief", "--root", str(root)])
    assert first.exit_code == 0, first.output
    assert "ok=2" in first.output and "no-digest=1" in first.output
    assert ws.read_manifest("r1").brief == "一句话结论"
    assert ws.read_manifest("r2").brief == "一句话结论"
    assert ws.read_manifest("r3").brief is None
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == h for p, h in hashes.items())

    second = runner.invoke(app, ["brief", "--root", str(root)])
    assert second.exit_code == 0
    assert "ok=0" in second.output and "skipped=2" in second.output

    forced = runner.invoke(app, ["brief", "r1", "--root", str(root), "--force"])
    assert forced.exit_code == 0
    assert "ok=1" in forced.output

    limited = runner.invoke(app, ["brief", "--root", str(root), "--force", "--limit", "1"])
    assert limited.exit_code == 0
    assert "ok=1" in limited.output


def test_cli_brief_reports_failure_with_exit_1(tmp_path, monkeypatch):
    root, ws = _serve_root(tmp_path)
    _ref_with_digest(ws, tmp_path, "r1")
    monkeypatch.setattr(
        "kairo.brief.provider_generator",
        lambda provider: (lambda body: "第一句。\n第二句。"),
    )
    monkeypatch.setattr("kairo.cli.select_provider", lambda: object())

    out = runner.invoke(app, ["brief", "--root", str(root)])
    assert out.exit_code == 1
    assert ws.read_manifest("r1").brief is None


def test_cli_brief_unknown_ref_exits_1(tmp_path, monkeypatch):
    root, _ = _serve_root(tmp_path)
    monkeypatch.setattr("kairo.cli.select_provider", lambda: object())
    out = runner.invoke(app, ["brief", "nope", "--root", str(root)])
    assert out.exit_code == 1
