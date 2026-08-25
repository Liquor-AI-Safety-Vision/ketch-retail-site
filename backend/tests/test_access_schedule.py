"""A shift that crosses midnight is one window, not two half-broken ones.

This exists because the same function has now been wrong twice, in two
different ways, and neither was caught by reading it:

  originally  `start <= minute <= end` can never be true for 22:00-06:00
              (1320 <= m <= 360 has no solution), so every hour of a night
              shift read as outside the allowed window and raised an
              intrusion alert against staff who were rostered on.

  then        the wrap was handled but `day_name in days` was still checked
              first, so Friday's 22:00-06:00 window accepted Friday 23:00 and
              rejected Saturday 02:00 -- the same shift, half of it alerting.
              The fix carried a comment claiming it handled exactly this.

The second bug survived a hand-written check that looked right: it asserted
Friday 02:00 was allowed, which the buggy code did return. But Friday 02:00
belongs to *Thursday's* shift. The assertion agreed with the implementation
rather than with the roster, which is the failure mode a test is supposed to
prevent.

So these cases are written from the roster: a Friday-night shift covers
Friday evening and Saturday morning, and nothing else.

    python backend/tools/run_tests_nopytest.py backend/tests/test_access_schedule.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _in_allowed_window(*args, **kwargs):
    """Imported lazily: storage_intrusion pulls in cv2/numpy at module level,
    and this function needs none of it."""
    from backend.src.pipelines.storage_intrusion import _in_allowed_window as fn

    return fn(*args, **kwargs)


# 2026-08-28 is a Friday, so the following day is a Saturday.
FRI = datetime(2026, 8, 28)
SAT = datetime(2026, 8, 29)
THU = datetime(2026, 8, 27)

NIGHT = {"allowed_windows": [{"days": ["fri"], "start": "22:00", "end": "06:00"}]}
DAY = {"allowed_windows": [{"days": ["fri"], "start": "08:00", "end": "17:00"}]}
EVERY_NIGHT = {"allowed_windows": [
    {"days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
     "start": "22:00", "end": "06:00"}]}


def _at(day: datetime, hh: int, mm: int = 0) -> datetime:
    return day.replace(hour=hh, minute=mm)


def test_night_shift_covers_its_own_evening():
    assert _in_allowed_window(_at(FRI, 22, 0), NIGHT)
    assert _in_allowed_window(_at(FRI, 23, 30), NIGHT)


def test_night_shift_covers_the_next_mornings_small_hours():
    """The half that used to alert against rostered staff."""
    assert _in_allowed_window(_at(SAT, 0, 30), NIGHT)
    assert _in_allowed_window(_at(SAT, 2, 0), NIGHT)
    assert _in_allowed_window(_at(SAT, 6, 0), NIGHT)


def test_night_shift_stops_when_it_ends():
    assert not _in_allowed_window(_at(SAT, 6, 1), NIGHT)
    assert not _in_allowed_window(_at(SAT, 12, 0), NIGHT)
    assert not _in_allowed_window(_at(FRI, 21, 59), NIGHT)


def test_friday_small_hours_belong_to_thursdays_shift_not_fridays():
    """The case the earlier hand-check got backwards.

    02:00 on Friday is the tail of a Thursday-night shift. A window listed
    only under Friday must not cover it -- and if Thursday is listed, it must.
    """
    assert not _in_allowed_window(_at(FRI, 2, 0), NIGHT)
    thursday_night = {"allowed_windows": [
        {"days": ["thu"], "start": "22:00", "end": "06:00"}]}
    assert _in_allowed_window(_at(FRI, 2, 0), thursday_night)
    assert _in_allowed_window(_at(THU, 23, 0), thursday_night)


def test_ordinary_daytime_window_is_unaffected():
    assert _in_allowed_window(_at(FRI, 12, 0), DAY)
    assert not _in_allowed_window(_at(FRI, 19, 0), DAY)
    assert not _in_allowed_window(_at(FRI, 7, 59), DAY)
    assert not _in_allowed_window(_at(SAT, 12, 0), DAY)


def test_every_night_window_has_no_gap_across_midnight():
    """Rostered every night, so there should be no minute in the small hours
    that reads as an intrusion."""
    for hh in (22, 23, 0, 1, 2, 3, 4, 5):
        day = FRI if hh >= 22 else SAT
        assert _in_allowed_window(_at(day, hh, 0), EVERY_NIGHT), f"{hh:02d}:00 rejected"


def test_no_windows_configured_allows_nothing():
    assert not _in_allowed_window(_at(FRI, 12, 0), {"allowed_windows": []})
    assert not _in_allowed_window(_at(FRI, 12, 0), {})


def test_missing_timestamp_is_not_allowed():
    assert not _in_allowed_window(None, NIGHT)
