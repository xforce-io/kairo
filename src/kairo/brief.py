"""#362 brief:一条 Ref 的一句话概述,由已落盘的 digest 派生并存进 manifest。

产出路径唯一:digest 成功后的旁路与 `kairo brief` 命令都走 :func:`generate_brief`。
契约不合就不写 manifest,不从正文猜、不回退。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from pathlib import Path

MAX_BRIEF_CHARS = 50

# provider 常把过程旁白与正文粘在同一行(digest.md 的 H1 也有同样毛病),所以锚点不限行首;
# 规则唯一:取最后一个 `BRIEF:` 之后、到行尾为止的内容。
BRIEF_ANCHOR = re.compile(r"BRIEF[:：][ \t]*(\S[^\n]*)")

_BRIEF_PERSONA = (
    "把这份纪要压成一句话,让人在时间轴列表上不点开就知道这条资料讲了什么。"
    "写实质内容:结论、争议、未决点或关键数字;不要写「本文讨论了…」这类空话。"
    "结论放前半句。\n"
    f"硬约束(违反即作废):总长不超过 {MAX_BRIEF_CHARS} 个字符,标点计入;"
    "只有一句,不分点,不加标题/引号/markdown 标记。\n"
    f"{MAX_BRIEF_CHARS} 字装不下全部议题时,只留最重要的一到两点,其余舍弃,"
    "不要靠压缩句子塞进更多信息。\n"
    "输出格式(唯一被接受的形式):最后一行必须是\n"
    "BRIEF: <这一句话>\n"
    "该行之前不要有正文;若你有过程说明,也必须让 BRIEF: 行是最后一行。\n"
    "`BRIEF:` 之后只放纪要内容本身:不要写你如何核对字数、如何遵守约束,"
    "也不要任何关于本次作答的自述。\n"
    "合规示例:\nBRIEF: 扫门分流已跑通,设备类型解析旧 bug 未定改期。"
)

_RETRY_HINT = (
    "上一版 BRIEF 是 {n} 个字符,超过上限 {limit}。删掉次要议题重写,不要改写成更密的长句。"
    f"仍然只用最后一行 BRIEF: 开头输出,不超过 {MAX_BRIEF_CHARS} 个字符;"
    "该行只放纪要内容,不要提字数、不要写核对过程。"
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
    """按锚点取正文:最后一个 `BRIEF: …` 行。

    provider 会把过程旁白粘在输出里(现网实测,digest.md 同样如此),所以正文位置不可靠,
    只认这一条锚点规则;没有锚点即失败,不去猜哪一段是正文。长度不在此判,便于纠正重试。
    """
    raw = (text or "").strip()
    if not raw:
        raise BriefError("brief is empty")
    found = BRIEF_ANCHOR.findall(raw)
    if not found:
        raise BriefError("brief anchor missing: no `BRIEF:` line")
    return found[-1].strip()


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


def clear_brief(ws, ref_id: str) -> None:
    """作废已存的 brief。宁可不展示,也不把过期的一句话当成当前 digest 的概述。"""
    man = ws.read_manifest(ref_id)
    if man.brief is None and man.brief_hash is None:
        return
    man.brief = None
    man.brief_hash = None
    ws.write_manifest(ref_id, man)


def brief_after_digest(ws, ref_id: str, *, provider=None) -> None:
    """digest 成功后的旁路。永不反噬 digest:失败只留 stderr 诊断。"""
    import sys

    try:
        path = digest_path(ws, ref_id)
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        if not brief_stale(ws.read_manifest(ref_id), text):
            return  # digest 内容没变,沿用现有 brief,不再调用 provider
        # 先作废再重算:重算失败时该行只显示标题,不会留下与新 digest 不符的旧概述。
        clear_brief(ws, ref_id)
        generate_brief(ws, ref_id, provider=provider)
    except Exception as exc:
        print(f"Warning: brief skipped ref={ref_id}: {exc}", file=sys.stderr, flush=True)
