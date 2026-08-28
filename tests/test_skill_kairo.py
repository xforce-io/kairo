"""#91: kairo operator skill 必须存在且协议要点不可被掏空。

驱动真实仓库内 SKILL.md（与 README 安装节指向的同一路径），
断言 frontmatter + 发现/两层读/blocked/写确认 等铁律仍在正文中。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGED_SKILL = REPO_ROOT / "src" / "kairo" / "data" / "SKILL.md"
SKILL_PATH = REPO_ROOT / "skills" / "kairo" / "SKILL.md"
CLAUDE_SKILL_LINK = REPO_ROOT / ".claude" / "skills" / "kairo"
GITIGNORE_PATH = REPO_ROOT / ".gitignore"


def _skill_text() -> str:
    assert SKILL_PATH.is_file(), f"missing skill artifact: {SKILL_PATH}"
    return SKILL_PATH.read_text(encoding="utf-8")


def test_skill_file_exists_with_frontmatter():
    text = _skill_text()
    assert text.startswith("---\n"), "SKILL.md must start with YAML frontmatter"
    # closed frontmatter + name/description required for agent loaders
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert m, "frontmatter must be closed with ---"
    fm = m.group(1)
    assert re.search(r"(?m)^name:\s*kairo\s*$", fm)
    assert re.search(r"(?m)^description:\s*\S", fm)
    # description should advertise discover/status/read triggers
    desc = re.search(r"(?m)^description:\s*(.+)$", fm)
    assert desc and ("status" in desc.group(1).lower() or "调研" in desc.group(1))


def test_skill_path_is_not_gitignored():
    """#91/#124: skill 真身必须可入库；裸 `.claude/` 全忽略会让开发态软链消失。"""
    gi = GITIGNORE_PATH.read_text(encoding="utf-8")
    assert "!.claude/skills/" in gi or "!.claude/skills/**" in gi
    assert not re.search(r"(?m)^\s*\.claude/\s*$", gi), (
        ".gitignore must not blanket-ignore .claude/ without un-ignoring skills"
    )
    for rel in (
        SKILL_PATH.relative_to(REPO_ROOT).as_posix(),
        CLAUDE_SKILL_LINK.relative_to(REPO_ROOT).as_posix(),
    ):
        proc = subprocess.run(
            ["git", "check-ignore", "-q", rel],
            cwd=REPO_ROOT,
            check=False,
        )
        assert proc.returncode != 0, f"{rel} is gitignored and would not ship"


def test_claude_skill_is_symlink_to_canonical():
    """仓内只有一份 SKILL.md 正文；skills/ 与 .claude/ 都指向它。"""
    assert PACKAGED_SKILL.is_file()
    assert SKILL_PATH.resolve() == PACKAGED_SKILL.resolve()
    assert CLAUDE_SKILL_LINK.is_symlink()
    assert (CLAUDE_SKILL_LINK / "SKILL.md").resolve() == PACKAGED_SKILL.resolve()


def test_skill_covers_discovery_status_and_understanding():
    text = _skill_text()
    for needle in (
        "constitution.yaml",
        "kairo status",
        "understanding.md",
    ):
        assert needle in text, f"skill must mention {needle!r}"
    assert "事实" in text
    # read order / not treating transcript as conclusion
    assert "transcript" in text.lower()
    assert "结论" in text or "最终结论" in text


def test_skill_write_commands_require_confirmation():
    text = _skill_text()
    # iron law: default read-only / confirm before write
    assert "确认" in text
    assert "只读" in text or "看永远便宜" in text
    for cmd in (
        "kairo step",
        "kairo run",
        "kairo re-step",
        "kairo accept",
        "kairo rollback",
        "kairo add",
        "kairo archive",
        "retry-ref",
        "kairo rm-ref",
    ):
        assert cmd in text, f"skill must map write intent for {cmd!r}"
    # 推进默认 step，勿与更重的 run 混写
    assert "默认" in text and "kairo step" in text
    assert "勿把口语" in text or "不要" in text or "勿" in text


def test_skill_blocked_closed_set():
    text = _skill_text()
    for reason in (
        "no-asr",
        "asr-failed",
        "convert-failed",
        "missing-source",
        "manual-edit",
        "compose-degraded",
    ):
        assert reason in text, f"blocked closed set missing {reason!r}"


def test_skill_archive_protocol():
    text = _skill_text()
    assert "kairo archive" in text
    assert "原始 transcript" in text or "原始会话" in text
    assert "--create" in text and "--bind" in text
    assert "不" in text and "step" in text


def test_skill_common_mistakes_and_no_auto_accept():
    text = _skill_text()
    assert "Common mistakes" in text or "常见错误" in text
    assert "不代批" in text and "accept" in text.lower()
    # pure-read must not auto-step
    assert "step" in text and ("绝不" in text or "不得" in text or "零写" in text)


@pytest.mark.parametrize(
    "readme_name",
    ["README.md", "README.zh-CN.md"],
)
def test_readme_documents_skill_install(readme_name: str):
    readme = REPO_ROOT / readme_name
    text = readme.read_text(encoding="utf-8")
    assert "git+https://github.com/xforce-io/kairo.git" in text
    assert "kairo connect" in text
    assert "kairo doctor" in text
    assert "skills/kairo" in text
    assert "ln -s" not in text
