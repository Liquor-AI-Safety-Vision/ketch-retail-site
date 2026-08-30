"""Zone geometry is stored as fractions of the frame, never as pixels.

Two formats used to coexist. `zone_norm` files (storage, entrance) were already
fractional; the divider/checkout files stored absolute pixels next to the
`width`/`height` they were drawn at, and `load_divider` rescaled -- but only if
the caller passed `size`. stream_track's own CLI did not, so it placed a
1920x1080 divider on whatever it was given, and the failure mode was staff and
customer roles landing on the wrong side of the counter with no error at all.

Fractions remove the failure mode instead of documenting it: there is no
resolution in the file to disagree with the frame, and no rescale step to
forget. `drawn_on` survives as provenance and is asserted here to be unused.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
ZONES = BACKEND / "config" / "zones"


def _zone_files() -> list[Path]:
    return sorted(ZONES.glob("*.json"))


def test_no_zone_file_stores_absolute_pixels():
    bad = []
    for f in _zone_files():
        d = json.loads(f.read_text())
        for key in ("width", "height"):
            if key in d:
                bad.append(f"{f.name}: top-level {key!r} -- move it under drawn_on")
        for z in d.get("zones", []):
            if "points" in z:
                bad.append(f"{f.name}/{z.get('name')}: has 'points' (pixels)")
            if "points_norm" not in z:
                bad.append(f"{f.name}/{z.get('name')}: missing 'points_norm'")
    assert not bad, "zone files still carry pixels:\n  " + "\n  ".join(bad)


def test_every_coordinate_is_a_fraction():
    """Catches a file converted with the wrong divisor, which stays plausible."""
    bad = []
    for f in _zone_files():
        d = json.loads(f.read_text())
        groups = [(z.get("name"), z["points_norm"]) for z in d.get("zones", [])]
        groups += [(k, v["zone_norm"]) for k, v in d.items()
                   if isinstance(v, dict) and "zone_norm" in v]
        for name, pts in groups:
            for x, y in pts:
                if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                    bad.append(f"{f.name}/{name}: ({x}, {y}) outside [0, 1]")
    assert not bad, "zone coordinates are not fractions:\n  " + "\n  ".join(bad)


def test_loading_a_divider_without_a_frame_size_is_refused():
    """The exact bug: no size meant no rescale, and no complaint either."""
    sys.path.insert(0, str(REPO))
    from backend.src.perception import zones

    path = ZONES / "top_side_view_zones.json"
    try:
        zones.load_divider(str(path))
    except ValueError as e:
        assert "frame size" in str(e)
    else:
        raise AssertionError("load_divider accepted a call with no frame size")


def test_a_divider_scales_with_the_frame_it_is_given():
    sys.path.insert(0, str(REPO))
    from backend.src.perception import zones

    path = str(ZONES / "top_side_view_zones.json")
    small, _ = zones.load_divider(path, size=(960, 540))
    large, _ = zones.load_divider(path, size=(1920, 1080))
    for (sx, sy), (lx, ly) in zip(small.p, large.p):
        assert abs(lx - 2 * sx) < 1e-3 and abs(ly - 2 * sy) < 1e-3


def test_drawn_on_is_provenance_and_nothing_reads_it():
    """If any loader started scaling by it again, the old coupling is back."""
    readers = [BACKEND / "src/perception/zones.py",
               BACKEND / "src/perception/problem2_fsm.py"]
    bad = []
    for f in readers:
        for node in ast.walk(ast.parse(f.read_text())):
            if (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
                    and node.slice.value in {"width", "height", "drawn_on"}):
                bad.append(f"{f.relative_to(REPO)}:{node.lineno} reads "
                           f"{node.slice.value!r} out of a zone file")
    assert not bad, "a zone loader is scaling by the drawn resolution again:\n  " + \
        "\n  ".join(bad)
