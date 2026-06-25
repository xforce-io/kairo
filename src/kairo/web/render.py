"""markdown → html(产物预览用)。"""

from __future__ import annotations

from markdown_it import MarkdownIt

_md = MarkdownIt("commonmark", {"html": False, "linkify": True}).enable("table")


def render_markdown(text: str) -> str:
    """渲染 markdown 为 HTML;禁用原始 HTML(防注入),开表格 + 自动链接。"""
    return _md.render(text)
