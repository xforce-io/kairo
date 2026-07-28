"""Test markdown rendering to HTML."""

from kairo.web.render import render_markdown


def test_render_heading_and_paragraph():
    html = render_markdown("# 标题\n\n正文一段。")
    assert "<h1>" in html and "标题" in html
    assert "<p>" in html


def test_render_table():
    html = render_markdown("| a | b |\n|---|---|\n| 1 | 2 |")
    assert "<table>" in html


def test_render_fact_anchor_not_visible_literal_keeps_id():
    """#107 S1:空 F- 锚点预览不可见字面标签,仍保留 id 属性。"""
    md = (
        '## 材料\n\n'
        '<a id="F-8f7b5c-01"></a>本 topic 当前可核对材料。〔S-8f7b5c〕\n'
    )
    html = render_markdown(md)
    # 不得把标签当可见文本转义出来
    assert "&lt;a id=" not in html
    assert "&lt;a id=&quot;F-8f7b5c-01&quot;" not in html
    # 必须有可用的 id 锚点
    assert 'id="F-8f7b5c-01"' in html
    assert "本 topic 当前可核对材料" in html
    # 空 a 标签(或等价),非转义实体
    assert "<a " in html or "<a>" in html


def test_render_fact_anchor_whitespace_and_single_quotes():
    """#107 S1:空白与单引号 id 形式。"""
    md = "<a id='F-ab12cd-02' ></a> 事实句。\n"
    html = render_markdown(md)
    assert "&lt;a id=" not in html
    assert 'id="F-ab12cd-02"' in html or "id='F-ab12cd-02'" in html


def test_render_rejects_script_as_live_html():
    """#107 S2:script 不得作为可执行标签放出。"""
    html = render_markdown("前缀 <script>alert(1)</script> 后缀")
    assert "<script>" not in html.lower().replace("&lt;script", "")
    # 允许转义后的可见文本,但不允许真 script 节点
    assert "<script" not in html
    assert "后缀" in html


def test_render_rejects_onclick_anchor_as_live():
    """#107 S2:带事件处理器的伪锚点不得活标签输出。"""
    import re

    md = '<a id="F-8f7b5c-01" onclick="alert(1)"></a>正文'
    html = render_markdown(md)
    # 不得出现未转义的活 <a ... onclick=...>
    assert re.search(r"<a\b[^>]*\bonclick\s*=", html, re.I) is None
    # 带额外属性的伪锚点不得被「恢复」成活 a(仅允许纯空 F- 锚点)
    assert re.search(r'<a\s+id="F-8f7b5c-01"\s*></a>', html) is None or (
        "onclick" not in html.split('<a id="F-8f7b5c-01"')[0][-20:]
    )
    # 更严:整页无活 a+onclick
    assert not re.search(r"<a\b[^>]*onclick", html, re.I)


def test_render_s_citation_links_to_source_index_row():
    """#109 S1:〔S-hex〕→ 可点链接,指向 #source-S-…;索引行带 id。"""
    import re

    md = """## 订单

关键时限 24 小时〔S-8f7b5c〕。

## 来源索引

| ID | 材料 | 可核对来源 |
|---|---|---|
| S-8f7b5c | 访谈 | [digest](references/2026-07-28-x/digest.md) |
"""
    html = render_markdown(md)
    # 正文引用是链接
    assert re.search(
        r'<a\s+href="#source-S-8f7b5c"[^>]*>[^<]*S-8f7b5c[^<]*</a>',
        html,
    ), html
    # 索引行有落地 id
    assert 'id="source-S-8f7b5c"' in html
    # digest 相对链仍在
    assert "references/2026-07-28-x/digest.md" in html


def test_render_fact_basis_citation_links_to_f_anchor():
    """#109 S2:〔依据:F-…〕与全角冒号 → #F-…;空锚点仍不可见字面。"""
    import re

    md = (
        '<a id="F-8f7b5c-01"></a>事实句。\n\n'
        "判断成立〔依据:F-8f7b5c-01〕。\n\n"
        "另一条〔依据：F-8f7b5c-01〕。\n"
    )
    html = render_markdown(md)
    assert "&lt;a id=" not in html
    assert 'id="F-8f7b5c-01"' in html
    links = re.findall(r'<a\s+href="(#F-8f7b5c-01)"[^>]*>', html)
    assert links.count("#F-8f7b5c-01") >= 2, html


def test_render_citation_patterns_do_not_open_xss():
    """#109 S3:伪 citation / 脚本不得变成活危险标签。"""
    import re

    html = render_markdown(
        "坏〔S-<script>x</script>〕完 "
        "〔依据:F-\" onclick=alert(1) x〕 "
        "<script>alert(1)</script>"
    )
    assert "<script" not in html
    assert not re.search(r"<a\b[^>]*onclick", html, re.I)
    # 非 hex 的 S- 不 linkify 成 source 链
    assert 'href="#source-S-' not in html or "script" not in html.split('href="#source-S-')[0]
