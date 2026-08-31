"""DeepSORT for person tracking, following the rules in uae-main/src/tracklib.

Identity model - there is no long-term re-identification, by design:

    TENTATIVE --N_INIT hits--> ACTIVE --miss--> LOST --past max age--> REMOVED
                                          <--matched again--

A REMOVED track is dropped and its id is never reused or recovered. Someone who
reappears after the LOST window is a new person. Everything is invested instead
in making that window strong, and every match is gated by POSITION before
appearance is consulted, so a detection can never be matched to a track on the
far side of the frame however similar the two embeddings are.

Matching runs in four passes, loosening as it goes (tracker._match):

    A  cascade, 0.98*embedding + 0.02*mahalanobis, chi-squared gate on the
       Kalman prediction                                   ACTIVE + LOST
    B  IoU, gated by embedding distance                    tracks lost <= 2 s
    C  IoU against the LAST OBSERVATION, not the Kalman prediction (OCSORT's
       Observation-Centric Recovery), skipping border tracks    remaining
    D  plain IoU                                           TENTATIVE

Ported from tracklib/association/deepsort/{track,tracker,cost,matching}.py.
All durations are declared in seconds and converted with FPS in Tracker.__init__,
so retargeting to another stream rate cannot silently retune the tracker.

Beyond identity, a track carries what the behaviour layer above it needs: the
pose of the detection it was matched to, and a per-hand wrist history. Both ride
along the association, so the link from a wrist to an id is the one the matcher
decided - never re-derived downstream by comparing boxes.
"""
import logging
from collections import deque
from enum import Enum

import numpy as np
from scipy.linalg import solve_triangular
from scipy.optimize import linear_sum_assignment
from scipy.spatial import distance as dist

logger = logging.getLogger(__name__)

INFINITY = 1e5
CHI2_INV95 = {2: 5.9915, 4: 9.4877}   # gating thresholds by degrees of freedom

# COCO-17 keypoint indices. The event layer needs the two wrists kept apart -
# one hand can take the cash while the other closes the drawer.
KPT_L_WRIST, KPT_R_WRIST = 9, 10


# ==================================================================== config
class Config:
    """Defaults from tracklib/association/deepsort/config/defaults.py."""

    FPS = 10.0

    N_INIT = 3                   # hits to promote TENTATIVE -> ACTIVE (frames)
    MAX_AGE_S = 3.0              # LOST -> REMOVED
    NEAR_BORDER_MAX_AGE_S = 1.0  # ...shorter at the image border, to stop id theft
    N_FEAT_S = 0.25              # raw features cached per track
    N_TRAJECTORIES_S = 1.0

    # how long a LOST track keeps being reported with a Kalman-predicted box.
    # Such a track may KEEP an event alive but must never trigger a transition.
    PERSON_GRACE_S = 1.0
    HAND_TRAJ_S = 3.0            # per-hand wrist history, for the event layer
    WRIST_CONF_TH = 0.3          # below this the wrist is stored but flagged invalid

    # dynamic appearance: EMA retention per detection, from conf and inter-IoU
    FEAT_UPDATE_METHOD = "dynamic"
    FEAT_EMA_ALPHA = 0.9
    DA_LOWER_CONF, DA_UPPER_CONF = 0.5, 0.9
    DA_LOWER_IOU, DA_UPPER_IOU = 0.4, 0.9
    DA_MIN_ALPHA, DA_MAX_ALPHA = 0.9, 0.98

    MAHA_METHOD = "top"          # track (center-x, TOP-y, a, h): the head stays
                                 # visible when the legs are occluded

    STEP_A_MAX_COST = 0.25
    STEP_A_DEPTH_STRIDE_S = 3.0  # >= MAX_AGE_S means LOST competes with ACTIVE
    STEP_A_BETA = 0.98           # embedding vs mahalanobis in the fusion cost
    STEP_A_GATING_CONF = 0.95

    STEP_B_MAX_COST = 0.7
    STEP_B_MAX_TIME_SINCE_UPDATE_S = 2.0
    # scipy cosine distance is bounded above by 2.0, so the previous value of 2.0
    # never fired and step B ran as bare IoU with the embedding ignored. Same-person
    # pairs sit well under 0.3 on this OSNet, different-person pairs above 0.5.
    STEP_B_EMBED_GATE = 0.5

    STEP_C_MAX_COST = 0.8
    STEP_C_IGNORE_NEAR_IMG_BORDER = True

    STEP_D_MAX_COST = 0.8

    BORDER_THRES = 0.01          # fraction of min(H, W) counted as "at the border"


