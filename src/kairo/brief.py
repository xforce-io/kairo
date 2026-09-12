"""#362 brief:一条 Ref 的一句话概述,由已落盘的 digest 派生并存进 manifest。

产出路径唯一:digest 成功后的旁路与 `kairo brief` 命令都走 :func:`generate_brief`。
契约不合就不写 manifest,不从正文猜、不回退。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

MAX_BRIEF_CHARS = 50

_BRIEF_PERSONA = (
    "把这份纪要压成一句话,让人在时间轴列表上不点开就知道这条资料讲了什么。"
    "写实质内容:结论、争议、未决点或关键数字;不要写「本文讨论了…」这类空话。"
    "结论放前半句。\n"
    f"硬约束(违反即作废):总长不超过 {MAX_BRIEF_CHARS} 个字符,标点计入;"
    "只有一句,不换行,不分点,不加标题/引号/markdown 标记;不做解释、不加前后缀。\n"
    f"{MAX_BRIEF_CHARS} 字装不下全部议题时,只留最重要的一到两点,其余舍弃,"
    "不要靠压缩句子塞进更多信息。\n"
    "示例(合规):扫门分流已跑通,设备类型解析旧 bug 未定改期。\n"
    "示例(违规,过长且分点):本次会议讨论了扫门分流的整体进展、标注页的产品形态、"
    "日志系统的拆分计划以及 IPD 中台接口的排期分歧,其中…\n"
    "只输出这一句话本身。"
)

_RETRY_HINT = (
    "上一版是 {n} 个字符,超过上限 {limit}。删掉次要议题重写,不要改写成更密的长句。"
    f"只输出一句、不超过 {MAX_BRIEF_CHARS} 个字符的正文。"
)


class BriefError(ValueError):
    """brief 产出失败或不合契约。"""


def digest_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def digest_path(ws, ref_id: str) -> Path:
    return ws.references_dir() / ref_id / "digest.md"


def brief_stale(man, digest_text: str) -> bool:
    """无 brief,或现有 brief 不是当前 digest 产出的。"""
    if not (man.brief or "").strip():
        return True
    return man.brief_hash != digest_hash(digest_text)


def normalize_brief(text: str) -> str:
    """规范成单行:非空、只有一行。长度不在此判,便于对超长做一次纠正重试。"""
    raw = (text or "").strip()
    if not raw:
        raise BriefError("brief is empty")
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if len(lines) != 1:
        raise BriefError(f"brief must be a single line, got {len(lines)}")
    return lines[0]


def check_brief(text: str) -> str:
    """契约校验:非空、单行、不超过 MAX_BRIEF_CHARS 字。不合则抛,调用方不写盘。"""
    one = normalize_brief(text)
    if len(one) > MAX_BRIEF_CHARS:
        raise BriefError(f"brief too long: {len(one)} > {MAX_BRIEF_CHARS}")
    return one


def provider_generator(provider) -> Callable[[str], str]:
    """把一句话压缩交给当前 provider;与 digest 用同一条 agent 通路。"""

    def generate(body: str) -> str:
        from kairo.rules import _run_agent

        return _run_agent(provider, _BRIEF_PERSONA, body, "brief.txt")

    return generate


def generate_brief(
    ws,
    ref_id: str,
    *,
    provider=None,
    generator: Callable[[str], str] | None = None,
) -> str:
    """从该 Ref 的 digest 产出 brief 并写进 manifest;失败抛 BriefError 且不写盘。"""
    from kairo.review import strip_process_preamble

    path = digest_path(ws, ref_id)
    if not path.is_file():
        raise BriefError(f"digest not found: {ref_id}")
    text = path.read_text(encoding="utf-8")
    body = strip_process_preamble(text)
    if not body.strip():
        raise BriefError(f"digest is empty: {ref_id}")
    generate = generator or (provider_generator(provider) if provider else None)
    if generate is None:
        raise BriefError("no provider for brief")

    def call(extra: str = "") -> str:
        try:
            return generate(body + extra)
        except BriefError:
            raise
        except Exception as exc:
            raise BriefError(f"provider failed: {exc}") from exc

    first = normalize_brief(call())
    if len(first) <= MAX_BRIEF_CHARS:
        one = first
    else:
        # 单次确定性纠正:只对超长回一次带实测字数的指令;仍不合契约即失败,不再重试。
        one = check_brief(call("\n\n" + _RETRY_HINT.format(n=len(first), limit=MAX_BRIEF_CHARS)))
    man = ws.read_manifest(ref_id)
    man.brief = one
    man.brief_hash = digest_hash(text)
    ws.write_manifest(ref_id, man)
    return one


def brief_after_digest(ws, ref_id: str, *, provider=None) -> None:
    """digest 成功后的旁路。永不反噬 digest:失败只留 stderr 诊断。"""
    import sys

    try:
        generate_brief(ws, ref_id, provider=provider)
    except Exception as exc:
        print(f"Warning: brief skipped ref={ref_id}: {exc}", file=sys.stderr, flush=True)
