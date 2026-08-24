"""Assign a staff/customer role to each track from a drawn zone.

Three layers, because a single frame is not enough to decide what someone is:

  1. anchor   which pixel represents a person standing on the floor
  2. side     which side of the divider that pixel is on, with a dead band
  3. vote     accumulate over the track's life - a role belongs to an identity,
              not to a frame

Layer 3 is what makes this work. Staff step out from behind the counter; a
customer leans across it. Either produces frames on the wrong side, and neither
should change what the person is. Only a sustained majority does.
"""
import json

import numpy as np

# keypoint indices (COCO-17)
K_SHOULDER = (5, 6)
K_HIP = (11, 12)
KPT_VIS = 0.5

# Within this of the line, cast no vote. Scaled by the person's box height,
# because the same pixel error means more floor for someone far from the camera.
DEAD_BAND_FRAC = 0.15
DEAD_BAND_MIN_PX = 20

VOTE_CAP = 50          # +-5 s at 10 fps; keeps the label able to change
VOTE_MIN = 5           # below this the role is still "?"

STAFF, CUSTOMER, UNKNOWN = "staff", "customer", "?"


class Divider:
    """An open polyline that cuts the frame in two.

    A 2-point line is extended infinitely, so it never matters whether the user
    clicked all the way to the image border. With more points, each person is
    scored against the nearest segment, which lets the line follow a counter
    that the camera's barrel distortion has bent.
    """

    def __init__(self, points, staff_ref):
        """staff_ref is a pixel known to be on the staff side.

        Not a sign: the sign of the cross product flips with the order the two
        endpoints happened to be clicked, so a hardcoded +1/-1 would silently
        invert every label when the zone is redrawn. A reference pixel does not.
        """
        self.p = np.asarray(points, np.float32)
        if len(self.p) < 2:
            raise ValueError("a divider needs at least 2 points")
        self.a = self.p[:-1]
        self.d = self.p[1:] - self.p[:-1]
        self.len = np.linalg.norm(self.d, axis=1)
        self.staff_ref = np.asarray(staff_ref, np.float32)
        self.staff_side = np.sign(self.signed_distance(self.staff_ref)) or 1.0

    def signed_distance(self, pt):
        """Signed pixel distance to the polyline; sign is the side."""
        v = np.asarray(pt, np.float32) - self.a
        cross = v[:, 0] * self.d[:, 1] - v[:, 1] * self.d[:, 0]
        dist = cross / self.len
        # nearest segment decides, but with the sign of that segment's side
        return float(dist[np.abs(dist).argmin()])

    def side(self, pt, box_h=None):
        """STAFF / CUSTOMER / UNKNOWN, the last inside the dead band."""
        s = self.signed_distance(pt)
        band = DEAD_BAND_MIN_PX if box_h is None else max(
            DEAD_BAND_MIN_PX, DEAD_BAND_FRAC * box_h)
        if abs(s) < band:
            return UNKNOWN
        return STAFF if np.sign(s) == self.staff_side else CUSTOMER


def load_divider(path, name=None, flip=False, size=None):
    """Read a zone_editor JSON and return its line divider.

    Staff defaults to the bottom-left corner's side of the line - for this
    camera, the corridor behind the counter that the POS terminals face into.
    `flip` moves it to the top-right corner's side instead.

    `size` is the (W, H) of the frames this divider will be used on. The file
    stores absolute pixels, so a divider drawn on 1920x1080 lands in the wrong
    place on any other resolution - silently, and roles would be wrong with no
    error to show for it. Passing the frame size rescales it. Note this only
    survives a change of resolution, not a change of viewpoint: the same scene
    reframed needs the zone drawn again.
    """
    data = json.load(open(path))
    W, H = data["width"], data["height"]
    sx, sy = (1.0, 1.0) if size is None else (size[0] / W, size[1] / H)
    if (sx, sy) != (1.0, 1.0):
        print(f"  divider drawn for {W}x{H}, scaling by "
              f"({sx:.3f}, {sy:.3f}) for {size[0]}x{size[1]}")
        W, H = size
    ref = (W - 1.0, 0.0) if flip else (0.0, H - 1.0)
    lines = [z for z in data["zones"] if z.get("type") == "line"]
    if name:
        lines = [z for z in lines if z["name"] == name]
    if not lines:
        raise SystemExit(f"{path} has no line zone"
                         + (f" named {name!r}" if name else ""))
    if len(lines) > 1:
        print(f"  {len(lines)} line zones present, using {lines[0]['name']!r}")
    skipped = [z["name"] for z in data["zones"] if z.get("type") != "line"]
    if skipped:
        print(f"  ignoring non-line zones: {', '.join(skipped)}")
    pts = [[x * sx, y * sy] for x, y in lines[0]["points"]]
    return Divider(pts, ref), lines[0]["name"]


