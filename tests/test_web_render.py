"""Test markdown rendering to HTML."""

from kairo.web.render import render_markdown


def test_render_heading_and_paragraph():
    html = render_markdown("# 标题\n\n正文一段。")
    assert "<h1>" in html and "标题" in html
    assert "<p>" in html


def test_render_table():
    html = render_markdown("| a | b |\n|---|---|\n| 1 | 2 |")
    assert "<table>" in html
