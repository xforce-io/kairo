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
    [["status"], ["step"], ["add", "x.txt"], ["index"], ["history"], ["diff"], ["prose", "x"]],
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


def test_cli_prose_generates_readable_archive(tmp_path, monkeypatch):
    """#60:kairo prose <id> 在 normalize 默认关时仍可按需产 prose,不改 constitution。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KAIRO_STUB", "1")
    runner.invoke(app, ["init"])
    audio = tmp_path / "rec.m4a"
    audio.write_bytes(b"fake audio")
    runner.invoke(app, ["add", str(audio)])
    runner.invoke(app, ["step"])  # ASR + digest;默认无 prose
    rid = next(p.name for p in (tmp_path / "references").iterdir() if p.is_dir())
    assert not (tmp_path / "references" / rid / "prose.md").exists()

    result = runner.invoke(app, ["prose", rid])
    assert result.exit_code == 0
    assert f"references/{rid}/prose.md" in result.output
    assert (tmp_path / "references" / rid / "prose.md").is_file()
    assert "STUB TRANSCRIPT" in (tmp_path / "references" / rid / "prose.md").read_text()
    # constitution 仍关
    import yaml

    con = yaml.safe_load((tmp_path / "constitution.yaml").read_text())
    assert not (con.get("pipeline") or {}).get("normalize", {}).get("enabled", False)
    # 再跑失败
    again = runner.invoke(app, ["prose", rid])
    assert again.exit_code != 0


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


def test_cli_add_dir_stream_multiform(tmp_path, monkeypatch):
    """#67:add <dir> 无 --corpus → 一条 stream 多形态 ref。"""
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    d = tmp_path / "docs"
    d.mkdir()
    (d / "a.md").write_text("a")
    (d / "b.m4a").write_bytes(b"x")
    result = runner.invoke(app, ["add", str(d)])
    assert result.exit_code == 0
    assert "added" in result.output
    mans = list((tmp_path / "references").glob("*/manifest.yaml"))
    assert len(mans) == 1
    text = mans[0].read_text()
    assert "class: stream" in text
    assert text.count("role:") >= 2


def test_cli_add_copy_materializes(tmp_path, monkeypatch):
    """#64:kairo add --copy 物化到 .kairo/uploads。"""
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    src = tmp_path / "out.txt"
    src.write_text("外部文件")
    result = runner.invoke(app, ["add", str(src), "--copy"])
    assert result.exit_code == 0
    uploads = tmp_path / ".kairo" / "uploads"
    assert uploads.is_dir()
    assert any(uploads.iterdir())


def test_cli_add_copy_dir_stream_ok(tmp_path, monkeypatch):
    """#67:add <dir> --copy → stream 多形态并物化。"""
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    d = tmp_path / "lib"
    d.mkdir()
    (d / "a.md").write_text("a")
    result = runner.invoke(app, ["add", str(d), "--copy"])
    assert result.exit_code == 0


def test_cli_add_copy_corpus_dir_friendly_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    d = tmp_path / "lib"
    d.mkdir()
    (d / "a.md").write_text("a")
    result = runner.invoke(app, ["add", str(d), "--corpus", "--copy"])
    assert result.exit_code != 0
    assert "基线" in result.output or "copy" in result.output.lower() or "目录" in result.output


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


def test_cli_serve_missing_web_dep_friendly(monkeypatch):
    """缺 kairo[web] 依赖时 serve 给友好提示、非零退出,不吐 traceback。"""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name.startswith("kairo.web"):
            raise ImportError("no web")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = runner.invoke(app, ["serve", "--port", "0"])
    assert result.exit_code != 0
    assert "kairo[web]" in result.output
    assert "Traceback" not in result.output


def test_cli_list_scans_serve_root(tmp_path, monkeypatch):
    """#95:kairo list 扫 root 下一层 workspace,与 discovery 同源。"""
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["new", "alpha"])
    runner.invoke(app, ["new", "beta"])
    (tmp_path / "noise").mkdir()
    result = runner.invoke(app, ["list", str(tmp_path)])
    assert result.exit_code == 0
    assert "alpha" in result.output and "beta" in result.output
    assert "noise" not in result.output or "SLUG" in result.output
    j = runner.invoke(app, ["list", str(tmp_path), "--json"])
    assert j.exit_code == 0
    data = json.loads(j.output)
    assert {x["slug"] for x in data} == {"alpha", "beta"}


