# tests/test_rules_digest_body.py
from kairo.workspace import Workspace
from kairo.models import Form
from kairo.provider import StubProvider
from kairo.rules import DigestRule


def test_read_body_concatenates_all_body_forms(tmp_path):
    ws = Workspace.init(tmp_path / "ws", topic="t")
    rdir = ws.references_dir() / "m"; rdir.mkdir(parents=True)
    (rdir / "transcript.md").write_text("会议口语转写")
    (rdir / "source_text.deck.md").write_text("PPT 正文要点")
    from kairo.models import Manifest
    man = Manifest(id="m", title="m", forms=[
        Form(role="transcript", location="references/m/transcript.md", hash="x"),
        Form(role="source_text", location="references/m/source_text.deck.md", hash="y"),
    ])
    body = DigestRule(ws, StubProvider())._read_body(man)
    assert "会议口语转写" in body and "PPT 正文要点" in body
    assert body.index("会议口语转写") < body.index("PPT 正文要点")  # transcript 在前