# ==================================================================== kalman
class KalmanFilter:
    """Constant-velocity filter over (x, y, a, h) and their velocities."""

    def __init__(self):
        ndim, dt = 4, 1.0
        self._motion_mat = np.eye(2 * ndim, 2 * ndim)
        for i in range(ndim):
            self._motion_mat[i, ndim + i] = dt
        self._update_mat = np.eye(ndim, 2 * ndim)
        self._std_pos = 1.0 / 20
        self._std_vel = 1.0 / 160

    def initiate(self, measurement):
        mean = np.r_[measurement, np.zeros(4)]
        h = measurement[3]
        std = [2 * self._std_pos * h, 2 * self._std_pos * h, 1e-2,
               2 * self._std_pos * h, 10 * self._std_vel * h,
               10 * self._std_vel * h, 1e-5, 10 * self._std_vel * h]
        return mean, np.diag(np.square(std))

    def _noise(self, mean):
        h = mean[3]
        std_pos = [self._std_pos * h, self._std_pos * h, 1e-2, self._std_pos * h]
        std_vel = [self._std_vel * h, self._std_vel * h, 1e-5, self._std_vel * h]
        return np.diag(np.square(np.r_[std_pos, std_vel]))

    def predict(self, mean, cov):
        mean = self._motion_mat @ mean
        cov = self._motion_mat @ cov @ self._motion_mat.T + self._noise(mean)
        return mean, cov

    def multi_predict(self, means, covs):
        """Vectorised predict for every track at once."""
        if len(means) == 0:
            return means, covs
        h = means[:, 3]
        std = np.stack([
            self._std_pos * h, self._std_pos * h, 1e-2 * np.ones_like(h),
            self._std_pos * h, self._std_vel * h, self._std_vel * h,
            1e-5 * np.ones_like(h), self._std_vel * h], axis=1)
        motion_cov = np.stack([np.diag(s) for s in np.square(std)])
        means = means @ self._motion_mat.T
        covs = self._motion_mat @ covs @ self._motion_mat.T + motion_cov
        return means, covs

    def project(self, mean, cov):
        h = mean[3]
        std = [self._std_pos * h, self._std_pos * h, 1e-1, self._std_pos * h]
        innovation_cov = np.diag(np.square(std))
        return (self._update_mat @ mean,
                self._update_mat @ cov @ self._update_mat.T + innovation_cov)

    def update(self, mean, cov, measurement):
        proj_mean, proj_cov = self.project(mean, cov)
        kalman_gain = np.linalg.solve(
            proj_cov.T, (cov @ self._update_mat.T).T).T
        innovation = measurement - proj_mean
        new_mean = mean + innovation @ kalman_gain.T
        new_cov = cov - kalman_gain @ proj_cov @ kalman_gain.T
        return new_mean, new_cov

    def gating_distance(self, mean, cov, measurements, only_position=False):
        """Squared Mahalanobis distance from the prediction to each measurement."""
        mean, cov = self.project(mean, cov)
        if only_position:
            mean, cov = mean[:2], cov[:2, :2]
            measurements = measurements[:, :2]
        d = measurements - mean
        try:
            chol = np.linalg.cholesky(cov)
        except np.linalg.LinAlgError:
            return np.full(len(measurements), INFINITY)
        z = solve_triangular(chol, d.T, lower=True, check_finite=False)
        return np.sum(z * z, axis=0)


