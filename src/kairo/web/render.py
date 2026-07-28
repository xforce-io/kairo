"""markdown → html(产物预览用)。"""

from __future__ import annotations

import re

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


def render_markdown(text: str) -> str:
    """渲染 markdown 为 HTML。

    - 默认禁用原始 HTML(防注入)
    - #107:空 F- 事实锚点恢复为 ``<a id="F-…"></a>``
    - #109:``〔S-hex〕`` → 链到 ``#source-S-hex``;``〔依据:F-…〕`` → ``#F-…``;
      索引表 ID 列加 ``id="source-S-…"``
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
    return html
