"""Storage rooms: alert when someone enters who should not be there.

A person crossing into the storage zone (zone_monitor.ZoneMonitor, confirmed over
entry_confirm_seconds) opens an entry. With face_identity enabled, their face
is identified against the store's staff gallery for about a second around the
entry (the face tier answers asynchronously); the best answer decides:

    unknown face                     -> unknown_entry
    staff, outside access_schedule   -> staff_outside_schedule
    staff, inside the schedule       -> nothing
"""
from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ai.src.common.timing import TimingCollector
from ai.src.domain.models import CameraPipelineBindingSpec, CameraStreamSpec, FrameEnvelope
from ai.src.perception import zone_monitor
from ai.src.perception.face_identity import MatchDecision
from ai.src.pipelines.alert_emitter import AlertEmitter
from ai.src.pipelines.base import FaceTarget, Pipeline, PipelineOutput, TrackedFrame


DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _store_local_time(frame: FrameEnvelope, store_timezone: str) -> datetime:
    """The wall-clock time at the store when this frame was taken.

    The simulated clock when the manifest enables one, the real clock
    otherwise. This used to read simulated_local_ts alone, which is None
    whenever the simulation clock is off -- and _in_allowed_window(None) is
    False, so on a real clock every staff entry was "outside the allowed
    window" at every hour of the day.
    """
    if frame.simulated_local_ts is not None:
        return frame.simulated_local_ts
    from zoneinfo import ZoneInfo

    return frame.ts_utc.astimezone(ZoneInfo(store_timezone))


def _parse_hhmm(value: str) -> int:
    hh, mm = value.split(":")
    return int(hh) * 60 + int(mm)


def _in_allowed_window(local_ts: datetime | None, access_schedule: dict[str, Any]) -> bool:
    if local_ts is None:
        return False
    windows = access_schedule.get("allowed_windows", [])
    if not windows:
        return False
    day_name = DAY_NAMES[local_ts.weekday()]
    # A window that crosses midnight is listed under the day it *starts* on,
    # which is how a shift roster reads: "Friday 22:00-06:00" is Friday
    # night, and its small hours fall on Saturday. So a Saturday 02:00 sample
    # has to be tested against Friday's windows, not Saturday's -- which
    # means the day of the sample is not enough to pick which windows apply.
    #
    # Getting this wrong is not a near miss. Checking `day_name in days`
    # first, before the wrap is considered, accepts the evening half and
    # rejects the morning half of the very same shift: staff on shift at
    # 02:00 read as intruders. An earlier version of this function did
    # exactly that while carrying a comment claiming otherwise.
    prev_day_name = DAY_NAMES[(local_ts.weekday() - 1) % 7]
    minute_of_day = local_ts.hour * 60 + local_ts.minute
    for window in windows:
        days = window.get("days", DAY_NAMES)
        start_min = _parse_hhmm(window["start"])
        end_min = _parse_hhmm(window["end"])
        if start_min <= end_min:
            if day_name in days and start_min <= minute_of_day <= end_min:
                return True
            continue
        # Crosses midnight: two halves, listed under two different calendar
        # days but belonging to one shift.
        if day_name in days and minute_of_day >= start_min:
            return True                      # tonight's evening half
        if prev_day_name in days and minute_of_day <= end_min:
            return True                      # last night's morning half
    return False


@dataclass
class PendingEntry:
    track_id: int
    event_frame_idx: int
    event_ts_utc: datetime
    event_local_ts: datetime | None
    snapshot_frame: FrameEnvelope
    resolved: bool = False


