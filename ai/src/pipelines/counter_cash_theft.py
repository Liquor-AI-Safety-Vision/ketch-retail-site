"""Counter: alert when a staff member takes cash from the open drawer and conceals it.

Per frame, from the camera's tracks and the till segmentation:

    tracks -> staff / customer side of the counter (zones.RoleVoter)
    till   -> drawer Open / Close, debounced over till_confirm_frames samples
    staff wrist trajectory + open drawer -> concealment FSM (cash_concealment_fsm)
    CONCEALED -> alert, unless a customer stood at that checkout in the last
                 customer_recent_window_s (then it was a sale, not a theft)
"""
from __future__ import annotations

import importlib.util
import itertools
from pathlib import Path
from typing import Any

import numpy as np

from ai.src.common.paths import zones_dir
from ai.src.common.timing import TimingCollector
from ai.src.domain.models import CameraPipelineBindingSpec, CameraStreamSpec, FrameEnvelope
from ai.src.perception import zones
from ai.src.perception.till_state import TillRegistry
from ai.src.pipelines import fsm_tuning
from ai.src.pipelines.alert_emitter import AlertEmitter
from ai.src.pipelines.base import Pipeline, PipelineOutput, TrackedFrame
from ai.src.pipelines.till_schedule import compute_till_schedule

# cash_concealment_fsm is deliberately NOT imported here. Every use goes through
# self.F, the per-camera copy from load_private_fsm(); a shared module-level
# alias is exactly what one camera would retune out from under another.

_FSM_INSTANCE_COUNTER = itertools.count()