KF = KalmanFilter()


# ==================================================================== coords
def xyah_to_ltrb(xyah):
    x, y, a, h = xyah
    w = a * h
    return np.array([x - w / 2, y - h / 2, x + w / 2, y + h / 2])


def ltrb_to_xyah(ltrb):
    l, t, r, b = ltrb
    w, h = r - l, b - t
    return np.array([l + w / 2, t + h / 2, w / max(h, 1e-6), h])


def xyah_to_top(xyah):
    """(center-x, center-y, a, h) -> (center-x, top-y, a, h)."""
    out = np.asarray(xyah, dtype=float).copy()
    out[1] -= out[3] / 2
    return out


def top_to_xyah(top):
    out = np.asarray(top, dtype=float).copy()
    out[1] += out[3] / 2
    return out


def ious(a, b):
    a, b = np.asarray(a, float).reshape(-1, 4), np.asarray(b, float).reshape(-1, 4)
    if not len(a) or not len(b):
        return np.zeros((len(a), len(b)))
    x1 = np.maximum(a[:, None, 0], b[None, :, 0])
    y1 = np.maximum(a[:, None, 1], b[None, :, 1])
    x2 = np.minimum(a[:, None, 2], b[None, :, 2])
    y2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    ar_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    ar_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return inter / (ar_a[:, None] + ar_b[None, :] - inter + 1e-9)


# ==================================================================== detection
class Detection:
    __slots__ = ("ltrb", "xyah", "top_xyah", "conf", "feat", "kpts",
                 "da_alpha", "max_inter_iou")

    def __init__(self, ltrb, conf, feat=None, kpts=None):
        self.ltrb = np.asarray(ltrb, dtype=float)
        self.xyah = ltrb_to_xyah(self.ltrb)
        self.top_xyah = xyah_to_top(self.xyah)
        self.conf = float(conf)
        self.feat = feat
        # (17, 3) of (x, y, conf), carried through so the pose reaches the track
        # by way of the association that matched them - never re-derived by
        # comparing boxes afterwards, which can hand one person's wrists to
        # another's id when two people stand shoulder to shoulder at a counter.
        self.kpts = kpts
        self.da_alpha = None
        self.max_inter_iou = None


# ==================================================================== track
class TrackState(Enum):
    TENTATIVE = 1
    ACTIVE = 2
    LOST = 3
    REMOVED = 4


