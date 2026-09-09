"""markdown → html(产物预览用)。"""

from __future__ import annotations

import csv
import io
import json
import re
from html import escape
from urllib.parse import quote, unquote

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


def _rewrite_digest_links(html: str, slug: str, ref_home: str | None = None) -> str:
    """把相对 digest 路径改写成 /w/{slug}/ref/{id}/form/digest,并挂 hx 供阅读区加载。"""
    qslug = quote(slug, safe="")

    def _repl(m: re.Match[str]) -> str:
        ref_id = unquote(m.group(1))
        if not _is_safe_ref_id(ref_id):
            return m.group(0)
        if ref_home is not None:
            query = f"home={quote(ref_home, safe='')}"
            href = f"/w/{qslug}?ref={quote(ref_id, safe='')}&{query}"
            url = f"/w/{qslug}/ref/{quote(ref_id, safe='')}?{query}"
            return (
                f'href="{escape(href, quote=True)}" hx-get="{url}" '
                f'hx-target="#meta" hx-push-url="{escape(href, quote=True)}"'
            )
        url = f"/w/{qslug}/ref/{quote(ref_id, safe='')}/form/digest"
        return (
            f'href="{url}" '
            f'hx-get="{url}" hx-target="#reader" hx-swap="innerHTML show:top"'
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


def render_markdown(
    text: str, *, slug: str | None = None, ref_home: str | None = None
) -> str:
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
        html = _rewrite_digest_links(html, slug, ref_home)
    return html


_SECTION_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.M)


def _cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "、".join(part for part in (_cell_text(item) for item in value) if part)
    if isinstance(value, dict):
        if value.get("userName"):
            return str(value.get("userName") or "")
        if "text" in value:
            return str(value.get("text") or "")
        return "、".join(
            f"{key}:{_cell_text(item)}"
            for key, item in value.items()
            if _cell_text(item)
        )
    return str(value)


def _html_table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = []
    for row in rows:
        padded = list(row) + [""] * (len(headers) - len(row))
        body.append("<tr>" + "".join(f"<td>{escape(c)}</td>" for c in padded[: len(headers)]) + "</tr>")
    return (
        '<div class="sheet-preview"><table>'
        f"<thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody>"
        "</table></div>"
    )


def _trim_empty_columns(headers: list[str], rows: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    width = len(headers)
    while width > 1:
        last = width - 1
        header_empty = not headers[last].strip()
        cells_empty = all(len(row) <= last or not str(row[last]).strip() for row in rows)
        if header_empty and cells_empty:
            width -= 1
            continue
        break
    headers = headers[:width]
    rows = [(row + [""] * width)[:width] for row in rows]
    return headers, rows


def _csv_table(text: str, *, min_columns: int = 2) -> str | None:
    sample = text.strip()
    if not sample or "," not in sample.splitlines()[0]:
        return None
    try:
        parsed = list(csv.reader(io.StringIO(sample)))
    except csv.Error:
        return None
    parsed = [row for row in parsed if any(cell.strip() for cell in row)]
    if len(parsed) < 2:
        return None
    width = max(len(row) for row in parsed)
    if width < min_columns:
        return None
    rich = sum(1 for row in parsed if sum(1 for cell in row if str(cell).strip()) >= 2)
    if rich < 2:
        return None
    headers = [(cell.strip() or f"列{i + 1}") for i, cell in enumerate((parsed[0] + [""] * width)[:width])]
    rows = [[str(cell) for cell in (row + [""] * width)[:width]] for row in parsed[1:]]
    headers, rows = _trim_empty_columns(headers, rows)
    return _html_table(headers, rows)


def _json_table(text: str) -> str | None:
    sample = text.strip()
    if not sample or sample[0] not in "[{":
        return None
    try:
        data = json.loads(sample)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict) and isinstance(data.get("records"), list):
        data = data["records"]
    if not isinstance(data, list) or not data:
        return None
    if all(isinstance(item, dict) for item in data):
        use_values = any(isinstance(item.get("values"), dict) for item in data)
        headers: list[str] = []
        seen: set[str] = set()

        def add(key: str) -> None:
            if key not in seen:
                seen.add(key)
                headers.append(key)

        if use_values:
            add("update_time")
            for item in data:
                values = item.get("values") if isinstance(item.get("values"), dict) else {}
                for key in values:
                    add(key)
            rows = []
            for item in data:
                values = item.get("values") if isinstance(item.get("values"), dict) else {}
                row = []
                for key in headers:
                    if key in item and key not in values:
                        row.append(_cell_text(item.get(key)))
                    else:
                        row.append(_cell_text(values.get(key)))
                rows.append(row)
        else:
            for item in data:
                for key in item:
                    add(key)
            rows = [[_cell_text(item.get(key)) for key in headers] for item in data]
        if len(headers) < 1 or len(rows) < 1:
            return None
        return _html_table(headers, rows)
    if all(isinstance(item, list) for item in data) and len(data) >= 2:
        headers = [_cell_text(cell) or f"列{i + 1}" for i, cell in enumerate(data[0])]
        rows = [[_cell_text(cell) for cell in row] for row in data[1:]]
        if len(headers) < 2:
            return None
        return _html_table(headers, rows)
    return None


def _block_html(body: str) -> str:
    text = body.strip()
    if not text:
        return ""
    return _json_table(text) or _csv_table(text) or f'<pre class="doc-plain">{escape(text)}</pre>'


_SHEET_KINDS = frozenset({"spreadsheet", "smartsheet"})


def _obviously_tabular(text: str) -> bool:
    body = _SECTION_HEADING_RE.sub("", text).strip()
    if not body:
        return False
    return _json_table(body) is not None or _csv_table(body, min_columns=3) is not None


def preview_datasource_html(text: str, *, kind: str | None = None) -> str:
    """表格类 Data Source 走表；文档类走 markdown。kind 未知时只认明显的表。"""
    if not (text or "").strip():
        return ""
    if kind in _SHEET_KINDS:
        return render_sheet_preview(text)
    if kind:
        return render_markdown(text)
    if _obviously_tabular(text):
        return render_sheet_preview(text)
    return render_markdown(text)


def render_sheet_preview(text: str) -> str:
    """把 Data Source 缓存正文渲成可读表格；非表格块保持纯文本。不改存数。"""
    if not (text or "").strip():
        return ""
    parts: list[str] = []
    cursor = 0
    matches = list(_SECTION_HEADING_RE.finditer(text))
    if not matches:
        return _block_html(text)
    for match in matches:
        if match.start() > cursor:
            parts.append(_block_html(text[cursor : match.start()]))
        level = min(len(match.group(1)), 3)
        parts.append(f"<h{level}>{escape(match.group(2).strip())}</h{level}>")
        cursor = match.end()
    if cursor < len(text):
        parts.append(_block_html(text[cursor:]))
    return "".join(part for part in parts if part)
