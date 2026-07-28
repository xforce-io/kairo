"""markdown → html(产物预览用)。"""

from __future__ import annotations

import re

from markdown_it import MarkdownIt

_md = MarkdownIt("commonmark", {"html": False, "linkify": True}).enable("table")

# #107 / #99:仅恢复「空、仅 id=F-…」事实锚点;禁止其它属性(onclick 等)被放行。
# 例:<a id="F-8f7b5c-01"></a> 或 <a id='F-ab12-02' ></a>
_FACT_ANCHOR_RE = re.compile(
    r"""<a\s+id\s*=\s*(?P<q>['"])(?P<fid>F-[0-9a-f]+-\d+)(?P=q)\s*>\s*</a\s*>""",
    re.IGNORECASE,
)

# 占位符:Markdown 当纯文本,渲染后再换成真 <a id>；字符集避免被 md 改写
_PLACEHOLDER_FMT = "\ue000KAIROFACT{n}\ue001"


def render_markdown(text: str) -> str:
    """渲染 markdown 为 HTML。

    默认禁用原始 HTML(防注入)。#107:对 #99 空 F- 事实锚点做窄恢复——
    预览中不露字面 ``&lt;a id=…&gt;``,仍输出 ``<a id="F-…"></a>`` 供页内跳转。
    """
    if not text:
        return _md.render(text)

    placeholders: list[str] = []

    def _stash(m: re.Match[str]) -> str:
        placeholders.append(m.group("fid"))
        return _PLACEHOLDER_FMT.format(n=len(placeholders) - 1)

    stashed = _FACT_ANCHOR_RE.sub(_stash, text)
    html = _md.render(stashed)
    for i, fid in enumerate(placeholders):
        token = _PLACEHOLDER_FMT.format(n=i)
        # markdown-it 通常原样保留 BMP 私用区;若被包进文本节点也直接 replace
        html = html.replace(token, f'<a id="{fid}"></a>')
    return html
