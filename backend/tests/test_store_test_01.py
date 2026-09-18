"""The pieces Store test 01 added: playlist cameras, the entrance pipeline,
the real-clock access schedule and the Postgres staff gallery.

No GPU, no network: the entrance pipeline is driven with synthetic detections
and hand-built face answers, the gallery with a stubbed loader.

    python backend/tools/run_tests_nopytest.py backend/tests/test_store_test_01.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _spec(**overrides):
    from backend.src.domain.models import CameraStreamSpec

    base = dict(
        company_id="co", store_id="store_test_01", store_display_name="Store test 01",
        store_timezone="Asia/Ho_Chi_Minh", camera_id="cam", display_name="cam",
        camera_type="entrance", source_kind="video_file", source_uri="x", source_path="x",
        strategy="forever", start_s=0.0, target_duration_s=0.0, target_fps=5.0,
        runtime_profile={}, perception_profile={}, simulation_profile={},
        pipeline_bindings=[], raw_config={},
    )
    base.update(overrides)
    return CameraStreamSpec(**base)


def _clip(path: Path, frames: int, fps: float, size: tuple[int, int], shade: int) -> str:
    import cv2

    w, h = size
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    assert writer.isOpened()
    for _ in range(frames):
        writer.write(np.full((h, w, 3), shade, np.uint8))
    writer.release()
    return str(path)


# ---------------------------------------------------------------- playlist

def test_a_playlist_plays_each_file_its_repeat_count_at_one_resolution(tmp_path):
    from backend.src.runtime.virtual_video_stream import VirtualVideoStreamSource

    a = _clip(tmp_path / "a.mp4", 20, 10.0, (320, 180), 50)    # 2 s -> 10 frames at 5 fps
    b = _clip(tmp_path / "b.mp4", 24, 24.0, (160, 90), 200)    # 1 s -> 5 frames at 5 fps
    src = VirtualVideoStreamSource(_spec(
        source_path=a, source_uri=a, playlist=[{"path": a, "repeat": 2}, {"path": b, "repeat": 1}]))

    passes: list[list] = []
    for frame, _t in src.iter_timed_frames(pace_realtime=False):
        if not passes or passes[-1][0] != frame.loop_index:
            passes.append([frame.loop_index, Path(frame.source_path).name, 0, set()])
        passes[-1][2] += 1
        passes[-1][3].add(frame.image_bgr.shape)
        if frame.loop_index == 4:
            break

    assert [(p[1]) for p in passes[:4]] == ["a.mp4", "a.mp4", "b.mp4", "a.mp4"]
    # Decimation is per file: a 24 fps file is not read with a 10 fps step.
    assert passes[0][2] in (9, 10) and passes[2][2] in (4, 5)
    # The smaller file is resized to the first entry's size, so the tracker and
    # zones built from the first frame stay valid for every frame after it.
    assert all(p[3] == {(180, 320, 3)} for p in passes)


def test_a_playlist_refuses_a_strategy_that_would_never_reach_its_second_file(tmp_path):
    from backend.src.runtime.virtual_video_stream import VirtualVideoStreamSource

    a = _clip(tmp_path / "a.mp4", 10, 10.0, (64, 48), 0)
    with pytest.raises(ValueError) as excinfo:
        VirtualVideoStreamSource(_spec(source_path=a, source_uri=a, strategy="trim",
                                       target_duration_s=10.0,
                                       playlist=[{"path": a, "repeat": 1}]))
    assert "playlist" in str(excinfo.value)


def test_playlist_entries_are_schema_checked():
    from backend.src.infra.manifest_schema import ManifestError, validate_manifest

    manifest = json.loads((BACKEND / "config/stores/store_test_01.json").read_text())
    validate_manifest(manifest)
    manifest["cameras"][0]["stream"]["playlist"][1]["repeats"] = 3
    with pytest.raises(ManifestError) as excinfo:
        validate_manifest(manifest)
    assert "playlist[1].repeats" in str(excinfo.value)


def test_store_test_01_has_the_seven_agreed_cameras():
    from backend.src.infra.local_config import camera_specs_from_manifest

    specs = {s.camera_id: s for s in camera_specs_from_manifest(
        BACKEND / "config/stores/store_test_01.json")}
    expected = {
        "st01_counter": ("counter_cash_theft_v1", 5),
        "st01_storage": ("storage_intrusion_v1", 5),
        "st01_cold_storage": ("cold_storage_intrusion_v1", 5),
        "st01_entrance": ("entrance_face_capture_v1", 5),
        "st01_whole_store": ("whole_store_tracking_v1", 5),
        "st01_whole_store_corner": ("whole_store_tracking_v1", 5),
        "st01_cold_drinks": ("whole_store_tracking_v1", 5),
    }
    assert {cid: (s.pipeline_ids[0], s.target_fps) for cid, s in specs.items()} == expected
    assert all(s.strategy == "forever" for s in specs.values())
    assert [e["repeat"] for e in specs["st01_counter"].playlist] == [100, 1]
    assert {s.store_display_name for s in specs.values()} == {"Store test 01"}


def test_the_ring_holds_the_entrance_cameras_frames():
    from backend.src.app import run_split_pipeline as driver

    h, w, _ = driver.MAX_FRAME_SHAPE
    assert h >= 1440 and w >= 1920, "entrance.mp4 is 1280x1440"


# ---------------------------------------------------------------- real clock

def test_with_no_simulated_clock_the_schedule_reads_the_stores_real_time():
    """It used to read simulated_local_ts alone, None on a real clock, which
    made every staff entry 'outside the allowed window' at every hour."""
    from backend.src.pipelines.storage_intrusion import _in_allowed_window, _store_local_time

    frame = SimpleNamespace(simulated_local_ts=None,
                            ts_utc=datetime(2026, 9, 17, 1, 30, tzinfo=timezone.utc))
    local = _store_local_time(frame, "Asia/Ho_Chi_Minh")
    assert (local.hour, local.minute) == (8, 30)
    schedule = {"allowed_windows": [{"days": ["thu"], "start": "08:00", "end": "09:00"}]}
    assert _in_allowed_window(local, schedule)


# ---------------------------------------------------------------- entrance

class _AlertStore:
    def __init__(self):
        self.alerts, self.media = [], []

    def create_alert(self, **kw):
        self.alerts.append(kw)
        return f"alert{len(self.alerts)}"

    def add_alert_media(self, **kw):
        self.media.append(kw)


class _MediaStore:
    def __init__(self):
        self.clips = []

    def save_snapshot(self, alert_id, frame):
        return f"/m/{alert_id}/snapshot.jpg"

    def save_image(self, alert_id, name, image):
        return f"/m/{alert_id}/{name}"

    def save_clip(self, alert_id, clip, fps):
        self.clips.append(alert_id)
        return f"/m/{alert_id}/evidence.mp4"


def _entrance(tmp_path):
    from backend.src.domain.models import CameraPipelineBindingSpec
    from backend.src.pipelines.entrance_face_capture import EntranceFaceCapturePipeline

    zone = tmp_path / "zone.json"
    # Lower half of the frame is inside.
    zone.write_text(json.dumps({"entrance": {"zone_norm": [[0, 0.5], [1, 0.5], [1, 1], [0, 1]]}}))
    binding = CameraPipelineBindingSpec("entrance_face_capture_v1", True, {
        "zone_config_path": str(zone), "zone_asset_key": "entrance",
        "entry_confirm_seconds": 0.5, "min_face_h": 32, "face_top_frac": 0.35,
        "alert_clip": {"pre_s": 1, "post_s": 1}})
    store = _AlertStore()
    return EntranceFaceCapturePipeline(_spec(), binding, store, _MediaStore(), "run"), store


def _frame(idx):
    from backend.src.domain.models import FrameEnvelope

    return FrameEnvelope(
        company_id="co", store_id="store_test_01", camera_id="cam", camera_type="entrance",
        frame_idx=idx, ts_utc=datetime(2026, 9, 17, 3, 0, tzinfo=timezone.utc),
        simulated_local_ts=None, source_kind="video_file", source_uri="x", source_path="x",
        image_bgr=np.zeros((1440, 1280, 3), np.uint8), source_frame_idx=idx,
        source_time_s=idx / 5.0, loop_index=0)


def _person(top_y):
    box = np.array([[540.0, top_y, 740.0, top_y + 500.0, 0.9]], np.float32)
    kpts = np.zeros((17, 3), np.float32)
    kpts[[11, 12]] = [640.0, top_y + 275.0, 0.9]    # pelvis 55% down the box
    feats = np.ones((1, 512), np.float32) / np.sqrt(512.0)
    return box, [kpts], feats


def _person_match(person_id, similarity, is_staff):
    return SimpleNamespace(person_id=person_id, display_name=person_id, is_staff=is_staff,
                           similarity=similarity)


def _answer(score, face_box, person=None, top1=None):
    return SimpleNamespace(
        observation=SimpleNamespace(
            detector_score=score, face_box_xyxy=np.array(face_box, np.float32),
            person_box_xyxy=np.array([0, 0, 200, 500], np.float32),
            chip_bgr=np.zeros((112, 112, 3), np.uint8)),
        person=person, top1=top1 or person)


def _walk_in(pipeline, answers_for):
    """A person walking from outside into the zone; answers_for(idx) gives
    the face answers returned for each face request on that frame."""
    requested = set()
    for idx in range(45):
        top = min(40.0 * idx, 900.0)    # pelvis crosses y=720 around frame 11
        _out, targets = pipeline.handle_perceived(_frame(idx), *_person(top))
        for tid, *_ in targets:
            requested.add(tid)
            for answer in answers_for(idx):
                pipeline.handle_face_answer(tid, answer)
    pipeline.finalize()
    return requested


def test_a_watchlisted_face_walking_in_raises_one_alert_with_evidence(tmp_path):
    pipeline, store = _entrance(tmp_path)
    shoplifter = lambda sim: _person_match("store_test_01_watchlist_01", sim, is_staff=False)

    def answers(idx):
        # A usable face that matches, a better match on a box too far down the
        # body (fabric), and a better match on a face too small to trust.
        return [_answer(0.7, [70, 20, 130, 90], shoplifter(0.40 + 0.01 * (idx % 5))),
                _answer(0.95, [70, 380, 130, 450], shoplifter(0.90)),
                _answer(0.90, [90, 30, 110, 50], shoplifter(0.85))]

    assert len(_walk_in(pipeline, answers)) == 1
    assert len(store.alerts) == 1
    alert = store.alerts[0]
    assert (alert["event_type"], alert["severity"]) == ("watchlist_match", "warning")
    assert alert["identity_person_id"] == "store_test_01_watchlist_01"
    # only the usable face's similarity counts, never the rejected boxes'
    assert abs(alert["identity_score"] - 0.44) < 1e-6
    assert alert["metadata"]["faces_rejected_position"] > 0
    assert alert["metadata"]["faces_rejected_small"] > 0
    assert sorted(m["kind"] for m in store.media) == ["clip", "face", "snapshot"]
    assert pipeline.metrics["watchlist_alerts"] == 1


def test_a_walk_in_with_no_match_writes_nothing(tmp_path):
    """Footfall is not an incident: a visit without a watchlist match is
    counted, never stored."""
    pipeline, store = _entrance(tmp_path)
    near = _person_match("store_test_01_watchlist_01", 0.2, is_staff=False)
    _walk_in(pipeline, lambda idx: [_answer(0.7, [70, 20, 130, 90], person=None, top1=near)])
    assert store.alerts == [] and store.media == []
    assert pipeline.metrics["visits_confirmed"] == 1
    assert pipeline.metrics["visits_with_face"] == 1


def test_a_staff_match_at_the_entrance_is_never_a_shoplifter(tmp_path):
    pipeline, store = _entrance(tmp_path)
    staff = _person_match("store_test_01_staff_a", 0.9, is_staff=True)
    _walk_in(pipeline, lambda idx: [_answer(0.7, [70, 20, 130, 90], staff)])
    assert store.alerts == []


def test_a_person_first_seen_inside_is_walking_out_and_gets_no_face_work(tmp_path):
    pipeline, store = _entrance(tmp_path)
    for idx in range(30):
        top = max(900.0 - 40.0 * idx, 500.0)       # starts inside, moves up
        _out, targets = pipeline.handle_perceived(_frame(idx), *_person(top))
        assert targets == []
    assert store.alerts == []


def test_storage_doors_match_staff_and_the_entrance_matches_the_watchlist():
    """A watchlisted face must stay 'unknown' at a storage door, or the
    intrusion pipeline -- which alerts on unknown faces -- would stay silent."""
    from backend.src.app.run_split_pipeline import _camera_face_config
    from backend.src.infra.local_config import camera_specs_from_manifest

    specs = {s.camera_id: s for s in camera_specs_from_manifest(
        BACKEND / "config/stores/store_test_01.json")}
    assert _camera_face_config(specs["st01_storage"]).match_is_staff is True
    assert _camera_face_config(specs["st01_cold_storage"]).match_is_staff is True
    assert _camera_face_config(specs["st01_entrance"]).match_is_staff is False


def test_the_sqlite_gallery_matches_within_one_role(tmp_path):
    from backend.src.perception.face_identity import FaceGallery

    vec = np.ones(512, np.float32) / np.sqrt(512.0)
    g = FaceGallery(str(tmp_path / "g.sqlite"))
    try:
        g.upsert_person("thief", "Regular shoplifter", is_staff=False, store_id=None)
        g.add_embedding("thief", vec)
        assert g.match(vec, is_staff=False)[3] == "matched"
        assert g.match(vec, is_staff=True)[3] == "gallery_empty"
        assert g.summarize(is_staff=True)["embeddings"] == 0
    finally:
        g.close()


# ---------------------------------------------------------------- gallery

def test_the_postgres_gallery_keeps_its_roster_when_a_refresh_fails(tmp_path):
    """Losing Neon for a minute must not turn every staff member into an
    unknown intruder."""
    from backend.src.infra.postgres_face_gallery import PostgresFaceGallery, open_face_gallery
    from backend.src.perception.face_identity import FaceGallery

    vec = np.ones(512, np.float32) / np.sqrt(512.0)
    roster = [{"embedding_id": 1, "person_id": "store_test_01_staff_a", "embedding": vec,
               "image_path": None, "display_name": "Staff A", "is_staff": True,
               "company_id": "co", "store_id": "store_test_01"}]
    calls = {"n": 0}

    def load():
        calls["n"] += 1
        if calls["n"] > 1:
            raise ConnectionError("neon unreachable")
        return roster

    gallery = PostgresFaceGallery("postgresql://u:p@host/db", refresh_s=0.0)
    gallery._load = load
    person, _t1, _t2, reason = gallery.match(vec, store_id="store_test_01", company_id="co")
    assert reason == "matched" and person.person_id == "store_test_01_staff_a"
    person, _t1, _t2, reason = gallery.match(vec, store_id="store_test_01", company_id="co")
    assert calls["n"] == 2 and reason == "matched"
    assert gallery.match(vec, store_id="store_1")[3] == "gallery_empty"
    assert gallery.match(vec, store_id="store_test_01", is_staff=False)[3] == "gallery_empty"
    assert "p@" not in gallery.db_path

    assert isinstance(open_face_gallery("postgresql://u:p@h/db"), PostgresFaceGallery)
    assert type(open_face_gallery(str(tmp_path / "gallery.sqlite"))) is FaceGallery
