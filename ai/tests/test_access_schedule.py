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

    python ai/tools/run_tests_nopytest.py ai/tests/test_access_schedule.py
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
    from ai.src.pipelines.storage_intrusion import _in_allowed_window as fn

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


def test_the_zone_thresholds_are_reachable_from_a_manifest():
    """Entry was configurable; exit, presence and anchor were not.

    warehouse.Config's own comment sources exit_confirmation_s = 4.0 from one
    clip, where a person in a doorway crossed the boundary three times in
    15 s. Exit is the side that decides whether someone leaving is seen to
    leave at all -- a doorway at the frame edge, a busier aisle or a camera
    further back all move it, and none of those are properties of the system.
    """
    from ai.src.perception import warehouse

    cfg = warehouse.Config(fps=5.0)
    assert (cfg.exit_confirmation_s, cfg.presence_confirmation_frames) == (4.0, 5)
    assert cfg.exit_on_loss_min_frames == 3 and cfg.anchor == "pelvis"

    params = {
        "entry_confirm_seconds": 2.0, "exit_confirm_seconds": 8.0,
        "presence_confirm_frames": 9, "exit_on_loss_min_frames": 6,
        "anchor": "bottom_center",
    }
    tuned = _apply_zone_params(params, fps=5.0)
    assert tuned.entry_confirmation_s == 2.0 and tuned.exit_confirmation_s == 8.0
    assert tuned.presence_confirmation_frames == 9
    assert tuned.exit_on_loss_min_frames == 6
    assert tuned.anchor == "bottom_center"
    # __post_init__ has to run after BOTH windows are set, or the frame counts
    # still describe the defaults while the seconds say otherwise.
    assert tuned.entry_frames == 10 and tuned.exit_frames == 40


def test_an_unrecognised_anchor_is_refused_not_silently_swapped():
    """ZoneMonitor tests `cfg.anchor == "bottom_center"` and takes the keypoint
    path for anything else, so a typo does not fail -- it selects the other
    anchor and moves every zone crossing on that camera with nothing said."""
    try:
        _apply_zone_params({"anchor": "botom_center"}, fps=5.0)
    except ValueError as e:
        assert "botom_center" in str(e)
    else:
        raise AssertionError("an unrecognised anchor was accepted")


def _apply_zone_params(params, fps):
    """Run StorageIntrusionPipeline's zone-config block on its own.

    Building the real pipeline needs a manifest, zone file, alert store and
    media store; the block under test reads self.params and self.spec.
    """
    import types
    from ai.src.perception import warehouse
    from ai.src.pipelines.storage_intrusion import StorageIntrusionPipeline

    holder = types.SimpleNamespace(
        params=params,
        spec=types.SimpleNamespace(
            target_fps=fps, camera_id="storage_1",
            perception_profile={"person_detector": {"conf_thres": 0.45}}),
        zone_cfg={},
        timing=_NullTiming(),
        _business_ready=False,
    )
    captured = {}

    class _Monitor:
        def __init__(self, zone_norm, w, h, cfg):
            captured["cfg"] = cfg

    real_monitor, real_tracker = warehouse.ZoneMonitor, None
    warehouse.ZoneMonitor = _Monitor
    try:
        holder.zone_cfg = {"zone_norm": [[0, 0], [1, 0], [1, 1]]}
        StorageIntrusionPipeline._bootstrap_business(
            holder, types.SimpleNamespace(image_bgr=_Frame()))
    finally:
        warehouse.ZoneMonitor = real_monitor
    return captured["cfg"]


class _Frame:
    shape = (1080, 1920, 3)


class _NullTiming:
    def track(self, _name):
        import contextlib
        return contextlib.nullcontext()
