"""#124:本机安装体检与 skill 分发。不碰 workspace / 引擎。"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from kairo import __version__
from kairo.machine import resolve_asr
from kairo.provider import select_provider

_SKILL_MD = "SKILL.md"
_NPX_HINT = "npx skills add xforce-io/kairo -g"


def _home() -> Path:
    return Path.home()


def canonical_skill_dir(home: Path | None = None) -> Path:
    return (home or _home()) / ".agents" / "skills" / "kairo"


def skill_source_file() -> Path | None:
    """包内 `kairo/data/SKILL.md`（wheel 与源码同路径）。"""
    packaged = Path(__file__).resolve().parent / "data" / _SKILL_MD
    if packaged.is_file():
        return packaged.resolve()
    return None


@dataclass(frozen=True)
class AgentMount:
    name: str
    detect: Path
    dest: Path


def agent_mounts(home: Path | None = None) -> tuple[AgentMount, ...]:
    h = home or _home()
    return (
        AgentMount("claude", h / ".claude", h / ".claude" / "skills" / "kairo"),
        AgentMount("cursor", h / ".cursor", h / ".cursor" / "skills" / "kairo"),
        AgentMount("codex", h / ".codex", h / ".codex" / "skills" / "kairo"),
        AgentMount("pi", h / ".pi" / "agent", h / ".pi" / "agent" / "skills" / "kairo"),
    )


def _web_installed() -> bool:
    try:
        import kairo.web.server  # noqa: F401
    except ImportError:
        return False
    return True


def _skill_md_bytes(md: Path) -> bytes | None:
    try:
        if md.is_file():
            return md.read_bytes()
    except OSError:
        return None
    return None


def _canonical_matches_packaged(canon: Path, src_file: Path) -> bool:
    """True iff canon/SKILL.md exists and its bytes equal the packaged operator skill."""
    got = _skill_md_bytes(canon / _SKILL_MD)
    if got is None:
        return False
    try:
        return got == src_file.read_bytes()
    except OSError:
        return False


def doctor_lines(*, home: Path | None = None) -> list[str]:
    """只读体检文案。不写盘。"""
    lines = [f"kairo {__version__}"]
    try:
        provider = select_provider()
        name = getattr(provider, "name", type(provider).__name__)
        model = (getattr(provider, "model", "") or "").strip()
        if name == "stub":
            lines.append(
                f"provider: {name}  ⚠ step 不会真算。"
                " 配 grok CLI / [provider.openai] / claude CLI，或设 KAIRO_PROVIDER"
            )
        elif model:
            lines.append(f"provider: {name} ({model})")
        else:
            lines.append(f"provider: {name}")
    except Exception as e:
        lines.append(f"provider: ⚠ {e}")

    asr = resolve_asr("whisper")
    if asr:
        lines.append(f"asr.whisper: 已配置 ({asr[1]})")
    else:
        cfg = (
            Path(os.environ.get("XDG_CONFIG_HOME") or (_home() / ".config"))
            / "kairo"
            / "config.toml"
        )
        lines.append("asr.whisper: 未配置  → 音频 step 会 blocked: no-asr")
        lines.append(f"  写入 {cfg} :")
        lines.append("  [asr.whisper]")
        lines.append(
            '  cmd = "mlx_whisper {input} --model mlx-community/whisper-large-v3-turbo'
            ' --language zh -f srt -o {outdir} --output-name {stem}"'
        )
        lines.append('  origin = "whisper:large-v3-turbo"')

    if _web_installed():
        lines.append("web extra: 已安装")
    else:
        lines.append(
            "web extra: 未安装  → uv tool install "
            "'git+https://github.com/xforce-io/kairo.git[web]'"
        )

    src = skill_source_file()
    canon = canonical_skill_dir(home)
    if src is None:
        lines.append("skill 源: ⚠ 包内没有 SKILL.md")
    else:
        lines.append(f"skill 源: {src}")
    if src is not None and _canonical_matches_packaged(canon, src):
        lines.append(f"skill: 已 connect ({canon})")
    elif (canon / _SKILL_MD).is_file():
        lines.append(f"skill: 未 connect（正文与包内 skill 不一致: {canon}）")
    else:
        lines.append(f"skill: 未 connect  → kairo connect  （或 {_NPX_HINT}）")
    return lines


def _install_canonical(src_file: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_file, dest / _SKILL_MD)


def _link_or_copy(src: Path, dest: Path) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    src_res = src.resolve()
    if dest.is_symlink():
        try:
            if dest.resolve() == src_res:
                return "ok"
        except OSError:
            pass
        dest.unlink()
    elif dest.exists():
        if dest.is_dir() and (dest / _SKILL_MD).is_file():
            try:
                if (dest / _SKILL_MD).read_text(encoding="utf-8") == (
                    src / _SKILL_MD
                ).read_text(encoding="utf-8"):
                    return "ok"
            except OSError:
                pass
        return "skip-exists"
    try:
        dest.symlink_to(src_res, target_is_directory=True)
        return "symlink"
    except OSError:
        shutil.copytree(src, dest)
        return "copy"


def connect_skill(*, home: Path | None = None) -> list[str]:
    """把包内 skill 拷到 ~/.agents/skills/kairo，并对已装 agent 挂链。

    Refuse when canonical is a symlink or an existing SKILL.md that does not
    match the packaged source — never write through a foreign target.
    """
    src_file = skill_source_file()
    if src_file is None:
        raise FileNotFoundError("找不到 SKILL.md（wheel 未打包 skill）")
    h = home or _home()
    canon = canonical_skill_dir(h)
    matches = _canonical_matches_packaged(canon, src_file)
    md = canon / _SKILL_MD
    foreign = canon.is_symlink() or md.is_symlink()
    if foreign or (md.is_file() and not matches):
        return [
            f"canonical occupied, not written: {canon} "
            "（外链或正文与包内 skill 不一致，拒绝覆盖）"
        ]
    _install_canonical(src_file, canon)
    lines = [f"canonical: {canon / _SKILL_MD}"]
    mounted = 0
    for agent in agent_mounts(h):
        if not agent.detect.is_dir():
            continue
        how = _link_or_copy(canon, agent.dest)
        if how == "skip-exists":
            lines.append(
                f"{agent.name}: skip {agent.dest}（已存在且不是本 skill 链接）"
            )
        else:
            mounted += 1
            lines.append(f"{agent.name}: {how} → {agent.dest}")
    if mounted == 0:
        lines.append(
            f"未探测到 Claude/Cursor/Codex/Pi 家目录；只写了 canonical。也可 {_NPX_HINT}"
        )
    return lines