class Track:
    def __init__(self, track_id, det, cfg, img_h, img_w, frames, frame=0):
        self.track_id = track_id
        self.cfg = cfg
        self.img_h, self.img_w = img_h, img_w
        self._f = frames                      # resolved frame counts

        self.state = TrackState.TENTATIVE
        self.hits = 1
        self.age = 1
        self.time_since_update = 0

        measurement = self._measure(det.xyah)
        self.mean, self.cov = KF.initiate(measurement)
        self.last_obs_kf_state = (self.mean.copy(), self.cov.copy())
        self.last_obs_measurement = measurement

        self.conf = det.conf
        self.obs_xyah = det.xyah.copy()
        self.trajectories = deque([det.xyah.copy()], maxlen=frames["n_traj"])

        self.smooth_feat = None
        self.feats = deque([], maxlen=frames["n_feat"])
        # features seen while TENTATIVE are held aside, never averaged in: at
        # promotion the single cleanest one seeds the identity, so a track born
        # under occlusion does not bake that occlusion into its appearance.
        self.tentative_feats = []
        if det.feat is not None:
            self.tentative_feats.append((det.feat, det.da_alpha))

        self.was_in_frame = False

        # ---- pose, for the event layer -------------------------------------
        # kpts is whatever the matched detection carried this frame; the wrist
        # deques are the history the behaviour rules read. Left and right stay
        # separate on purpose (docs 4.2): one hand can take cash while the other
        # closes the drawer, and merging them yields a meaningless trajectory.
        self.kpts = None
        self.kpts_frame = -1
        self.wrist_l = deque(maxlen=frames["hand_traj"])
        self.wrist_r = deque(maxlen=frames["hand_traj"])
        self._record_pose(det, frame)

    # ---------------------------------------------------------- geometry
    def _measure(self, xyah):
        return xyah_to_top(xyah) if self.cfg.MAHA_METHOD == "top" else np.asarray(xyah)

    @property
    def xyah(self):
        m = self.mean[:4].copy()
        return top_to_xyah(m) if self.cfg.MAHA_METHOD == "top" else m

    @property
    def ltrb(self):
        """Kalman-predicted box."""
        return xyah_to_ltrb(self.xyah)

    @property
    def obs_ltrb(self):
        """Last actually-observed box, used by step C."""
        return xyah_to_ltrb(self.obs_xyah)

    def is_near_img_border(self):
        l, t, r, b = self.obs_ltrb
        aspect = (r - l) / max(b - t, 1e-6)
        thres = self.cfg.BORDER_THRES * min(self.img_h, self.img_w)
        # top/bottom only count for wide boxes: a standing person's box touching
        # the bottom edge is ordinary framing, not someone leaving the scene
        return (l < thres or self.img_w - r < thres
                or (t < thres and aspect >= 1.0)
                or (self.img_h - b < thres and aspect >= 1.0))

    # ---------------------------------------------------------- state
    @property
    def is_tentative(self):
        return self.state == TrackState.TENTATIVE

    @property
    def is_active(self):
        return self.state == TrackState.ACTIVE

    @property
    def is_lost(self):
        return self.state == TrackState.LOST

    @property
    def is_removed(self):
        return self.state == TrackState.REMOVED

    # ---------------------------------------------------------- appearance
    def _update_feature(self, feat, alpha):
        if feat is None:
            return
        if self.smooth_feat is None:
            self.smooth_feat = feat.astype(np.float64).copy()
        else:
            self.smooth_feat = alpha * self.smooth_feat + (1.0 - alpha) * feat
        self.smooth_feat /= np.linalg.norm(self.smooth_feat) + 1e-12
        self.feats.append(feat)

    # ---------------------------------------------------------- pose
    def _record_pose(self, det, frame):
        """Store this frame's pose and append one sample to each wrist history.

        A sample is always appended when the track was observed, carrying its own
        frame number and a validity flag. A low-confidence wrist is recorded but
        flagged, rather than dropped or interpolated: velocity computed across a
        guessed wrist position is worse than no velocity, and the rules must be
        able to tell "the hand was elsewhere" from "the hand was not visible".
        Frames where the track was not observed simply leave no sample, so gaps
        are visible as jumps in the stored frame numbers.
        """
        self.kpts = det.kpts
        if det.kpts is None:
            return
        self.kpts_frame = frame
        th = self.cfg.WRIST_CONF_TH
        for idx, hist in ((KPT_L_WRIST, self.wrist_l), (KPT_R_WRIST, self.wrist_r)):
            x, y, c = det.kpts[idx]
            hist.append((frame, float(x), float(y), float(c), bool(c >= th)))

    def wrist(self, side):
        return self.wrist_l if side == "left" else self.wrist_r

    def last_valid_wrist(self, side, max_age=None):
        """Most recent wrist sample flagged valid, or None if it is too stale."""
        for frame, x, y, c, ok in reversed(self.wrist(side)):
            if ok:
                if max_age is not None and self.kpts_frame - frame > max_age:
                    return None
                return (frame, x, y, c)
        return None

    # ---------------------------------------------------------- predict
    def post_predict(self):
        self.age += 1
        self.time_since_update += 1

    # ---------------------------------------------------------- update
    def _oru_interpolate(self, last, new, delta_t):
        return [last + (new - last) * i / delta_t for i in range(1, delta_t)]

    def update(self, det, frame=0):
        measurement = self._measure(det.xyah)

        # OCSORT's Observation-Centric Re-Update: a track recovered after a gap
        # has been coasting on Kalman predictions that drifted. Rewind to the
        # last real observation, replay the filter along a straight line to the
        # new one, and adopt that instead of the drifted state.
        if self.time_since_update > 1:
            m, c = self.last_obs_kf_state
            for interp in self._oru_interpolate(self.last_obs_measurement,
                                                measurement,
                                                self.time_since_update):
                m, c = KF.predict(m, c)
                m, c = KF.update(m, c, interp)
            self.mean, self.cov = KF.predict(m, c)

        self.mean, self.cov = KF.update(self.mean, self.cov, measurement)
        self.last_obs_kf_state = (self.mean.copy(), self.cov.copy())
        self.last_obs_measurement = measurement

        self.obs_xyah = det.xyah.copy()
        self.conf = det.conf
        self.trajectories.append(self.xyah)
        self._record_pose(det, frame)

        self.hits += 1
        self.time_since_update = 0

        if not self.was_in_frame and not self.is_near_img_border():
            self.was_in_frame = True

        if self.state == TrackState.TENTATIVE:
            if det.feat is not None:
                self.tentative_feats.append((det.feat, det.da_alpha))
            if self.hits >= self.cfg.N_INIT:
                self.state = TrackState.ACTIVE
                if self.tentative_feats:
                    # lowest alpha wins; ties break toward the most recent
                    n = len(self.tentative_feats)
                    best = min(((f, a, a + 1e-3 * (n - i))
                                for i, (f, a) in enumerate(self.tentative_feats)),
                               key=lambda e: e[2])
                    self._update_feature(best[0], best[1])
                self.tentative_feats = []
        else:
            if det.feat is not None:
                self._update_feature(det.feat, det.da_alpha)
            if self.state == TrackState.LOST:
                self.state = TrackState.ACTIVE

    def mark_lost(self):
        if self.state == TrackState.TENTATIVE:
            self.state = TrackState.REMOVED
        elif self.state == TrackState.ACTIVE:
            self.state = TrackState.LOST
        elif self.state == TrackState.LOST:
            # adaptive max age: a track that has been fully inside the frame and
            # then vanishes at the border has most likely left, so retire it fast
            # rather than leave an id lying around for a newcomer to steal
            max_age = (self._f["near_border_max_age"]
                       if (self.was_in_frame and self.is_near_img_border())
                       else self._f["max_age"])
            if self.time_since_update > max_age:
                self.state = TrackState.REMOVED


