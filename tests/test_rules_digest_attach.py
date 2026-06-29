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


def test_no_attachment_fingerprint_matches_legacy_formula(tmp_path):
    # 无附件时 digest 指纹必须与历史公式一致,避免部署后全量 spurious 重算
    from kairo.workspace import Workspace
    from kairo.models import Manifest, Form
    from kairo.provider import StubProvider
    from kairo.rules import DigestRule, _hash
    ws = Workspace.init(tmp_path / "ws", topic="t")
    rdir = ws.references_dir() / "m"; rdir.mkdir(parents=True)
    (rdir / "transcript.md").write_text("转写正文")
    ws.write_manifest("m", Manifest(id="m", title="m", forms=[
        Form(role="transcript", location="references/m/transcript.md", hash="t1"),
    ]))
    rule = DigestRule(ws, StubProvider())
    man = ws.read_manifest("m")
    body = rule._read_body(man)
    items = rule.discover()
    wi = next(i for i in items if i.key == "references/m/digest.md")
    legacy = _hash(f"{rule.prompt}\n\n---正文---\n{body}")
    assert wi.input_hash == legacy