def load_private_fsm():
    """A cash_concealment_fsm module object private to one pipeline instance.

    The FSM keeps its tuning in module-level globals -- FPS, the confirm
    counts, ANY_HAND, WRIST_CONF_TH -- and reads them from inside step(). The
    business tier runs several cameras as threads in one process, so with one
    shared module the second camera to start would retune the first one's
    thresholds. A module object per pipeline makes those globals per camera.

    The copies are not registered in sys.modules, so nothing outside this
    pipeline can reach them by name.
    """
    source = Path(__file__).resolve().parents[1] / "perception" / "cash_concealment_fsm.py"
    name = f"cash_concealment_fsm__private{next(_FSM_INSTANCE_COUNTER)}"
    spec = importlib.util.spec_from_file_location(name, source)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load a private copy of {source}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CounterCashTheftPipeline(Pipeline):
    def __init__(
        self,
        spec: CameraStreamSpec,
        binding: CameraPipelineBindingSpec,
        alert_store,
        media_store,
        run_id: str,
    ):
        super().__init__(spec, binding)
        self.params = binding.params
        # Private FSM module: its tuning globals belong to this camera alone.
        self.F = load_private_fsm()
        self.alerts = AlertEmitter(spec, binding, alert_store, media_store, run_id)
        self.suppress_if_customer_recent = bool(self.params.get("suppress_if_customer_recent", True))
        self.customer_recent_window_frames = max(
            1,
            int(round(float(self.params.get("customer_recent_window_s", 3.0)) * self.spec.target_fps)),
        )

        schedule = compute_till_schedule(
            self.spec.target_fps, int(self.params.get("till_confirm_frames", 5)),
            self.params.get("till_fps"))
        self.till_reg = TillRegistry(schedule.confirm_samples)

        self.voter = None
        self.checkout = {}
        self.last_customer_seen: dict[str, int] = {}
        self.hands_by_track: dict[int, Any] = {}
        self.events_by_track: dict[int, Any] = {}
        self.last_till_box = None
        self.last_till_open = False
        self.metrics = {
            "alerts_emitted": 0,
            "suppressed_events": 0,
            "state_transitions": 0,
            "till_state_changes": 0,
            "till_every_n_frames": schedule.every_n,
            "till_confirm_samples": schedule.confirm_samples,
            "till_confirm_window_s": schedule.confirm_window_s,
        }
        self.timing = TimingCollector()

    def _configure_fsm_globals(self):
        fsm = dict(self.params.get("fsm", {}))
        fps = self.spec.target_fps
        # The default arithmetic is left exactly as it was -- frames_10fps *
        # fps / 10 -- so every recorded benchmark still describes this code.
        # An override takes the seconds path instead; at the default seconds
        # the two agree, but they are not written as the same expression and
        # nothing here relies on them rounding identically.
        scale = lambda frames_10fps: max(1, int(round(frames_10fps * fps / 10.0)))

        values = {"FPS": float(fps)}
        for key, const in fsm_tuning.FLAGS.items():
            values[const] = bool(fsm.get(key, getattr(self.F, const)))
        for key, (const, frames_10fps) in fsm_tuning.TIMING_FRAMES_AT_10FPS.items():
            values[const] = (
                max(1, int(round(float(fsm[key]) * fps))) if key in fsm
                else scale(frames_10fps)
            )
        for key, const in fsm_tuning.GEOMETRY.items():
            if key in fsm:
                values[const] = float(fsm[key])

        # DRAWER_IN/DRAWER_OUT are a hysteresis pair: entry at or under
        # DRAWER_IN, exit at or over DRAWER_OUT. Inverted or equal, the FSM
        # does not fail -- it oscillates, emitting a stream of entries and
        # exits that look like a very busy employee.
        drawer_in = values.get("DRAWER_IN", self.F.DRAWER_IN)
        drawer_out = values.get("DRAWER_OUT", self.F.DRAWER_OUT)
        if drawer_in >= drawer_out:
            raise ValueError(
                f"{self.spec.camera_id}: fsm.drawer_in ({drawer_in}) must be below "
                f"fsm.drawer_out ({drawer_out}). They are a hysteresis pair -- with "
                f"no gap the drawer state chatters on every frame a wrist hovers at "
                f"the boundary, and every chatter is an event.")

        for name, value in values.items():
            setattr(self.F, name, value)

    def _bootstrap(self, frame: FrameEnvelope):
        """Zone voter, checkout zones and FSM tuning; needs the first frame for its size."""
        h, w = frame.image_bgr.shape[:2]
        zones_path = self.params.get("zones_path", str(zones_dir() / "top_side_view_zones.json"))
        divider, _name = zones.load_divider(zones_path, size=(w, h))
        self.voter = zones.RoleVoter(divider)
        self.checkout = self.F.load_checkout_zones(zones_path, (w, h))
        self._configure_fsm_globals()

    def finalize(self) -> int:
        self.alerts.flush()
        return 0

    def _customer_served_recently(self, till_c: tuple[float, float] | None, frame_idx: int):
        zone_name = self.F.zone_for_till(till_c, self.checkout)
        served = (
            zone_name is not None
            and zone_name in self.last_customer_seen
            and frame_idx - self.last_customer_seen[zone_name] <= self.customer_recent_window_frames
        )
        return served, zone_name

    def _emit_alert(self, frame: FrameEnvelope, event, zone_name: str | None):
        track_id = int(event.track_id)
        self.alerts.emit(
            event_type="suspected_cash_concealment",
            severity="high",
            snapshot_frame=frame,
            opened_at=frame.ts_utc,
            event_frame_idx=frame.frame_idx,
            identity_label=f"track_{event.track_id}",
            reason_codes=list(event.reasons),
            metadata={
                "employee_track_id": track_id,
                "alert_frame_idx": frame.frame_idx,
                "event_start_frame_idx": int(event.start),
                "event_source_time_s": round(frame.source_time_s, 3),
                "active_hand": "RIGHT" if event.active_hand == "R" else "LEFT",
                "checkout_zone": zone_name,
                "company_id": self.spec.company_id,
                "store_id": self.spec.store_id,
            },
            media_metadata={"employee_track_id": track_id},
        )
        self.metrics["alerts_emitted"] += 1

    def _update_till(self, till_dets) -> int:
        """Debounce this frame's drawer reading; returns the number of state changes.

        Only frames that actually sampled the drawer advance the confirm
        streak: feeding it a repeated reading would reach `confirm` without
        new evidence. On skipped frames the previous confirmed state stands.
        """
        if till_dets is None:
            return 0
        items_till, changes = self.till_reg.update(till_dets)
        self.metrics["till_state_changes"] += len(changes)
        open_boxes = []
        self.last_till_open = False
        for till_state, _raw_name, _score, poly in items_till:
            if till_state.state == "Open":
                self.last_till_open = True
                open_boxes.append([float(poly[:, 0].min()), float(poly[:, 1].min()),
                                   float(poly[:, 0].max()), float(poly[:, 1].max())])
        if open_boxes:
            self.last_till_box = open_boxes[0]
        return len(changes)

    def on_frame(self, tracked: TrackedFrame) -> PipelineOutput:
        frame = tracked.frame
        if self.voter is None:
            self._bootstrap(frame)
        self.alerts.push(frame)
        for tid in tracked.removed:
            self.voter.drop(tid)
        till_changes = self._update_till(tracked.till_detections)

        with self.timing.track("voter_role_update"):
            live_items = []
            for track, _source in tracked.live:
                role, anchor_pt = self.voter.update(track.track_id, track.ltrb, track.kpts)
                live_items.append((track, role, anchor_pt))

        with self.timing.track("customer_zone_update"):
            for zone_name, poly in self.checkout.items():
                for track, role, _anchor_pt in live_items:
                    if role != zones.CUSTOMER:
                        continue
                    pt, _src = zones.anchor(track.ltrb, np.array(track.kpts) if track.kpts is not None else None)
                    if self.F.point_in_poly(pt, poly):
                        self.last_customer_seen[zone_name] = frame.frame_idx
                        break

        alerts_now = 0
        till_c = None
        if self.last_till_box is not None:
            till_c = (
                (self.last_till_box[0] + self.last_till_box[2]) / 2.0,
                (self.last_till_box[1] + self.last_till_box[3]) / 2.0,
            )

        with self.timing.track("fsm_logic"):
            for track, role, _anchor_pt in live_items:
                if role != zones.STAFF or track.kpts is None:
                    continue
                k = np.array(track.kpts)
                torso = self.F.torso_len(k)
                hands = self.hands_by_track.setdefault(track.track_id, self.F.HandHistory())
                for side_name, wrist_idx in (("L", self.F.WRI_L), ("R", self.F.WRI_R)):
                    hands.push(side_name, self.F.Sample(frame.frame_idx, k[wrist_idx, 0], k[wrist_idx, 1], k[wrist_idx, 2]))

                event = self.events_by_track.get(track.track_id)
                if event is None or event.state == self.F.ENDED:
                    event = self.F.ConcealmentEvent(track.track_id, frame.frame_idx)
                    self.events_by_track[track.track_id] = event
                before = event.state
                self.F.step(event, frame.frame_idx, k, torso, self.last_till_box, self.last_till_open, hands, [])
                if before != event.state:
                    self.metrics["state_transitions"] += 1
                if event.state == self.F.CONCEALED and not event.alerted:
                    event.alerted = True
                    served, zone_name = self._customer_served_recently(till_c, frame.frame_idx)
                    if self.suppress_if_customer_recent and served:
                        self.metrics["suppressed_events"] += 1
                        continue
                    self._emit_alert(frame, event, zone_name)
                    alerts_now += 1

        self.alerts.persist_ready(frame.frame_idx)
        self.metrics["timing"] = self.timing.as_dict()
        return PipelineOutput(alerts_emitted=alerts_now, events_emitted=till_changes)
