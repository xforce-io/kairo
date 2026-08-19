"""#122 听读消费契约:行级时间前缀、配对、搜索命中。"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from kairo.models import Form

_PREFIX = re.compile(
    r"^[ \t]*\[(?:"
    r"(?P<h>\d{1,3}):(?P<hmm>[0-5]\d):(?P<hss>[0-5]\d)(?:\.(?P<hf>\d{1,3}))?"
    r"|"
    r"(?P<m>\d{1,3}):(?P<mss>[0-5]\d)(?:\.(?P<mf>\d{1,3}))?"
    r")\](?P<body>.*)$"
)


@dataclass(frozen=True)
class Unit:
    start: float | None
    end: float | None
    text: str
    zero: bool = False


@dataclass(frozen=True)
class Hit:
    text: str
    start: float | None
    unit: Unit


@dataclass(frozen=True)
class Pair:
    audio: Form
    transcript: Form | None
    linked: bool


def _frac(digits: str | None) -> float:
    if not digits:
        return 0.0
    return int(digits) / (10 ** len(digits))


def _line_start(line: str) -> float | None:
    m = _PREFIX.match(line)
    if not m:
        return None
    if m.group("h") is not None:
        return (
            int(m.group("h")) * 3600
            + int(m.group("hmm")) * 60
            + int(m.group("hss"))
            + _frac(m.group("hf"))
        )
    return int(m.group("m")) * 60 + int(m.group("mss")) + _frac(m.group("mf"))


def _line_body(line: str) -> str | None:
    m = _PREFIX.match(line)
    return m.group("body").lstrip(" ") if m else None


def parse_units(text: str, duration: float | None = None) -> list[Unit]:
    """把 transcript 收成单元。duration 为 None 时不过滤超时长前缀。"""
    leading: list[str] = []
    accepted: list[tuple[float, list[str]]] = []
    last: float | None = None
    for line in text.splitlines():
        start = _line_start(line)
        body = _line_body(line) if start is not None else line
        if start is None or (last is not None and start < last):
            if accepted:
                accepted[-1][1].append(body if body is not None else line)
            else:
                leading.append(line if start is None else (body or line))
            continue
        accepted.append((start, [body or ""]))
        last = start

    units: list[Unit] = []
    if leading:
        units.append(Unit(start=None, end=None, text="\n".join(leading)))
    for i, (start, lines) in enumerate(accepted):
        end = accepted[i + 1][0] if i + 1 < len(accepted) else duration
        units.append(
            Unit(
                start=start,
                end=end,
                text="\n".join(lines),
                zero=end is not None and end == start,
            )
        )
    if duration is not None:
        return apply_duration(units, duration)
    return units


def apply_duration(units: Iterable[Unit], duration: float) -> list[Unit]:
    """按真实音频时长裁掉起点越界的前缀，并重算半开区间终点。"""
    out: list[Unit] = []
    for u in units:
        if u.start is None:
            out.append(u)
            continue
        if u.start >= duration:
            if out:
                prev = out[-1]
                out[-1] = Unit(prev.start, prev.end, f"{prev.text}\n{u.text}", prev.zero)
            else:
                out.append(Unit(None, None, u.text))
            continue
        out.append(u)

    timed_idx = [i for i, u in enumerate(out) if u.start is not None]
    for n, i in enumerate(timed_idx):
        u = out[i]
        end = out[timed_idx[n + 1]].start if n + 1 < len(timed_idx) else duration
        out[i] = Unit(u.start, end, u.text, end == u.start)
    return out


def unit_at(units: Iterable[Unit], t: float) -> Unit | None:
    """半开区间归属。零时长与无时间区不高亮。"""
    hit = None
    for u in units:
        if u.start is None or u.end is None or u.zero:
            continue
        if u.start <= t < u.end:
            hit = u
    return hit


def search_hits(units: Iterable[Unit], query: str) -> list[Hit]:
    if not query:
        return []
    q = query.casefold()
    out: list[Hit] = []
    for u in units:
        if q in u.text.casefold():
            out.append(Hit(text=u.text, start=u.start, unit=u))
    return out


def pair_audio_transcripts(forms: Iterable[Form]) -> list[Pair]:
    audios = [f for f in forms if f.role == "audio"]
    transcripts = [f for f in forms if f.role == "transcript"]
    claimed: dict[int, list[Form]] = defaultdict(list)
    audio_by_id = {id(a): a for a in audios}

    for t in transcripts:
        if not t.origin.startswith("asr-from:"):
            continue
        want = t.origin.split(":", 1)[1]
        matches = [a for a in audios if a.hash == want]
        if len(matches) != 1:
            continue
        claimed[id(matches[0])].append(t)

    used_a: set[int] = set()
    used_t: set[int] = set()
    pairs: list[Pair] = []
    for aid, ts in claimed.items():
        if len(ts) != 1:
            continue
        pairs.append(Pair(audio=audio_by_id[aid], transcript=ts[0], linked=True))
        used_a.add(aid)
        used_t.add(id(ts[0]))

    rest_a = [a for a in audios if id(a) not in used_a]
    rest_t = [t for t in transcripts if id(t) not in used_t]
    if len(rest_a) == 1 and len(rest_t) == 1:
        pairs.append(Pair(audio=rest_a[0], transcript=rest_t[0], linked=True))
        used_a.add(id(rest_a[0]))
        rest_a = []

    for a in rest_a:
        pairs.append(Pair(audio=a, transcript=None, linked=False))
    return pairs