def test_cli_list_uses_kairo_serve_root_env(tmp_path, monkeypatch):
    """#95:无参数时 list 读 KAIRO_SERVE_ROOT。"""
    monkeypatch.setenv("KAIRO_SERVE_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path / "elsewhere" if False else tmp_path)
    elsewhere = tmp_path / "cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    runner.invoke(app, ["new", "env-ws", "--root", str(tmp_path)])
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "env-ws" in result.output


def test_cli_new_and_rm_ws(tmp_path, monkeypatch):
    """#95:new 建目录+init;rm-ws --yes 删除且保留 root glossary。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "glossary.yaml").write_text("entries: []\n")
    created = runner.invoke(app, ["new", "能源业务", "--root", str(tmp_path)])
    assert created.exit_code == 0
    assert (tmp_path / "能源业务" / "constitution.yaml").is_file()
    bad = runner.invoke(app, ["new", "能源业务", "--root", str(tmp_path)])
    assert bad.exit_code != 0
    deleted = runner.invoke(app, ["rm-ws", "能源业务", "--root", str(tmp_path), "--yes"])
    assert deleted.exit_code == 0
    assert not (tmp_path / "能源业务").exists()
    assert (tmp_path / "glossary.yaml").is_file()


def test_cli_rm_ws_rejects_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["rm-ws", "nope", "--root", str(tmp_path), "--yes"])
    assert result.exit_code != 0
    assert "不存在" in result.output


def test_cli_add_to_attaches_form(tmp_path, monkeypatch):
    """#95:add --to <id> 向既有参考追加形态(Web attach)。"""
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    a = tmp_path / "a.txt"
    a.write_text("主材料")
    b = tmp_path / "b.png"
    b.write_bytes(b"\x89PNG")
    runner.invoke(app, ["add", str(a)])
    rid = next(p.name for p in (tmp_path / "references").iterdir() if p.is_dir())
    result = runner.invoke(app, ["add", str(b), "--to", rid, "--copy"])
    assert result.exit_code == 0
    man = (tmp_path / "references" / rid / "manifest.yaml").read_text()
    assert man.count("role:") >= 2


def test_cli_title_renames_display_name(tmp_path, monkeypatch):
    """#95:title 只改展示名,不动 id。"""
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    t = tmp_path / "meeting.txt"
    t.write_text("x")
    runner.invoke(app, ["add", str(t)])
    rid = next(p.name for p in (tmp_path / "references").iterdir() if p.is_dir())
    result = runner.invoke(app, ["title", rid, "王强会"])
    assert result.exit_code == 0
    assert "王强会" in (tmp_path / "references" / rid / "manifest.yaml").read_text()
    st = runner.invoke(app, ["status"])
    assert st.exit_code == 0
    assert "plan=" in st.output and "王强会" in st.output


def test_cli_glossary_workspace_and_shared(tmp_path, monkeypatch):
    """#95:glossary add/list/rm 覆盖 workspace 与 shared。"""
    root = tmp_path / "serve"
    root.mkdir()
    monkeypatch.chdir(root)
    runner.invoke(app, ["new", "ws", "--root", str(root)])
    monkeypatch.chdir(root / "ws")

    add_ws = runner.invoke(app, ["glossary", "add", "天溯", "--note", "本区"])
    assert add_ws.exit_code == 0
    add_sh = runner.invoke(
        app, ["glossary", "add", "公共锚", "--scope", "shared", "--note", "root"]
    )
    assert add_sh.exit_code == 0
    assert (root / "glossary.yaml").is_file()

    listed = runner.invoke(app, ["glossary", "list"])
    assert listed.exit_code == 0
    assert "天溯" in listed.output and "公共锚" in listed.output
    assert "[workspace]" in listed.output and "[shared]" in listed.output

    rm_ws = runner.invoke(app, ["glossary", "rm", "0", "--scope", "workspace"])
    assert rm_ws.exit_code == 0
    rm_sh = runner.invoke(app, ["glossary", "rm", "0", "--scope", "shared"])
    assert rm_sh.exit_code == 0
    again = runner.invoke(app, ["glossary", "list"])
    assert "天溯" not in again.output
    assert "公共锚" not in again.output or "[shared] (0)" in again.output