class EntryFaceWindow:
    def __init__(self, start_frame: int):
        self.start_frame = start_frame
        self.end_frame: int | None = None
        self.best: tuple[tuple[float, float], MatchDecision] | None = None

    def is_collecting(self, frame_idx: int) -> bool:
        return self.end_frame is None or frame_idx <= self.end_frame

    def consider(self, decision: MatchDecision | None):
        # None means no identification happened at all -- the async face tier
        # answering a request that raised, so the business worker's count can
        # come down. Guarded here as well as at the call site: the call site
        # guard is what the current path relies on, but a method that
        # dereferences its argument unconditionally is one new caller away
        # from the same crash.
        if decision is None or decision.observation is None:
            return
        key = (
            float(decision.top1.similarity if decision.top1 is not None else 0.0),
            float(decision.observation.detector_score),
        )
        if self.best is None or key > self.best[0]:
            self.best = (key, decision)

    def resolved_decision(self) -> MatchDecision | None:
        return None if self.best is None else self.best[1]


class StorageIntrusionPipeline(Pipeline):
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
        self.alerts = AlertEmitter(spec, binding, alert_store, media_store, run_id)
        self.monitor = None
        # Identification runs in the face tier: this pipeline only collects
        # targets for it and takes the answers back in on_face_answer.
        self.face_enabled = bool(self.spec.perception_profile.get("face_identity", {}).get("enabled", False))
        self.entry_windows: dict[int, EntryFaceWindow] = {}
        self.pending_entries: dict[int, PendingEntry] = {}
        # Tracks whose face request came back as a failure rather than as a
        # verdict. Without this, "the face tier is broken" and "this person
        # never turned toward the camera" both arrive as no decision at all,
        # and both raise an identical unknown-person alert -- so a dead face
        # model reads as a night of intruders. Alerting either way is
        # deliberate (a real stranger during an outage must not be missed);
        # telling them apart lets an operator see an outage for what it is.
        self.face_failed_tracks: set[int] = set()
        self.metrics = {
            "alerts_emitted": 0,
            "entries_seen": 0,
            "unknown_alerts": 0,
            "face_identification_failures": 0,
            "staff_outside_schedule_alerts": 0,
        }
        self.timing = TimingCollector()

        zone_data = json.loads(Path(self.params["zone_config_path"]).read_text())
        self.zone_cfg = zone_data[self.params["zone_asset_key"]]
        self.min_box_h_frac = float(
            self.zone_cfg.get("min_box_h_frac", zone_monitor.Config.min_box_h_frac))
        self.access_schedule = dict(self.params.get("access_schedule", {}))
        self.post_entry_frames = max(
            1,
            round(
                float(self.params.get("face_capture_window", {}).get("end_after_inside_s", 1.0))
                * self.spec.target_fps
            ),
        )

    def _zone_monitor_config(self) -> zone_monitor.Config:
        conf = float(self.spec.perception_profile.get("person_detector", {}).get("conf_thres", 0.45))
        cfg = zone_monitor.Config(fps=self.spec.target_fps)
        cfg.person_conf = conf
        if "entry_confirm_seconds" in self.params:
            cfg.entry_confirmation_s = float(self.params["entry_confirm_seconds"])
        if "exit_confirm_seconds" in self.params:
            cfg.exit_confirmation_s = float(self.params["exit_confirm_seconds"])
        if "presence_confirm_frames" in self.params:
            cfg.presence_confirmation_frames = int(self.params["presence_confirm_frames"])
        if "exit_on_loss_min_frames" in self.params:
            cfg.exit_on_loss_min_frames = int(self.params["exit_on_loss_min_frames"])
        if "anchor" in self.params:
            anchor = str(self.params["anchor"])
            # Checked rather than trusted: ZoneMonitor compares cfg.anchor
            # against "bottom_center" and takes the keypoint path for anything
            # else, so a typo would silently move every zone decision.
            if anchor not in zone_monitor.ANCHOR_MODES:
                raise ValueError(
                    f"{self.spec.camera_id}: anchor={anchor!r} is not one of "
                    f"{sorted(zone_monitor.ANCHOR_MODES)}. An unrecognised value would "
                    f"quietly select the keypoint anchor and shift every zone "
                    f"crossing on this camera.")
            cfg.anchor = anchor
        # Once, after both confirmation windows are settled: __post_init__ is
        # what turns them into entry_frames/exit_frames.
        cfg.__post_init__()
        cfg.min_box_h_frac = self.min_box_h_frac
        return cfg

    def finalize(self) -> int:
        """Close out anything still open. Returns alerts emitted while doing so.

        Anyone who walks into the zone in the last second of a stream has an
        open face window when the frames run out; that entry is resolved with
        whatever the window has, not dropped.
        """
        emitted = 0
        for track_id in list(self.pending_entries):
            window = self.entry_windows.get(track_id)
            emitted += self._finalize_entry(
                track_id, window.resolved_decision() if window else None)
        # After, not before: an alert raised just above opens a clip.
        self.alerts.flush()
        return emitted

    def _finalize_entry(self, track_id: int, decision: MatchDecision | None):
        pending = self.pending_entries.get(track_id)
        if pending is None or pending.resolved:
            return 0
        pending.resolved = True

        is_unknown = True
        is_staff = False
        person_id = None
        label = "Unknown"
        similarity = None
        reason = "no_identity_decision"
        top1 = None
        top2 = None
        if decision is not None:
            is_unknown = decision.is_unknown
            person_id = decision.identity
            label = decision.display_name
            reason = decision.reason
            top1 = decision.top1.similarity if decision.top1 is not None else None
            top2 = decision.top2.similarity if decision.top2 is not None else None
            if decision.person is not None:
                is_staff = bool(decision.person.is_staff)
                similarity = float(decision.person.similarity)

        # A failed identification still alerts -- an outage is not a reason to
        # stop watching the door -- but it says so.
        face_failed = decision is None and track_id in self.face_failed_tracks
        if face_failed:
            reason = "face_identification_failed"

        event_type = None
        reason_codes = [reason]
        if is_unknown:
            event_type = "unknown_entry"
            reason_codes.append(
                "FACE_IDENTIFICATION_FAILED" if face_failed else "UNKNOWN_PERSON_ENTERED_STORAGE")
            self.metrics["unknown_alerts"] += 1
        elif is_staff and not _in_allowed_window(pending.event_local_ts, self.access_schedule):
            event_type = "staff_outside_schedule"
            reason_codes.append("STAFF_ENTERED_OUTSIDE_ALLOWED_WINDOW")
            self.metrics["staff_outside_schedule_alerts"] += 1

        self.pending_entries.pop(track_id, None)
        self.entry_windows.pop(track_id, None)
        self.face_failed_tracks.discard(track_id)
        if event_type is None:
            return 0

        self.alerts.emit(
            event_type=event_type,
            severity="warning",
            snapshot_frame=pending.snapshot_frame,
            opened_at=pending.event_ts_utc,
            event_frame_idx=pending.event_frame_idx,
            identity_person_id=person_id,
            identity_label=label,
            identity_score=similarity,
            reason_codes=reason_codes,
            metadata={
                "track_id": track_id,
                "camera_type": self.spec.camera_type,
                "event_frame_idx": pending.event_frame_idx,
                "event_source_time_s": round(pending.snapshot_frame.source_time_s, 3),
                "identity_reason": reason,
                "top1_similarity": top1,
                "top2_similarity": top2,
                "company_id": self.spec.company_id,
                "store_id": self.spec.store_id,
            },
            media_metadata={"track_id": track_id},
        )
        self.metrics["alerts_emitted"] += 1
        return 1

    def on_frame(self, tracked: TrackedFrame) -> PipelineOutput:
        """Zone logic, entry windows and entry alerts for one tracked frame.

        Returns the entries needing identification as face_targets, for the
        business tier to route to the face tier.
        """
        frame = tracked.frame
        if self.monitor is None:
            h, w = frame.image_bgr.shape[:2]
            self.monitor = zone_monitor.ZoneMonitor(self.zone_cfg["zone_norm"], w, h,
                                                 self._zone_monitor_config())
        self.alerts.push(frame)
        removed = tracked.removed
        with self.timing.track("zone_monitor_drop"):
            self.monitor.drop(removed, frame.frame_idx)

        alerts_now = 0
        for tid in removed:
            window = self.entry_windows.get(tid)
            decision = window.resolved_decision() if window is not None else None
            alerts_now += self._finalize_entry(tid, decision)

        events_before = len(self.monitor.events)
        with self.timing.track("zone_monitor_update"):
            items = self.monitor.update(
                [(t.track_id, t.ltrb, t.kpts, source == "detected") for t, source in tracked.live],
                frame.frame_idx,
            )
        state_of = {tid: st for tid, _ltrb, _kp, st, _pt, _streak in items}

        if self.face_enabled:
            for tid, st in state_of.items():
                if st == zone_monitor.PENDING_IN:
                    self.entry_windows.setdefault(tid, EntryFaceWindow(frame.frame_idx))
                elif st == zone_monitor.OUTSIDE and tid in self.pending_entries:
                    window = self.entry_windows.get(tid)
                    decision = window.resolved_decision() if window is not None else None
                    alerts_now += self._finalize_entry(tid, decision)

        face_targets: list[FaceTarget] = []
        if self.face_enabled:
            for tid, ltrb, _kp, st, _pt, _streak in items:
                window = self.entry_windows.get(tid)
                if window is None or not window.is_collecting(frame.frame_idx):
                    continue
                if st not in (zone_monitor.PENDING_IN, zone_monitor.INSIDE):
                    continue
                face_targets.append(FaceTarget(tid, ltrb, self.spec.store_id, self.spec.company_id))

        new_events = self.monitor.events[events_before:]
        for event in new_events:
            if event["event"] != "ZONE_ENTRY":
                continue
            tid = int(event["track_id"])
            self.metrics["entries_seen"] += 1
            self.pending_entries[tid] = PendingEntry(
                track_id=tid,
                event_frame_idx=frame.frame_idx,
                event_ts_utc=frame.ts_utc,
                event_local_ts=_store_local_time(frame, self.spec.store_timezone),
                # A copy: frame.image_bgr is a view into a ring slot the
                # decoder will have overwritten by the time the entry window
                # closes. Cheap, since a zone entry is rare.
                snapshot_frame=dataclasses.replace(frame, image_bgr=frame.image_bgr.copy()),
            )
            if not self.face_enabled:
                alerts_now += self._finalize_entry(tid, None)
                continue
            window = self.entry_windows.setdefault(tid, EntryFaceWindow(frame.frame_idx))
            window.end_frame = frame.frame_idx + self.post_entry_frames

        if self.face_enabled:
            for tid, window in list(self.entry_windows.items()):
                st = state_of.get(tid)
                if window.end_frame is None:
                    continue
                if st in (zone_monitor.PENDING_IN, zone_monitor.INSIDE) and frame.frame_idx <= window.end_frame:
                    continue
                alerts_now += self._finalize_entry(tid, window.resolved_decision())

        self.alerts.persist_ready(frame.frame_idx)
        self.metrics["timing"] = self.timing.as_dict()
        return PipelineOutput(alerts_emitted=alerts_now, events_emitted=len(new_events),
                              face_targets=face_targets)

    def on_face_answer(self, track_id: int, decision) -> None:
        """Feed an async identification result in whenever it arrives.

        A window that has already been finalized or was never opened (the
        track left before its request came back) is silently ignored.
        decision=None is the face tier saying it could not identify at all.
        """
        if decision is None:
            # Remembered, so the alert this track eventually raises can say
            # the identification failed rather than that the person was
            # unrecognised.
            self.face_failed_tracks.add(int(track_id))
            self.metrics["face_identification_failures"] += 1
            return
        window = self.entry_windows.get(track_id)
        if window is not None:
            window.consider(decision)
