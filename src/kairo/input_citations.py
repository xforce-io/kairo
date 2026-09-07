"""Locations refer exclusively to the immutable input's original text lines."""
import re

_RANGE = re.compile(r'L([1-9][0-9]{0,8})(?:-L([1-9][0-9]{0,8}))?\Z')


def line_range(location: str, count: int) -> tuple[int, int] | None:
    match = _RANGE.fullmatch(location)
    if not match:
        return None
    start, end = int(match[1]), int(match[2] or match[1])
    return (start, end) if 1 <= start <= end <= count else None


def numbered_content(content: str) -> str:
    return '\n'.join(f'{i}: {line}' for i, line in enumerate(content.splitlines(), 1))
