# tests/test_rules_digest_attach.py
from kairo.workspace import Workspace
from kairo.models import Manifest, Form
from kairo.provider import StubProvider
from kairo.rules import DigestRule


def _wi_hash(ws, man):
    # discover 出该 ref 的 digest WorkItem,取其 input_hash
    items = DigestRule(ws, StubProvider()).discover()
    return next(i for i in items if i.key == f"references/{man.id}/digest.md").input_hash


def test_attachment_changes_digest_fingerprint(tmp_path):
    ws = Workspace.init(tmp_path / "ws", topic="t")
    rdir = ws.references_dir() / "m"; rdir.mkdir(parents=True)
    (rdir / "transcript.md").write_text("转写正文")
    ws.write_manifest("m", Manifest(id="m", title="m", forms=[
        Form(role="transcript", location="references/m/transcript.md", hash="t1"),
    ]))
    h1 = _wi_hash(ws, ws.read_manifest("m"))
    # 加一张图片 form → 指纹必须变
    man = ws.read_manifest("m")
    man.forms.append(Form(role="attachment", location="references/m/board.png", hash="img9"))
    ws.write_manifest("m", man)
    h2 = _wi_hash(ws, ws.read_manifest("m"))
    assert h1 != h2