# ==================================================================== costs
def _smooth_embedding_cost(tracks, dets, tidx, didx):
    tf = [tracks[i].smooth_feat for i in tidx]
    df = [dets[j].feat for j in didx]
    if any(f is None for f in tf) or any(f is None for f in df):
        return np.zeros((len(tidx), len(didx)))
    return dist.cdist(np.asarray(tf), np.asarray(df), "cosine")


def iou_cost(tracks, dets, tidx, didx):
    return 1.0 - ious([tracks[i].ltrb for i in tidx], [dets[j].ltrb for j in didx])


def ocr_iou_cost(tracks, dets, tidx, didx):
    """Step C: IoU against the last observation, not the Kalman prediction.

    Once a track has coasted through an occlusion its prediction has drifted, and
    the place it was last actually seen is the better guess.
    """
    return 1.0 - ious([tracks[i].obs_ltrb for i in tidx],
                      [dets[j].ltrb for j in didx])


def maha_gated_fusion_cost(tracks, dets, tidx, didx, cfg):
    """Step A: appearance, blended with and hard-gated by Kalman geometry.

    The chi-squared gate is what makes a match to someone across the frame
    impossible: any pair whose Mahalanobis distance exceeds the 95% interval is
    set to INFINITY before the assignment runs, regardless of embedding
    similarity. Appearance only ranks candidates that geometry already allows.
    """
    cost = _smooth_embedding_cost(tracks, dets, tidx, didx)
    gate = CHI2_INV95[4]
    measurements = np.asarray(
        [dets[j].top_xyah if cfg.MAHA_METHOD == "top" else dets[j].xyah
         for j in didx])
    for row, i in enumerate(tidx):
        maha = KF.gating_distance(tracks[i].mean, tracks[i].cov, measurements)
        cost[row] = cfg.STEP_A_BETA * cost[row] + (1 - cfg.STEP_A_BETA) * maha
        cost[row, maha > gate] = INFINITY
    return cost


