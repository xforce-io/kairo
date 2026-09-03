"""#138 发生时间、时间轴扫描、Web 查询契约。"""

from __future__ import annotations

import calendar
import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

_ID_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:-|$)")
_MONTH = re.compile(r"^(\d{4})-(\d{2})$")


class TimelineQueryError(ValueError):
    """非法 / 互斥的时间轴查询。"""


def parse_calendar_date(text: str | None) -> dt.date | None:
    """合法公历日；格式不对或 02-31 一类 → None。"""
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return None
    try:
        return dt.date.fromisoformat(raw)
    except ValueError:
        return None


def parse_added_at(text: str | None) -> dt.datetime | None:
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        return dt.datetime.fromisoformat(text.strip())
    except ValueError:
        return None


def effective_occurred(
    ref_id: str, occurred_at: str | None
) -> tuple[dt.date | None, str]:
    """(发生日, user|id|unknown)。不读盘。"""
    parsed = parse_calendar_date(occurred_at)
    if parsed is not None:
        return parsed, "user"
    m = _ID_DATE.match(ref_id)
    if m:
        parsed = parse_calendar_date(m.group(1))
        if parsed is not None:
            return parsed, "id"
    return None, "unknown"


def is_fold_class(ws, source_class: str) -> bool:
    sc = ws.constitution.source_classes.get(source_class)
    return sc is None or sc.fold


def _mtime_local_iso(path: Path) -> str:
    return dt.datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat()


def effective_added_at(added_at: str | None, manifest_path: Path) -> dt.datetime:
    parsed = parse_added_at(added_at)
    if parsed is not None:
        if parsed.tzinfo is None:
            return parsed.astimezone()
        return parsed
    return dt.datetime.fromtimestamp(manifest_path.stat().st_mtime).astimezone()


@dataclass(frozen=True)
class TimelineItem:
    workspace: str
    topic: str
    id: str
    title: str
    occurred_at: dt.date | None
    occurred_source: str
    added_at: dt.datetime
    tags: tuple[str, ...] = ()


def scan_timeline(root: Path | str) -> list[TimelineItem]:
    """扫全部可访问 Ref（Topic home + 全局库）；损坏 manifest 跳过。"""
    from kairo.refs import list_all_refs, resolve_open
    from kairo.workspace import WorkspaceNotFound

    root = Path(root).resolve()
    items: list[TimelineItem] = []
    if not root.is_dir():
        return items
    for rec in list_all_refs(root):
        try:
            ws, ref_id = resolve_open(root, rec.home, rec.id)
            man = ws.read_manifest(ref_id)
        except (WorkspaceNotFound, Exception):
            continue
        occ, src = effective_occurred(ref_id, man.occurred_at)
        man_path = ws.references_dir() / ref_id / "manifest.yaml"
        if not man_path.is_file():
            continue
        added = effective_added_at(man.added_at, man_path)
        topic_name = rec.home
        if rec.home:
            try:
                topic_name = ws.constitution.topic
            except Exception:
                topic_name = rec.home
        else:
            topic_name = "global"
        items.append(
            TimelineItem(
                workspace=rec.home or "global",
                topic=topic_name,
                id=ref_id,
                title=man.title or ref_id,
                occurred_at=occ,
                occurred_source=src,
                added_at=added,
                tags=tuple(rec.tags),
            )
        )
    return items


def filter_by_tags(items: list[TimelineItem], tags: list[str]) -> list[TimelineItem]:
    """多 Tag 筛选为 AND：结果必须带上给出的每一个 Tag。"""
    wanted = [t for t in tags if t]
    if not wanted:
        return items
    need = set(wanted)
    return [it for it in items if need.issubset(set(it.tags))]


def item_as_json(it: TimelineItem) -> dict:
    return {
        "workspace": it.workspace,
        "topic": it.topic,
        "id": it.id,
        "title": it.title,
        "occurred_at": it.occurred_at.isoformat() if it.occurred_at else None,
        "occurred_source": it.occurred_source,
        "added_at": it.added_at.isoformat(),
        "tags": list(it.tags),
        "home": "" if it.workspace in ("", "global") else it.workspace,
    }


MAX_RANGE_DAYS = 31


@dataclass(frozen=True)
class TimelineQuery:
    view: str  # calendar | recent | unknown
    month: dt.date  # 该月 1 号
    day: dt.date  # 日历选中日 / 区间止日
    start: dt.date | None = None  # 闭区间起;与 end 同时有值则为区间
    end: dt.date | None = None


def range_day_count(start: dt.date, end: dt.date) -> int:
    return (end - start).days + 1


def format_range_label(start: dt.date, end: dt.date, *, zh: bool) -> str:
    if zh:
        return f"{start.month}月{start.day}日 – {end.month}月{end.day}日"
    return f"{start.strftime('%b')} {start.day} – {end.strftime('%b')} {end.day}"


def filter_range(
    items: list[TimelineItem], start: dt.date, end: dt.date
) -> list[TimelineItem]:
    """发生日落在闭区间内;未知发生日排除。"""
    out = [
        it
        for it in items
        if it.occurred_at is not None and start <= it.occurred_at <= end
    ]
    out.sort(key=lambda it: (it.occurred_at or dt.date.min, it.workspace, it.id))
    return out


