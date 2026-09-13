"""#371: understanding 术语表只收 topic 概念,专名以 Knowledge 为准。"""

import yaml

from kairo.engine import step
from kairo.models import (
    DEFAULT_UNDERSTANDING_FOLD,
    LEGACY_UNDERSTANDING_FOLD_371,
    resolve_fold_protocol,
)
from kairo.provider import AgentResult, _scan_artifacts, _stub_compose_document
from kairo.workspace import Workspace


class _PersonaCapturingProvider:
    """Stub-compatible provider that records the persona of every compose call."""

    name = "capture"
    model = "capture"
    supports_read_dirs = True

    def __init__(self):
        self.compose_personas: list[str] = []

    def run(self, config, signal=None):
        config.artifact_dir.mkdir(parents=True, exist_ok=True)
        art = config.artifact or "output.md"
        if art == "doc.md":
            self.compose_personas.append(config.persona)
            content = _stub_compose_document(
                config.persona, config.context, artifact_dir=config.artifact_dir
            )
        else:
            from kairo.provider import _stub_required_bodies

            bodies = _stub_required_bodies(config.context, config.artifact_dir)
            content = config.context + (("\n\n" + "\n\n".join(bodies)) if bodies else "")
        (config.artifact_dir / art).write_text(content)
        return AgentResult(artifacts=_scan_artifacts(config.artifact_dir))


def test_new_topic_fold_protocol_scopes_glossary_to_concepts(tmp_path):
    """S1: a fresh Topic carries the new default with all three required points."""
    ws = Workspace.init(tmp_path)
    fold = next(t for t in ws.constitution.targets if t.path == "understanding.md").fold_protocol
    assert fold == DEFAULT_UNDERSTANDING_FOLD
    assert "术语表只收本 topic 特有的概念与口径" in fold
    assert "专名以〔领域知识上下文〕为准按条目标题书写" in fold and "不进术语表" in fold
    assert "不标 ⚠️" in fold
    assert "维持一张去重的术语表" not in fold
    assert "未确认的挂 ⚠️" not in fold


def test_resolve_fold_protocol_replaces_only_verbatim_legacy_default():
    """S2 unit: legacy default → new default; anything edited stays byte-identical."""
    from kairo.models import _SUPERSEDED_UNDERSTANDING_FOLDS

    assert resolve_fold_protocol(LEGACY_UNDERSTANDING_FOLD_371) == DEFAULT_UNDERSTANDING_FOLD
    # Older generations shipped before #371 (still present on live Topics) migrate too.
    assert len(_SUPERSEDED_UNDERSTANDING_FOLDS) == 5
    for old in _SUPERSEDED_UNDERSTANDING_FOLDS:
        assert "未确认的挂 ⚠️" in old
        assert resolve_fold_protocol(old) == DEFAULT_UNDERSTANDING_FOLD
    assert DEFAULT_UNDERSTANDING_FOLD not in _SUPERSEDED_UNDERSTANDING_FOLDS
    assert resolve_fold_protocol(DEFAULT_UNDERSTANDING_FOLD) == DEFAULT_UNDERSTANDING_FOLD
    edited = LEGACY_UNDERSTANDING_FOLD_371 + "\n只写中文。"
    assert resolve_fold_protocol(edited) == edited
    assert resolve_fold_protocol("") == ""
    assert LEGACY_UNDERSTANDING_FOLD_371 != DEFAULT_UNDERSTANDING_FOLD


def _write_fold(ws: Workspace, fold: str) -> Workspace:
    con = ws.constitution
    for target in con.targets:
        if target.path == "understanding.md":
            target.fold_protocol = fold
    (ws.root / "constitution.yaml").write_text(
        yaml.safe_dump(con.model_dump(), allow_unicode=True, sort_keys=False)
    )
    return Workspace.open(ws.root)


def test_compose_feeds_new_default_for_legacy_topic_and_keeps_custom_text(tmp_path):
    """S2 integration: an old Topic composes with the new text; a customised one is untouched on disk and in prompt."""
    legacy_ws = _write_fold(Workspace.init(tmp_path / "legacy"), LEGACY_UNDERSTANDING_FOLD_371)
    material = tmp_path / "m.txt"
    material.write_text("材料")
    legacy_ws.add([material])
    provider = _PersonaCapturingProvider()
    step(legacy_ws, provider)
    assert provider.compose_personas, "compose did not run"
    assert provider.compose_personas[-1].startswith(DEFAULT_UNDERSTANDING_FOLD)
    assert "维持一张去重的术语表" not in provider.compose_personas[-1]
    # Migration is read-time only: constitution.yaml still holds the legacy text.
    on_disk = yaml.safe_load((legacy_ws.root / "constitution.yaml").read_text())
    assert on_disk["targets"][0]["fold_protocol"] == LEGACY_UNDERSTANDING_FOLD_371

    custom_text = "自定义折叠协议:只写三段。"
    custom_ws = _write_fold(Workspace.init(tmp_path / "custom"), custom_text)
    custom_ws.add([material])
    provider = _PersonaCapturingProvider()
    step(custom_ws, provider)
    assert provider.compose_personas[-1].startswith(custom_text)
    assert DEFAULT_UNDERSTANDING_FOLD not in provider.compose_personas[-1]
