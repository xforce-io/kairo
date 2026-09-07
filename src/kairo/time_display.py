"""Console dates use the service's local timezone without changing stored values."""
from datetime import datetime
import re


def clock_label(value: str | None) -> str:
    text = (value or '').strip()
    if not text or re.fullmatch(r'\d{4}-\d{2}-\d{2}', text):
        return text
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        return parsed.strftime('%Y-%m-%d %H:%M')
    local = parsed.astimezone()
    offset = local.strftime('%z')
    return local.strftime('%Y-%m-%d %H:%M') + f' UTC{offset[:3]}:{offset[3:]}'