def cell_href(q: TimelineQuery, d: dt.date) -> str:
    """两下点:已有单日再点另一天 → 区间;已是区间再点 → 新单日。"""
    if q.start is not None and q.end is not None and q.start != q.end:
        return f"/timeline?day={d.isoformat()}"
    cur = q.day
    if d == cur:
        return f"/timeline?day={d.isoformat()}"
    a, b = (cur, d) if cur <= d else (d, cur)
    return f"/timeline?from={a.isoformat()}&to={b.isoformat()}"


def week_bounds(d: dt.date) -> tuple[dt.date, dt.date]:
    """含 d 的周一..周日(与 month_cells 周首一致)。"""
    mon = d - dt.timedelta(days=d.weekday())
    return mon, mon + dt.timedelta(days=6)


def resolve_timeline_query(
    *,
    month: str | None = None,
    day: str | None = None,
    mode: str | None = None,
    unknown: str | None = None,
    start: str | None = None,
    end: str | None = None,
    today: dt.date | None = None,
) -> TimelineQuery:
    """Web GET /timeline 查询契约。非法或互斥 → TimelineQueryError。"""
    today = today or dt.date.today()
    unknown_on = unknown in ("1", "true", "yes")
    mode = (mode or "").strip() or None
    start_s = (start or "").strip() or None
    end_s = (end or "").strip() or None
    if start_s or end_s:
        if mode == "recent" or unknown_on:
            raise TimelineQueryError("range is exclusive")
        if not start_s or not end_s:
            raise TimelineQueryError("range needs from and to")
        a = parse_calendar_date(start_s)
        b = parse_calendar_date(end_s)
        if a is None or b is None:
            raise TimelineQueryError("illegal day")
        if a > b:
            a, b = b, a
        return TimelineQuery(
            view="calendar",
            month=dt.date(b.year, b.month, 1),
            day=b,
            start=a,
            end=b,
        )
    if mode == "day":
        mode = None
    if mode not in (None, "recent"):
        raise TimelineQueryError("illegal mode")
    if mode == "recent":
        if day or month or unknown_on:
            raise TimelineQueryError("recent is exclusive")
        return TimelineQuery(view="recent", month=_month_start(today), day=today)
    if unknown_on:
        if day or mode == "recent":
            raise TimelineQueryError("unknown is exclusive")
        m = _parse_month(month, today)
        return TimelineQuery(view="unknown", month=m, day=_clamp_dom(today, m))
    d = parse_calendar_date(day) if day else None
    if day and d is None:
        raise TimelineQueryError("illegal day")
    if month:
        m = _parse_month(month, None)
        if d is not None and (d.year, d.month) != (m.year, m.month):
            raise TimelineQueryError("month/day mismatch")
        if d is None:
            d = _clamp_dom(today, m)
        return TimelineQuery(view="calendar", month=m, day=d)
    if d is not None:
        return TimelineQuery(
            view="calendar", month=dt.date(d.year, d.month, 1), day=d
        )
    m = _month_start(today)
    return TimelineQuery(view="calendar", month=m, day=today)


def _month_start(d: dt.date) -> dt.date:
    return dt.date(d.year, d.month, 1)


def _parse_month(month: str | None, today: dt.date | None) -> dt.date:
    if not month:
        assert today is not None
        return _month_start(today)
    m = _MONTH.fullmatch(month.strip())
    if not m:
        raise TimelineQueryError("illegal month")
    y, mo = int(m.group(1)), int(m.group(2))
    if mo < 1 or mo > 12:
        raise TimelineQueryError("illegal month")
    return dt.date(y, mo, 1)


def _clamp_dom(today: dt.date, month: dt.date) -> dt.date:
    last = calendar.monthrange(month.year, month.month)[1]
    return dt.date(month.year, month.month, min(today.day, last))


def month_cells(year: int, month: int) -> list[dt.date]:
    first = dt.date(year, month, 1)
    start = first - dt.timedelta(days=first.weekday())
    return [start + dt.timedelta(days=i) for i in range(42)]


def shift_month_day(day: dt.date, delta_months: int) -> dt.date:
    """‹ › 换月：保留日号，钳到月末。"""
    y = day.year
    m = day.month + delta_months
    while m < 1:
        m += 12
        y -= 1
    while m > 12:
        m -= 12
        y += 1
    last = calendar.monthrange(y, m)[1]
    return dt.date(y, m, min(day.day, last))


def format_cli_timeline(
    items: list[TimelineItem], *, recent: bool = False, day: dt.date | None = None
) -> str:
    if day is not None:
        items = [it for it in items if it.occurred_at == day]
    if recent:
        ordered = sorted(items, key=lambda it: it.added_at, reverse=True)
        lines: list[str] = []
        groups: dict[str, list[TimelineItem]] = {}
        order: list[str] = []
        for it in ordered:
            local = it.added_at.astimezone()
            key = local.date().isoformat()
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(it)
        for key in order:
            lines.append(key)
            for it in groups[key]:
                lines.append(_cli_row(it))
        return "\n".join(lines) + ("\n" if lines else "")
    unknown = [it for it in items if it.occurred_at is None]
    dated = [it for it in items if it.occurred_at is not None]
    dated.sort(key=lambda it: it.occurred_at or dt.date.min, reverse=True)
    lines = []
    if unknown:
        lines.append("⚠ 发生时间未知")
        for it in unknown:
            lines.append(_cli_row(it))
    current: dt.date | None = None
    for it in dated:
        if it.occurred_at != current:
            current = it.occurred_at
            lines.append(current.isoformat())
        lines.append(_cli_row(it))
    return "\n".join(lines) + ("\n" if lines else "")


def _cli_row(it: TimelineItem) -> str:
    return f"  {it.workspace}  {it.title}  {it.id}"
