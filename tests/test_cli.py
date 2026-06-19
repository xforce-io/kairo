from typer.testing import CliRunner

from kairo.cli import app

runner = CliRunner()


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
    # understanding + assessment 各折入两条 digest → 4 处 folded
    state_targets = (tmp_path / ".kairo" / "history" / "0000" / "state.targets.json").read_text()
    assert state_targets.count("digest.md") == 4


def test_cli_status_lists_references(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    text = tmp_path / "meeting.txt"
    text.write_text("内容")
    runner.invoke(app, ["add", str(text)])
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "meeting" in result.stdout
