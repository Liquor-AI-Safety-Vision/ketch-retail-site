from __future__ import annotations

import multiprocessing as mp
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from backend.src.domain.models import CameraStreamSpec
from backend.src.runtime.camera_runtime import CameraRuntime
from backend.src.runtime.perf import cap_native_threads
from backend.src.runtime.shared_inference import SharedInferenceRegistry


def _run_camera_worker(
    spec: CameraStreamSpec,
    *,
    db_path: str,
    media_root: str,
    gallery_db_path: str,
    cpu_only: bool,
    pace_realtime: bool,
    limit_frames: int | None,
    native_threads: int,
    shared_inference: SharedInferenceRegistry | None = None,
) -> dict[str, Any]:
    cap_native_threads(native_threads)
    runtime = CameraRuntime(
        spec,
        db_path=db_path,
        media_root=media_root,
        gallery_db_path=gallery_db_path,
        cpu_only=cpu_only,
        shared_inference=shared_inference,
    )
    return runtime.run(
        pace_realtime=pace_realtime,
        limit_frames=limit_frames,
    )


class RuntimeSupervisor:
    def __init__(
        self,
        specs: list[CameraStreamSpec],
        *,
        db_path: str,
        media_root: str,
        gallery_db_path: str,
        cpu_only: bool = True,
        native_threads: int = 1,
        shared_inference_config: dict[str, Any] | None = None,
    ):
        self.specs = specs
        self.db_path = db_path
        self.media_root = media_root
        self.gallery_db_path = gallery_db_path
        self.cpu_only = cpu_only
        self.native_threads = native_threads
        self.shared_inference_config = dict(shared_inference_config or {})

    def run(
        self,
        *,
        camera_ids: list[str] | None = None,
        pace_realtime: bool = False,
        limit_frames: int | None = None,
        limit_seconds: float | None = None,
        concurrency_mode: str = "seq",
    ) -> list[dict[str, Any]]:
        selected = [spec for spec in self.specs if camera_ids is None or spec.camera_id in camera_ids]
        if concurrency_mode == "seq":
            return [self._run_one(spec, pace_realtime, limit_frames, limit_seconds) for spec in selected]

        if concurrency_mode == "thread":
            return self._run_threaded(selected, pace_realtime, limit_frames, limit_seconds)
        if concurrency_mode == "process":
            return self._run_processed(selected, pace_realtime, limit_frames, limit_seconds)
        raise ValueError(f"unsupported concurrency_mode: {concurrency_mode}")

    def _run_threaded(
        self,
        selected: list[CameraStreamSpec],
        pace_realtime: bool,
        limit_frames: int | None,
        limit_seconds: float | None,
    ) -> list[dict[str, Any]]:
        results_by_camera: dict[str, dict[str, Any]] = {}
        shared_inference = SharedInferenceRegistry(
            cpu_only=self.cpu_only,
            config=self.shared_inference_config,
        )
        try:
            with ThreadPoolExecutor(max_workers=len(selected) or 1, thread_name_prefix="camera-runtime") as pool:
                future_map = {
                    pool.submit(
                        self._run_one,
                        spec,
                        pace_realtime,
                        limit_frames,
                        limit_seconds,
                        shared_inference,
                    ): spec.camera_id
                    for spec in selected
                }
                for future in as_completed(future_map):
                    camera_id = future_map[future]
                    results_by_camera[camera_id] = future.result()
        finally:
            shared_inference.close()
        return [results_by_camera[spec.camera_id] for spec in selected]

    def _run_processed(
        self,
        selected: list[CameraStreamSpec],
        pace_realtime: bool,
        limit_frames: int | None,
        limit_seconds: float | None,
    ) -> list[dict[str, Any]]:
        ctx = mp.get_context("spawn")
        with ctx.Pool(processes=len(selected) or 1) as pool:
            jobs = []
            for spec in selected:
                effective_limit_frames = self._effective_limit_frames(spec, limit_frames, limit_seconds)
                jobs.append(
                    (
                        spec.camera_id,
                        pool.apply_async(
                            _run_camera_worker,
                            kwds={
                                "spec": spec,
                                "db_path": self.db_path,
                                "media_root": self.media_root,
                                "gallery_db_path": self.gallery_db_path,
                                "cpu_only": self.cpu_only,
                                "pace_realtime": pace_realtime,
                                "limit_frames": effective_limit_frames,
                                "native_threads": self.native_threads,
                            },
                        ),
                    )
                )
            results_by_camera = {camera_id: job.get() for camera_id, job in jobs}
        return [results_by_camera[spec.camera_id] for spec in selected]

    @staticmethod
    def _effective_limit_frames(
        spec: CameraStreamSpec,
        limit_frames: int | None,
        limit_seconds: float | None,
    ) -> int | None:
        effective_limit_frames = limit_frames
        if effective_limit_frames is None and limit_seconds is not None:
            effective_limit_frames = max(1, int(round(limit_seconds * spec.target_fps)))
        return effective_limit_frames

    def _run_one(
        self,
        spec: CameraStreamSpec,
        pace_realtime: bool,
        limit_frames: int | None,
        limit_seconds: float | None,
        shared_inference: SharedInferenceRegistry | None = None,
    ) -> dict[str, Any]:
        return _run_camera_worker(
            spec,
            db_path=self.db_path,
            media_root=self.media_root,
            gallery_db_path=self.gallery_db_path,
            cpu_only=self.cpu_only,
            pace_realtime=pace_realtime,
            limit_frames=self._effective_limit_frames(spec, limit_frames, limit_seconds),
            native_threads=self.native_threads,
            shared_inference=shared_inference,
        )
