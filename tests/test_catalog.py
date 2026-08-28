from pathlib import Path

from kairo.catalog import CatalogItem, format_catalog, read_dirs_for, stage_files
from kairo.provider import (
    AgentConfig,
    CodexProvider,
    GrokProvider,
    OpenAICompatibleProvider,
)
from kairo.models import REASON_PROVIDER_FAILED, State
from kairo.rules import ComposeRule, DigestRule
from kairo.workspace import Workspace


def test_format_catalog_has_role_origin_size_not_body(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("SECRET_BODY_SHOULD_NOT_APPEAR")
    items = [
        CatalogItem(
            rel_path="references/r/a.md",
            abs_path=f,
            role="source_text",
            origin="markitdown-from:abc",
            required=True,
            size=f.stat().st_size,
        )
    ]
    text = format_catalog(items)
    assert "必读" in text and "source_text" in text
    assert "markitdown-from:abc" in text
    assert f"{f.stat().st_size}B" in text
    assert "SECRET_BODY_SHOULD_NOT_APPEAR" not in text


def test_read_dirs_only_contains_selected_directories(tmp_path):
    required = tmp_path / "r1" / "a.md"
    optional = tmp_path / "r2" / "b.md"
    tree = tmp_path / "tree"
    required.parent.mkdir()
    optional.parent.mkdir()
    tree.mkdir()
    required.write_text("a")
    optional.write_text("b")
    items = [
        CatalogItem("r1/a.md", required, "source_text", "added", True, 1),
        CatalogItem("r2/b.md", optional, "attachment", "added", False, 1),
        CatalogItem("tree", tree, "corpus_tree", "corpus", False, 0),
    ]
    assert read_dirs_for(items) == [tree]


def test_stage_files_uses_unique_controlled_paths(tmp_path):
    a = tmp_path / "a" / "same.md"
    b = tmp_path / "b" / "same.md"
    c = tmp_path / "c" / "image.jpg"
    for path in (a, b, c):
        path.parent.mkdir()
    a.write_text("first")
    b.write_text("second")
    c.write_text("optional")
    art = tmp_path / "art"
    art.mkdir()
    items = [
        CatalogItem("../../escape.md", a, "source_text", "added", True, 5),
        CatalogItem("other/same.md", b, "source_text", "added", True, 6),
        CatalogItem("image.jpg", c, "attachment", "added", False, 8),
    ]
    stage_files(items, art)
    assert sorted(p.read_text() for p in (art / "required").iterdir()) == [
        "first",
        "second",
    ]
    assert sorted(p.read_text() for p in (art / "optional").iterdir()) == ["optional"]
    assert not (tmp_path / "escape.md").exists()
    assert "../../escape.md" in format_catalog(items)


def test_codex_does_not_make_read_dirs_writable(tmp_path):
    calls = []

    def fake_runner(cmd, args, *, cwd, input, stdout_file=None, timeout=None):
        calls.append(args)
        idx = args.index("--output-last-message")
        Path(args[idx + 1]).write_text("ok")

    extra = tmp_path / "ref"
    extra.mkdir()
    CodexProvider(runner=fake_runner).run(
        AgentConfig(
            persona="P",
            context="C",
            artifact_dir=tmp_path / "out",
            model="",
            artifact="digest.md",
            read_dirs=[extra],
        )
    )
    args = calls[0]
    assert "--add-dir" not in args
    assert str(extra) not in args


def test_grok_raises_on_read_dirs(tmp_path):
    p = GrokProvider()
    try:
        p.run(
            AgentConfig(
                persona="P",
                context="C",
                artifact_dir=tmp_path,
                model="",
                artifact="digest.md",
                read_dirs=[tmp_path],
            )
        )
    except RuntimeError as exc:
        assert "授读" in str(exc) or "read_dirs" in str(exc).lower()
    else:
        raise AssertionError("expected RuntimeError")


def test_openai_raises_on_read_dirs(tmp_path):
    p = OpenAICompatibleProvider(
        base_url="http://example.invalid", api_key="k", model="m"
    )
    try:
        p.run(
            AgentConfig(
                persona="P",
                context="C",
                artifact_dir=tmp_path,
                model="m",
                artifact="digest.md",
                read_dirs=[tmp_path],
            )
        )
    except RuntimeError as exc:
        assert "授读" in str(exc) or "read_dirs" in str(exc).lower()
    else:
        raise AssertionError("expected RuntimeError")


def test_digest_fails_closed_when_provider_does_not_declare_read_support(tmp_path):
    ws = Workspace.init(tmp_path)
    source = tmp_path / "meeting.txt"
    source.write_text("会议正文")
    ref_id = ws.add([source])

    class UnknownProvider:
        name = "unknown"
        model = "unknown"

        def run(self, config, signal=None):
            raise AssertionError("must fail before provider call")

    state = State()
    DigestRule(ws, UnknownProvider()).discover(state)[0].run(state)
    product = state.products[f"references/{ref_id}/digest.md"]
    assert product.status == "blocked"
    assert product.reason == REASON_PROVIDER_FAILED


def test_digest_context_is_catalog_not_body(tmp_path):
    ws = Workspace.init(tmp_path)
    t = tmp_path / "meeting.txt"
    t.write_text("会议正文内容ABC")
    ws.add([t])
    captured = {}

    class Probe:
        name = "probe"
        model = "probe"
        supports_read_dirs = True

        def run(self, config, signal=None):
            captured["context"] = config.context
            captured["read_dirs"] = list(config.read_dirs)
            dest = config.artifact_dir / (config.artifact or "digest.md")
            dest.write_text("D")
            return None

    DigestRule(ws, Probe()).discover()[0].run(State())
    ctx = captured["context"]
    assert "会议正文内容ABC" not in ctx
    assert "材料目录" in ctx
    assert "必读" in ctx
    assert captured["read_dirs"]


def test_compose_skips_judgment_target(tmp_path):
    ws = Workspace.init(tmp_path)
    t = tmp_path / "m.txt"
    t.write_text("x")
    rid = ws.add([t])
    d = ws.root / "references" / rid / "digest.md"
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_text("纪要")
    items = ComposeRule(ws, None).discover(State())
    assert [it.key for it in items] == ["understanding.md"]


def test_init_default_targets_only_understanding(tmp_path):
    ws = Workspace.init(tmp_path)
    assert [t.path for t in ws.constitution.targets] == ["understanding.md"]
    assert ws.constitution.targets[0].layer == "fact"