def smooth_embedding_gated_iou_cost(tracks, dets, tidx, didx, cfg):
    """Step B: IoU, but only between pairs whose appearance is not contradictory."""
    cost = iou_cost(tracks, dets, tidx, didx)
    embed = _smooth_embedding_cost(tracks, dets, tidx, didx)
    cost[embed > cfg.STEP_B_EMBED_GATE] = INFINITY
    return cost


# ==================================================================== matching
def min_cost_matching(cost_func, max_cost, tracks, dets, tidx, didx):
    if not tidx or not didx:
        return [], list(tidx), list(didx)
    cost = np.asarray(cost_func(tracks, dets, tidx, didx), dtype=float)
    cost[cost > max_cost] = max_cost + 1e-5
    rows, cols = linear_sum_assignment(cost)

    matches, un_t, un_d = [], [], []
    for r, i in enumerate(tidx):
        if r not in rows:
            un_t.append(i)
    for c, j in enumerate(didx):
        if c not in cols:
            un_d.append(j)
    for r, c in zip(rows, cols):
        if cost[r, c] > max_cost:
            un_t.append(tidx[r])
            un_d.append(didx[c])
        else:
            matches.append((tidx[r], didx[c]))
    return matches, un_t, un_d


def cascade_matching(cost_func, max_cost, tracks, dets, tidx, didx,
                     max_depth, stride):
    """Match tracks in bands of time_since_update, freshest band first.

    A stride at or above max age puts every LOST track in the same band as the
    ACTIVE ones, which is what the uae config does.
    """
    if not tidx or not didx:
        return [], list(tidx), list(didx)
    matches, remaining = [], list(didx)
    n_levels = max(1, max_depth // max(stride, 1) + 1)
    for level in range(n_levels):
        if not remaining:
            break
        lo, hi = level * stride, (level + 1) * stride
        band = [i for i in tidx if lo <= tracks[i].time_since_update < hi]
        if not band:
            continue
        m, _, remaining = min_cost_matching(cost_func, max_cost, tracks, dets,
                                            band, remaining)
        matches += m
    matched = {t for t, _ in matches}
    return matches, [i for i in tidx if i not in matched], remaining


# ==================================================================== tracker

#: Manifest spelling -> Config attribute. `match_thres` is the association gate
#: a camera most often needs to move, so it gets the short name; everything else
#: is addressed by the attribute's own name, lowercased.
TRACKER_ALIASES = {"match_thres": "STEP_A_MAX_COST"}


def camera_config(overrides, *, camera_id="?"):
    """A Config subclass carrying one camera's tuning.

    Tracker already took a `cfg`; nothing ever passed one, so every camera in a
    16-camera run shared the class-level defaults and the per-camera
    `person_tracker` block in each manifest configured nothing at all.

    A subclass rather than a mutated Config, because the split architecture runs
    several cameras as threads in one process: writing onto Config would have
    the second camera retune the first, which is the same failure the FSM
    globals had.

    Unknown names raise. A tracker threshold that silently does nothing is how
    someone spends a day tuning a value the tracker never reads.
    """
    if not overrides:
        return Config
    fields = {}
    for key, value in overrides.items():
        attr = TRACKER_ALIASES.get(key, key.upper())
        if not hasattr(Config, attr):
            known = ", ".join(sorted(TRACKER_ALIASES)
                              + [a.lower() for a in vars(Config)
                                 if a.isupper()])
            raise ValueError(
                f"camera {camera_id}: person_tracker.{key} is not a tracker "
                f"setting (looked for Config.{attr}). Known: {known}")
        fields[attr] = value
    return type(f"Config_{camera_id}", (Config,), fields)


class Tracker:
    def __init__(self, img_h, img_w, cfg=Config, fps=None):
        self.cfg = cfg
        self.img_h, self.img_w = img_h, img_w
        fps = float(fps if fps is not None else cfg.FPS)
        self.fps = fps

        def f(seconds):
            return max(1, int(round(seconds * fps)))

        # every duration resolved once, here, from seconds
        self.frames = {
            "max_age": f(cfg.MAX_AGE_S),
            "near_border_max_age": f(cfg.NEAR_BORDER_MAX_AGE_S),
            "n_feat": f(cfg.N_FEAT_S),
            "n_traj": f(cfg.N_TRAJECTORIES_S),
            "depth_stride": f(cfg.STEP_A_DEPTH_STRIDE_S),
            "step_b_max_tsu": f(cfg.STEP_B_MAX_TIME_SINCE_UPDATE_S),
            "person_grace": f(cfg.PERSON_GRACE_S),
            "hand_traj": f(cfg.HAND_TRAJ_S),
        }
        self.tracks = []
        # per-tracker, so each camera counts from 1 and its ids are reproducible
        # regardless of what the other cameras in the process are doing
        self._next_id = 0
        self.frame = 0

    # ---------------------------------------------------------- appearance
    def _apply_dynamic_appearance(self, dets):
        """Per-detection EMA retention from confidence and inter-detection IoU.

        A crop that is low-confidence or heavily overlapped is probably
        contaminated, so it should barely move the identity (alpha -> 0.98); a
        clean isolated one moves it most (alpha -> 0.90).
        """
        cfg = self.cfg
        if cfg.FEAT_UPDATE_METHOD != "dynamic" or not dets:
            for d in dets:
                d.da_alpha = cfg.FEAT_EMA_ALPHA
            return
        ltrbs = np.array([d.ltrb for d in dets])
        m = ious(ltrbs, ltrbs)
        np.fill_diagonal(m, -1.0)
        isolation = 1.0 - m.max(axis=1)
        conf = np.clip([d.conf for d in dets], cfg.DA_LOWER_CONF, cfg.DA_UPPER_CONF)
        iso = np.clip(isolation, cfg.DA_LOWER_IOU, cfg.DA_UPPER_IOU)
        a_conf = cfg.DA_MIN_ALPHA + (1 - cfg.DA_MIN_ALPHA) * (
            1.0 - (conf - cfg.DA_LOWER_CONF) / (cfg.DA_UPPER_CONF - cfg.DA_LOWER_CONF))
        a_iso = cfg.DA_MIN_ALPHA + (1 - cfg.DA_MIN_ALPHA) * (
            1.0 - (iso - cfg.DA_LOWER_IOU) / (cfg.DA_UPPER_IOU - cfg.DA_LOWER_IOU))
        alphas = np.minimum(np.maximum(a_conf, a_iso), cfg.DA_MAX_ALPHA)
        for d, a, i in zip(dets, alphas, isolation):
            d.da_alpha, d.max_inter_iou = float(a), float(i)

    # ---------------------------------------------------------- predict
    def _predict(self):
        if not self.tracks:
            return
        means = np.stack([t.mean for t in self.tracks])
        covs = np.stack([t.cov for t in self.tracks])
        for i, t in enumerate(self.tracks):
            if t.state == TrackState.LOST:
                # freeze aspect and height velocity while unobserved, or the box
                # inflates or collapses the longer the occlusion lasts
                means[i, 6] = 0.0
                means[i, 7] = 0.0
        means, covs = KF.multi_predict(means, covs)
        for i, t in enumerate(self.tracks):
            t.mean, t.cov = means[i], covs[i]
            t.post_predict()

    # ---------------------------------------------------------- match
    def _match(self, dets):
        cfg, F = self.cfg, self.frames
        confirmed = [i for i, t in enumerate(self.tracks) if not t.is_tentative]
        tentative = [i for i, t in enumerate(self.tracks) if t.is_tentative]
        all_d = list(range(len(dets)))

        # STEP A - cascade on fused embedding + mahalanobis, chi2-gated
        m_a, un_t_a, un_d = cascade_matching(
            lambda t, d, ti, di: maha_gated_fusion_cost(t, d, ti, di, cfg),
            cfg.STEP_A_MAX_COST, self.tracks, dets, confirmed, all_d,
            max_depth=F["max_age"] + 1, stride=F["depth_stride"])

        # STEP B - IoU gated by embedding, only for recently seen tracks
        near = [i for i in un_t_a
                if self.tracks[i].time_since_update <= F["step_b_max_tsu"]]
        far = [i for i in un_t_a
               if self.tracks[i].time_since_update > F["step_b_max_tsu"]]
        m_b, un_t_b, un_d = min_cost_matching(
            lambda t, d, ti, di: smooth_embedding_gated_iou_cost(t, d, ti, di, cfg),
            cfg.STEP_B_MAX_COST, self.tracks, dets, near, un_d)

        # STEP C - OCSORT recovery against the last observation
        cand_c = [i for i in far + un_t_b
                  if not (cfg.STEP_C_IGNORE_NEAR_IMG_BORDER
                          and self.tracks[i].is_near_img_border())]
        m_c, _, un_d = min_cost_matching(
            ocr_iou_cost, cfg.STEP_C_MAX_COST, self.tracks, dets, cand_c, un_d)

        # STEP D - plain IoU for tentative tracks
        m_d, _, un_d = min_cost_matching(
            iou_cost, cfg.STEP_D_MAX_COST, self.tracks, dets, tentative, un_d)

        matches = m_a + m_b + m_c + m_d
        matched = {t for t, _ in matches}
        assert len(matched) == len(matches), "a track was matched twice"
        unmatched = [i for i in range(len(self.tracks)) if i not in matched]
        return matches, unmatched, un_d

    # ---------------------------------------------------------- update
    def update(self, dets, frame=None):
        """Advance one frame. Returns (live tracks, ids removed this frame)."""
        self.frame = self.frame + 1 if frame is None else frame
        self._predict()
        self._apply_dynamic_appearance(dets)
        matches, unmatched_t, unmatched_d = self._match(dets)

        for ti, di in matches:
            self.tracks[ti].update(dets[di], self.frame)
        for ti in unmatched_t:
            self.tracks[ti].mark_lost()
        for di in unmatched_d:
            self._next_id += 1
            self.tracks.append(Track(self._next_id, dets[di], self.cfg,
                                     self.img_h, self.img_w, self.frames,
                                     self.frame))

        removed = [t.track_id for t in self.tracks if t.is_removed]
        self.tracks = [t for t in self.tracks if not t.is_removed]
        return self.live_tracks, removed

    @property
    def live_tracks(self):
        """Confirmed tracks with a box, tagged by where that box came from.

        A track that misses a frame is not gone: it is still in self.tracks and
        the Kalman filter is still predicting for it. Reporting only the observed
        ones would make an actor blink out of existence on a single missed
        detection and tear an in-progress event in half. So LOST tracks are also
        returned, within person_grace, tagged "predicted" - which per docs 10 may
        keep an event alive but must not be treated as fresh evidence.
        """
        out = []
        for t in self.tracks:
            if t.is_active:
                out.append((t, "detected"))
            elif t.is_lost and t.time_since_update <= self._f_grace:
                out.append((t, "predicted"))
        return out

    @property
    def _f_grace(self):
        return self.frames["person_grace"]

    @property
    def active_tracks(self):
        return [t for t in self.tracks if t.is_active]

    @property
    def lost_tracks(self):
        return [t for t in self.tracks if t.is_lost]
