"""The till FSM's tunable thresholds, named once.

Two consumers need this list and they must not drift apart: the pipeline,
which applies the values, and the manifest schema, which decides whether a
manifest may name them. A key present in one and absent from the other is
precisely the failure the schema exists to catch -- a correctly spelled
setting that is either refused for no reason or accepted and read by nobody.

It lives in its own module rather than on the pipeline class because
manifest_schema would otherwise have to import counter_cash_theft to see the
tables, and that pulls in stream_track, run_counter and onnxruntime -- a
GPU-stack import in the path of validating a JSON file.

Both groups are per camera. Neither is a property of the system:

  timing    the appendix gives these as frames at 10 fps, which is where they
            were measured and not a rate any camera here runs at. A manifest
            states them in SECONDS -- checkable against a video, unlike "3
            frames at a frame rate this camera does not use".

  geometry  in units of torso length, so they survive distance from the
            camera -- but not camera ANGLE. A till filmed from 3 m up at a
            slant does not place the wrist at the same normalised distance
            from the drawer centre as one filmed level from 2 m, so
            DRAWER_IN = 0.80 cannot be correct for both. Measured on a single
            clip; per camera is the only honest grain.
"""
from __future__ import annotations

#: manifest key (seconds) -> (FSM global, the appendix's frames at 10 fps)
TIMING_FRAMES_AT_10FPS: dict[str, tuple[str, int]] = {
    "hand_inside_confirm_s": ("HAND_INSIDE_CONFIRM", 3),
    "hand_exit_confirm_s": ("HAND_EXIT_CONFIRM", 3),
    "pose_grace_s": ("POSE_GRACE", 5),
    "dwell_confirm_s": ("DWELL_CONFIRM", 5),
    "state_timeout_s": ("STATE_TIMEOUT", 50),
    "post_event_s": ("POST_EVENT", 30),
    "traj_window_s": ("TRAJ_WINDOW", 25),
    "traj_lookback_s": ("TRAJ_LOOKBACK", 5),
}

#: manifest key -> FSM global. Unitless ratios, applied as given.
GEOMETRY: dict[str, str] = {
    "drawer_roi_expand": "DRAWER_ROI_EXPAND",
    "drawer_in": "DRAWER_IN",
    "drawer_out": "DRAWER_OUT",
    "deep_in": "DEEP_IN",
    "withdraw_delta": "WITHDRAW_DELTA",
    "pocket_radius": "POCKET_RADIUS",
    "hand_follow_gate": "HAND_FOLLOW_GATE",
    "wrist_conf_th": "WRIST_CONF_TH",
}

#: Not a threshold: a switch over which hands the drawer test accepts.
FLAGS: dict[str, str] = {"any_hand": "ANY_HAND"}
