"""Warehouse zone state machine and entry/exit events (doc 02, section 5).

Pure logic: no video, no drawing, no clock. Feed it tracks per frame, get
events out. That separation is what doc 02 section 3.1 asks for - perception is
shared across problems, the business rules are not.

POC scope, per instruction: no allowed windows, so every confirmed entry is a
violation. Cooldown and the high-water rule (doc section 7) are Phase 2 and are
deliberately absent - raw events are what you want while judging the state
machine itself, because suppression hides both the noise and its cause.
"""
from dataclasses import dataclass, field

import cv2
import numpy as np

from backend.src.perception import zones
OUTSIDE, PENDING_IN, INSIDE = "OUTSIDE", "PENDING_IN", "INSIDE"


@dataclass
class Config:
    """POC values, fixed by hand rather than tuned: the two clips available are
    not general enough to fit thresholds against, and doing so would produce
    numbers that look measured but are not."""
    # 0.45, not the 0.35 used at the counter: a bottle on a shelf in storage_1
    # scored 0.426 as a person and produced a ZONE_ENTRY at t=62.1s.
    person_conf: float = 0.45
    entry_confirmation_s: float = 1.0
    # Deliberately longer than entry, per doc 02 appendix A. Measured on
    # storage_1: a person standing in the doorway crossed the boundary back and
    # forth, producing 3 entries in 15 s; the gaps between leaving and returning
    # were 1.7 s and 3.1 s, so anything under ~4 s still lets that through.
    exit_confirmation_s: float = 4.0
    presence_confirmation_frames: int = 5

    # Consecutive frames seen OUTSIDE the zone that make a track's disappearance
    # count as an exit rather than an unknown. Needed because exit_confirmation
    # is longer than the tracker's max_age (3 s, and only 1 s near the image
    # border): someone who walks out through a door at the frame edge stops
    # being detected long before 4 s elapse, so a confirmed ZONE_EXIT could
    # never fire for the most ordinary way of leaving a room.
    exit_on_loss_min_frames: int = 3

    # Fallback only. The real value belongs in the per-camera config beside
    # zone_norm, because it is a property of the lens and the mounting, not of
    # the system: expressing it as a fraction survives a change of resolution
    # but says nothing about how large a person looks in THIS room. A camera
    # covering a deep aisle renders people far smaller than one over a corner.
    # Measured on storage_1: objects mis-detected as people topped out at
    # 0.108H, the smallest real track reached 0.206H, so anything in between
    # separates them cleanly; 0.14 sits near the middle of that gap.
    min_box_h_frac: float = 0.14

    # Which pixel represents a person. 'pelvis' is measured better under the
    # waist-height occlusion this footage is full of - doc 02 section 4 says
    # bottom-center, chosen before any warehouse footage existed.
    anchor: str = "pelvis"

    fps: float = 10.0
    entry_frames: int = field(init=False)
    exit_frames: int = field(init=False)

    def __post_init__(self):
        self.entry_frames = max(1, round(self.entry_confirmation_s * self.fps))
        self.exit_frames = max(1, round(self.exit_confirmation_s * self.fps))


#: The anchor modes ZoneMonitor actually distinguishes. It tests
#: `cfg.anchor == "bottom_center"` and falls through to the keypoint path for
#: everything else, so an unrecognised string does not fail -- it selects the
#: other mode. Named here so a manifest value can be checked against the real
#: set instead of being trusted.
ANCHOR_MODES = frozenset({"pelvis", "bottom_center"})


def anchor_point(ltrb, kpts, mode):
    """One-shot anchor, for callers with no per-track state (the presence scan)."""
    if mode == "bottom_center":
        return zones.point_from_frac(ltrb, zones.BOTTOM_CENTRE_FRAC), "box"
    frac, src = zones.anchor_frac(ltrb, kpts)
    if frac is None:
        return zones.point_from_frac(ltrb, zones.BOX_CENTRE_FRAC), "box"
    return zones.point_from_frac(ltrb, frac), src


