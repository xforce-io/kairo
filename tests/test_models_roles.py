from kairo.models import Constitution


def test_image_exts_map_to_attachment():
    rbe = Constitution().roles_by_ext
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".heic"):
        assert rbe[ext] == "attachment", ext


def test_audio_and_document_roles_unchanged():
    rbe = Constitution().roles_by_ext
    assert rbe[".m4a"] == "audio"
    assert rbe[".pdf"] == "document"


def test_guess_role_falls_back_to_builtin_for_old_workspace(tmp_path):
    # 旧 workspace 的 constitution 冻结了无图片扩展名的 roles_by_ext;
    # guess_role 应回退内置默认 → .jpg 仍归 attachment(不落到 transcript)
    import yaml
    from kairo.workspace import Workspace
    ws = Workspace.init(tmp_path / "ws", topic="t")
    cpath = ws.root / "constitution.yaml"
    data = yaml.safe_load(cpath.read_text())
    data["roles_by_ext"] = {".m4a": "audio", ".pdf": "document"}  # 模拟旧配置(无图片)
    cpath.write_text(yaml.safe_dump(data, allow_unicode=True))
    from pathlib import Path
    assert ws.guess_role(Path("x.jpg")) == "attachment"
    assert ws.guess_role(Path("x.png")) == "attachment"
    assert ws.guess_role(Path("x.m4a")) == "audio"          # 持久化配置仍优先
    assert ws.guess_role(Path("x.unknown")) == "transcript"  # 都没有 → default_role
