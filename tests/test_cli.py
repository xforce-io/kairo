import json

import pytest
from typer.testing import CliRunner

from kairo.cli import app

runner = CliRunner()


def test_cli_help_shows_quickstart():
    """#22 ①:顶层 --help 带「快速上手」happy-path + 两层产出 + 心智 SSOT 指向。"""
    out = runner.invoke(app, ["--help"]).output
    assert "快速上手" in out
    assert "init" in out and "add" in out and "step" in out
    assert "understanding.md" in out and "assessment.md" in out
    assert "constitution.yaml" in out


@pytest.mark.parametrize(
    "cmd",
    [["status"], ["step"], ["add", "x.txt"], ["index"], ["history"], ["diff"]],
)
def test_cli_friendly_error_outside_workspace(tmp_path, monkeypatch, cmd):
    """#22 ②:非工作区给友好错误,非零退出,不吐 traceback。"""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, cmd)
    assert result.exit_code != 0
    assert "不是 kairo 工作区" in result.output
    assert "Traceback" not in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_cli_status_warns_on_corpus_drift(tmp_path, monkeypatch):
    """#13 v2:改 corpus 后 status 给 advisory(不自动重算,提示 re-step)。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KAIRO_STUB", "1")
    runner.invoke(app, ["init"])
    meeting = tmp_path / "m.txt"
    meeting.write_text("会议")
    wp = tmp_path / "wp.md"
    wp.write_text("基线v1")
    runner.invoke(app, ["add", str(meeting)])
    runner.invoke(app, ["add", str(wp), "--corpus"])
    runner.invoke(app, ["step"])
    # 折叠后无漂移:status 不报 corpus 提示
    assert "corpus" not in runner.invoke(app, ["status"]).output
    wp.write_text("基线v2-改了关键内容")  # corpus 变更
    out = runner.invoke(app, ["status"]).output
    assert "corpus" in out and "re-step" in out  # advisory


def test_cli_init_creates_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init", "kidney"])
    assert result.exit_code == 0
    assert (tmp_path / "constitution.yaml").is_file()
    assert (tmp_path / ".kairo" / "state.json").is_file()


def test_cli_end_to_end_domino_audio_and_text(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KAIRO_STUB", "1")  # 强制 stub,端到端不触真 API
    runner.invoke(app, ["init"])
    audio = tmp_path / "rec.m4a"
    audio.write_bytes(b"fake audio")
    text = tmp_path / "wangqiang.txt"
    text.write_text("王强会议:三智能体定位与落地优先级")
    runner.invoke(app, ["add", str(audio)])
    runner.invoke(app, ["add", str(text)])

    result = runner.invoke(app, ["step"])
    assert result.exit_code == 0

    understanding = (tmp_path / "understanding.md").read_text()
    # 音频链:ASR→Digest→Compose
    assert "STUB TRANSCRIPT" in understanding
    # 文本链:Digest→Compose
    assert "三智能体定位与落地优先级" in understanding
    # 两层文档都生成
    assert (tmp_path / "assessment.md").is_file()
    # understanding + assessment 各折入两条 digest
    targets = json.loads(
        (tmp_path / ".kairo" / "history" / "0000" / "state.targets.json").read_text()
    )
    assert len(targets["understanding.md"]["folded"]) == 2
    assert len(targets["assessment.md"]["folded"]) == 2


def test_cli_re_step_discards_manual_edit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KAIRO_STUB", "1")
    runner.invoke(app, ["init"])
    t = tmp_path / "m.txt"
    t.write_text("内容")
    runner.invoke(app, ["add", str(t)])
    runner.invoke(app, ["step"])
    canonical = (tmp_path / "understanding.md").read_text()
    (tmp_path / "understanding.md").write_text("乱改")
    result = runner.invoke(app, ["re-step", "understanding.md"])
    assert result.exit_code == 0
    assert (tmp_path / "understanding.md").read_text() == canonical


def test_cli_history_and_diff(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KAIRO_STUB", "1")
    runner.invoke(app, ["init"])
    t = tmp_path / "m.txt"
    t.write_text("内容")
    runner.invoke(app, ["add", str(t)])
    runner.invoke(app, ["step"])
    h = runner.invoke(app, ["history"])
    assert h.exit_code == 0 and "0000" in h.stdout
    (tmp_path / "understanding.md").write_text("手改")
    d = runner.invoke(app, ["diff"])
    assert d.exit_code == 0 and "understanding.md" in d.stdout


def test_cli_status_shows_drift_counter(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KAIRO_STUB", "1")
    runner.invoke(app, ["init"])
    t = tmp_path / "m.txt"
    t.write_text("内容")
    runner.invoke(app, ["add", str(t)])
    runner.invoke(app, ["step"])
    s = runner.invoke(app, ["status"])
    assert s.exit_code == 0 and "距上次 A" in s.stdout


def test_cli_status_lists_references(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    text = tmp_path / "meeting.txt"
    text.write_text("内容")
    runner.invoke(app, ["add", str(text)])
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "meeting" in result.stdout


def test_cli_index_command_writes_meetings(tmp_path, monkeypatch):
    """#16:kairo index 手动重建 stream 导航索引(无需 step)。"""
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    meeting = tmp_path / "会议实录.txt"
    meeting.write_text("会议")
    runner.invoke(app, ["add", str(meeting)])

    result = runner.invoke(app, ["index"])

    assert result.exit_code == 0
    index = tmp_path / "references" / "MEETINGS.md"
    assert index.is_file()
    assert "会议实录" in index.read_text()


def test_cli_add_dir_without_corpus_friendly_error(tmp_path, monkeypatch):
    """#24:add <dir> 无 --corpus → 友好错误 + 非零退出,不吐 traceback。"""
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    d = tmp_path / "docs"
    d.mkdir()
    (d / "a.md").write_text("a")
    result = runner.invoke(app, ["add", str(d)])
    assert result.exit_code != 0
    assert "corpus" in result.output
    assert "Traceback" not in result.output


def test_cli_add_dir_corpus_ok(tmp_path, monkeypatch):
    """#24:add <dir> --corpus → 建一条 corpus_tree 引用。"""
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    d = tmp_path / "corpus_docs"
    (d / "sub").mkdir(parents=True)
    (d / "sub" / "b.md").write_text("b")
    result = runner.invoke(app, ["add", str(d), "--corpus"])
    assert result.exit_code == 0
    man = (tmp_path / "references").glob("*/manifest.yaml")
    assert any("corpus_tree" in p.read_text() for p in man)


def test_cli_e2e_corpus_dir_not_digested(tmp_path, monkeypatch):
    """#24 e2e:corpus 目录不产 digest;stream 正常折叠出两层文档。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KAIRO_STUB", "1")
    runner.invoke(app, ["init"])
    # corpus 目录
    cdir = tmp_path / "corpus_docs"
    (cdir / "平台").mkdir(parents=True)
    (cdir / "平台" / "术语表.md").write_text("灵犀系统=正式名")
    # stream 文件
    s = tmp_path / "会议.txt"
    s.write_text("王强会议:落地优先级")
    runner.invoke(app, ["add", str(cdir), "--corpus"])
    runner.invoke(app, ["add", str(s)])
    result = runner.invoke(app, ["step"])
    assert result.exit_code == 0
    # 两层文档生成
    assert (tmp_path / "understanding.md").is_file()
    assert (tmp_path / "assessment.md").is_file()
    # corpus 目录引用没有 digest.md(不被 digest)
    refs = tmp_path / "references"
    corpus_ref = next(p for p in refs.iterdir() if "corpus_docs" in p.name)
    assert not (corpus_ref / "digest.md").exists()
