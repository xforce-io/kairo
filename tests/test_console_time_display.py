import os
import time
from datetime import date
from kairo.time_display import clock_label
from kairo.timeline import _dt_and_day, effective_occurred


def test_local_timezone_cross_day_and_date_only(monkeypatch):
    original = os.environ.get('TZ')
    try:
        monkeypatch.setenv('TZ', 'Asia/Shanghai')
        time.tzset()
        assert clock_label('2026-09-05T20:30:00Z') == '2026-09-06 04:30 UTC+08:00'
        assert clock_label('2026-09-05T16:30:00-04:00') == '2026-09-06 04:30 UTC+08:00'
        assert _dt_and_day('2026-09-05T20:30:00Z')[1] == date(2026, 9, 6)
        assert clock_label('2026-09-05') == '2026-09-05'
        assert clock_label('2026-09-05T20:30:00') == '2026-09-05 20:30'
        assert effective_occurred('x', '2026-09-05')[0] == date(2026, 9, 5)
        assert clock_label('bad') == 'bad'
    finally:
        if original is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = original
        time.tzset()
