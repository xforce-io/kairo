"""markdown → html(产物预览用)。"""

from __future__ import annotations

import re
from urllib.parse import quote

from markdown_it import MarkdownIt

_md = MarkdownIt("commonmark", {"html": False, "linkify": True}).enable("table")

# #107 / #99:仅恢复「空、仅 id=F-…」事实锚点;禁止其它属性(onclick 等)被放行。
_FACT_ANCHOR_RE = re.compile(
    r"""<a\s+id\s*=\s*(?P<q>['"])(?P<fid>F-[0-9a-f]+-\d+)(?P=q)\s*>\s*</a\s*>""",
    re.IGNORECASE,
)

# #109:正文来源短 ID → 页内来源索引行(仅 hex,防注入)
_S_CITE_RE = re.compile(r"〔S-([0-9a-f]+)〕")
# #109:判断依据 → 页内 F- 锚点(半角/全角冒号)
_F_BASIS_RE = re.compile(r"〔依据[:：](F-[0-9a-f]+-\d+)〕")

# 索引表首列 S- id → 给 td 加 id=source-S-…
_INDEX_TD_RE = re.compile(
    r"(<td>)(S-[0-9a-f]+)(</td>)",
    re.IGNORECASE,
)

_PLACEHOLDER_FMT = "\ue000KAIROFACT{n}\ue001"

# #111:来源索引 [digest](references/<ref_id>/digest.md) → console 预览路由
_DIGEST_REL_HREF_RE = re.compile(
    r'href="references/([^"/]+)/digest\.md"'
)


def _is_safe_ref_id(ref_id: str) -> bool:
    """拒绝路径穿越与空段;允许与 workspace ref 目录名兼容的字符。"""
    if not ref_id or ref_id in (".", ".."):
        return False
    if "/" in ref_id or "\\" in ref_id or ".." in ref_id:
        return False
    # 与常见 ref_id 一致:日期-slug / hex 等,不含空白与引号
    return bool(re.fullmatch(r"[\w.\-]+", ref_id, flags=re.UNICODE))


def _rewrite_digest_links(html: str, slug: str) -> str:
    """把相对 digest 路径改写成 /w/{slug}/ref/{id}/form/digest,并挂 hx 供阅读区加载。"""
    qslug = quote(slug, safe="")

    def _repl(m: re.Match[str]) -> str:
        ref_id = m.group(1)
        if not _is_safe_ref_id(ref_id):
            return m.group(0)
        url = f"/w/{qslug}/ref/{quote(ref_id, safe='')}/form/digest"
        return (
            f'href="{url}" '
            f'hx-get="{url}" hx-target="#reader" hx-swap="innerHTML"'
        )

    return _DIGEST_REL_HREF_RE.sub(_repl, html)


def _linkify_citations(text: str) -> str:
    """将规范 〔S-…〕/〔依据:F-…〕 换成 Markdown 链接(html 关闭时仍安全)。"""

    def _s(m: re.Match[str]) -> str:
        sid = m.group(1)
        label = f"〔S-{sid}〕"
        return f"[{label}](#source-S-{sid})"

    def _f(m: re.Match[str]) -> str:
        fid = m.group(1)
        # 统一半角冒号展示,href 用 F- id
        label = f"〔依据:{fid}〕"
        return f"[{label}](#{fid})"

    text = _S_CITE_RE.sub(_s, text)
    text = _F_BASIS_RE.sub(_f, text)
    return text


def _tag_source_index_rows(html: str) -> str:
    """来源索引表 ID 列加 id=source-S-…,供正文 S- 链页内跳转。"""

    def _td(m: re.Match[str]) -> str:
        sid = m.group(2)
        # 规范小写 hex 与正文 href 一致
        sid_norm = "S-" + sid[2:].lower() if sid.upper().startswith("S-") else sid
        if not re.fullmatch(r"S-[0-9a-f]+", sid_norm):
            return m.group(0)
        return f'<td id="source-{sid_norm}">{sid}</td>'

    return _INDEX_TD_RE.sub(_td, html)


def render_markdown(text: str, *, slug: str | None = None) -> str:
    """渲染 markdown 为 HTML。

    - 默认禁用原始 HTML(防注入)
    - #107:空 F- 事实锚点恢复为 ``<a id="F-…"></a>``
    - #109:``〔S-hex〕`` → 链到 ``#source-S-hex``;``〔依据:F-…〕`` → ``#F-…``;
      索引表 ID 列加 ``id="source-S-…"``
    - #111:传入 ``slug`` 时,``references/<ref>/digest.md`` 重写为 console
      ``/w/{slug}/ref/{ref}/form/digest``(无 slug 则保留相对路径)
    """
    if not text:
        return _md.render(text)

    placeholders: list[str] = []

    def _stash(m: re.Match[str]) -> str:
        placeholders.append(m.group("fid"))
        return _PLACEHOLDER_FMT.format(n=len(placeholders) - 1)

    # 1) 先 stash 空 F- 锚点,避免被当 HTML 转义
    stashed = _FACT_ANCHOR_RE.sub(_stash, text)
    # 2) 再把 citation 编成 md 链接
    stashed = _linkify_citations(stashed)
    html = _md.render(stashed)
    for i, fid in enumerate(placeholders):
        token = _PLACEHOLDER_FMT.format(n=i)
        html = html.replace(token, f'<a id="{fid}"></a>')
    # 3) 索引行落地 id
    html = _tag_source_index_rows(html)
    # 4) Web console:相对 digest → 预览路由
    if slug:
        html = _rewrite_digest_links(html, slug)
    return html