def anchor(ltrb, kpts):
    """The pelvis: the one landmark this camera almost always sees.

    Deliberately NOT the feet. Behind a counter the legs are gone - measured on
    this footage, one person had ankles visible in 0 of 97 frames - and the
    obvious repair, extrapolating hip + k*(hip - shoulder) down to the floor,
    is what produced the staff/customer errors. It failed three ways:

      - unbounded: it landed outside the person's own box in 45% of frames, by
        up to 178 px on a 357 px-tall box, which is how it crossed the divider
      - the ratio k is anthropometry, not measurement, and the true value
        varies across the frame as perspective compresses distant people
      - worst, it switched anchors mid-track. Ankles and pelvis sit ~140 px
        apart in this scene, so every occlusion jumped the measurement by that
        much. A bias that toggles is worse than one that is always present.

    So: one landmark, always, never extrapolated. The divider is then a
    pelvis-height boundary rather than a floor boundary - which is also what a
    person naturally draws when they trace where bodies cross a counter.

    Returns (point, source).
    """
    pt, src = None, None
    if kpts is not None:
        vis = kpts[:, 2] > KPT_VIS
        hip = [i for i in K_HIP if vis[i]]
        if hip:
            pt, src = kpts[hip, :2].mean(axis=0), "pelvis"
        else:
            sho = [i for i in K_SHOULDER if vis[i]]
            if sho:
                pt, src = kpts[sho, :2].mean(axis=0), "shoulder"
    if pt is None:
        pt = np.array([(ltrb[0] + ltrb[2]) / 2, (ltrb[1] + ltrb[3]) / 2])
        src = "box"
    # Invariant: an anchor outside this person's own box says nothing about
    # this person. Cheap, and it alone would have caught the bug above.
    return np.array([np.clip(pt[0], ltrb[0], ltrb[2]),
                     np.clip(pt[1], ltrb[1], ltrb[3])], np.float32), src


BOX_CENTRE_FRAC = (0.5, 0.5)
BOTTOM_CENTRE_FRAC = (0.5, 1.0)


def anchor_frac(ltrb, kpts):
    """The pelvis expressed as a fraction of the box it was measured in.

    A fraction rather than a pixel, because the two halves of a track are not
    updated together while it coasts: the Kalman filter moves `ltrb` every
    frame, but `kpts` keeps whatever the last matched detection carried, so an
    absolute pelvis is stale by however long the track has been unmatched. Clip
    it into the current box and it ends up pinned to the trailing edge - always
    biased against the direction of travel, which for a zone test means exits
    are reported late. A fraction moves with the box for free.

    Returns ((fx, fy), source) or (None, None) if nothing usable is visible.
    """
    if kpts is None:
        return None, None
    w = max(1e-6, float(ltrb[2] - ltrb[0]))
    h = max(1e-6, float(ltrb[3] - ltrb[1]))
    vis = kpts[:, 2] > KPT_VIS
    for idxs, name in ((K_HIP, "pelvis"), (K_SHOULDER, "shoulder")):
        sel = [i for i in idxs if vis[i]]
        if sel:
            p = kpts[sel, :2].mean(axis=0)
            return (float(np.clip((p[0] - ltrb[0]) / w, 0.0, 1.0)),
                    float(np.clip((p[1] - ltrb[1]) / h, 0.0, 1.0))), name
    return None, None


def point_from_frac(ltrb, frac):
    """Absolute anchor for this frame's box. Inside the box by construction."""
    return np.array([ltrb[0] + frac[0] * (ltrb[2] - ltrb[0]),
                     ltrb[1] + frac[1] * (ltrb[3] - ltrb[1])], np.float32)


class RoleVoter:
    """One running vote per track id. Roles persist; single frames do not."""

    def __init__(self, divider):
        self.div = divider
        self.score = {}
        self.anchor_src = {"pelvis": 0, "shoulder": 0, "box": 0}
        self.abstained = 0

    def update(self, track_id, ltrb, kpts):
        pt, src = anchor(ltrb, kpts)
        self.anchor_src[src] += 1
        s = self.div.side(pt, box_h=ltrb[3] - ltrb[1])
        if s == UNKNOWN:
            self.abstained += 1
        if s != UNKNOWN:
            v = self.score.get(track_id, 0) + (1 if s == STAFF else -1)
            self.score[track_id] = int(np.clip(v, -VOTE_CAP, VOTE_CAP))
        return self.role(track_id), pt

    def role(self, track_id):
        v = self.score.get(track_id, 0)
        if v >= VOTE_MIN:
            return STAFF
        if v <= -VOTE_MIN:
            return CUSTOMER
        return UNKNOWN

    def drop(self, track_id):
        self.score.pop(track_id, None)