class ZoneMonitor:
    """One warehouse zone on one camera."""

    def __init__(self, zone_norm, W, H, cfg=None):
        self.cfg = cfg or Config()
        self.poly = (np.asarray(zone_norm, np.float32) * [W, H]).astype(np.float32)
        self.state = {}          # track_id -> OUTSIDE / PENDING_IN / INSIDE
        self.streak = {}         # track_id -> consecutive frames in current bid
        self.first_seen = {}     # track_id -> frame it appeared
        self.frac = {}           # track_id -> pelvis as a fraction of its box
        self.events = []
        self.anchor_src = {"pelvis": 0, "shoulder": 0, "box": 0}
        self.coasted = 0         # anchors taken from a remembered fraction

    def inside(self, pt):
        return cv2.pointPolygonTest(self.poly, (float(pt[0]), float(pt[1])),
                                    False) >= 0

    def n_inside(self):
        return sum(1 for s in self.state.values() if s == INSIDE)

    def _emit(self, kind, tid, frame, reason):
        e = {"frame": frame, "t": round(frame / self.cfg.fps, 2), "event": kind,
             "track_id": tid, "persons_in_zone": self.n_inside(),
             "reason_codes": reason}
        self.events.append(e)
        return e

    def update(self, tracks, frame_idx):
        """tracks: list of (track_id, ltrb, kpts, detected).

        `detected` says whether this frame's box came from a detection or from
        the Kalman filter. It matters: keypoints are only refreshed on a match,
        so on a coasting frame the stored fraction is the only trustworthy way
        to place the anchor inside the predicted box.
        """
        cfg, out = self.cfg, []
        live = set()

        for tid, ltrb, kpts, detected in tracks:
            live.add(tid)
            if cfg.anchor == "bottom_center":
                frac, src = zones.BOTTOM_CENTRE_FRAC, "box"
            else:
                frac, src = (zones.anchor_frac(ltrb, kpts) if detected
                             else (None, None))
                if frac is not None:
                    self.frac[tid] = frac          # fresh measurement, remember it
                else:
                    frac = self.frac.get(tid)
                    if frac is None:
                        frac, src = zones.BOX_CENTRE_FRAC, "box"
                    else:
                        src = "pelvis"
                        self.coasted += 1
            pt = zones.point_from_frac(ltrb, frac)
            self.anchor_src[src] = self.anchor_src.get(src, 0) + 1
            ins = self.inside(pt)
            st = self.state.get(tid)

            if st is None:
                self.first_seen[tid] = frame_idx
                # doc section 5: a track that appears already inside the zone
                # never produces an OUTSIDE->INSIDE transition. Detector misses
                # at the door, entry from a blind corner and restarts all look
                # like this, so it has to count as an entry in its own right.
                st = PENDING_IN if ins else OUTSIDE
                self.streak[tid] = 1 if ins else 0

            elif st == OUTSIDE:
                if ins:
                    st, self.streak[tid] = PENDING_IN, 1
            elif st == PENDING_IN:
                if ins:
                    self.streak[tid] += 1
                    if self.streak[tid] >= cfg.entry_frames:
                        st = INSIDE
                        self.state[tid] = INSIDE       # count self in n_inside
                        born = self.first_seen[tid] + cfg.entry_frames > frame_idx + 1
                        self._emit("ZONE_ENTRY", tid, frame_idx,
                                   ["BORN_INSIDE_ZONE"] if born
                                   else ["ZONE_ENTRY_CONFIRMED"])
                else:
                    st, self.streak[tid] = OUTSIDE, 0
            elif st == INSIDE:
                if ins:
                    self.streak[tid] = 0
                else:
                    self.streak[tid] += 1
                    if self.streak[tid] >= cfg.exit_frames:
                        st, self.streak[tid] = OUTSIDE, 0
                        self.state[tid] = OUTSIDE
                        self._emit("ZONE_EXIT", tid, frame_idx,
                                   ["ZONE_EXIT_CONFIRMED"])

            self.state[tid] = st
            out.append((tid, ltrb, kpts, st, pt, self.streak.get(tid, 0)))

        return out

    def drop(self, track_ids, frame_idx):
        """A removed track stops occupying the zone; it cannot sit INSIDE forever.

        This is NOT an exit. Losing a track means the tracker stopped seeing the
        person - behind a pallet, past max_age - and they are most likely still
        standing there. Emitting ZONE_EXIT would assert something we did not
        observe. But dropping it silently leaves the log with more entries than
        exits and no way to tell why, so it is recorded as its own event and
        deliberately carries no alert weight.
        """
        for tid in track_ids:
            was_inside = self.state.get(tid) == INSIDE
            # in the INSIDE state this counts consecutive frames seen OUTSIDE,
            # i.e. how far along the exit confirmation the track had got
            outside_for = self.streak.get(tid, 0)
            self.state.pop(tid, None)
            self.streak.pop(tid, None)
            self.first_seen.pop(tid, None)
            self.frac.pop(tid, None)
            if not was_inside:
                continue
            if outside_for >= self.cfg.exit_on_loss_min_frames:
                # observed leaving, then lost - most likely walked out of view.
                # Marked as inferred so a downstream consumer can tell it apart
                # from an exit we watched complete.
                self.state.pop(tid, None)
                self._emit("ZONE_EXIT", tid, frame_idx,
                           ["EXIT_INFERRED_FROM_TRACK_LOSS",
                            f"OUTSIDE_FOR_{outside_for}F"])
            else:
                self._emit("TRACK_LOST_INSIDE", tid, frame_idx,
                           ["TRACK_REMOVED_WHILE_INSIDE"])

    def presence(self, tracks_by_frame):
        """Scheduled scan (doc section 6): people confirmed over K frames.

        Takes a window of frames, not one, because a single-frame scan is a coin
        flip - sampling this footage showed many frames with nobody detected
        while people were plainly in the room.
        """
        hits = sum(1 for tracks in tracks_by_frame
                   if any(self.inside(anchor_point(t[1], t[2], self.cfg.anchor)[0])
                          for t in tracks))
        return hits >= self.cfg.presence_confirmation_frames, hits
