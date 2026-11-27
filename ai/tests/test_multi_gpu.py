"""Inference processes spread over the GPUs of one machine."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_one_gpu_leaves_cuda_alone():
    from ai.src.app.run_split_pipeline import _gpu_for

    assert [_gpu_for(k, 1) for k in range(3)] == [None, None, None]


def test_processes_go_round_robin_and_face_takes_the_last_gpu():
    from ai.src.app.run_split_pipeline import _gpu_for

    assert [_gpu_for(k, 2) for k in range(4)] == [0, 1, 0, 1]
    assert _gpu_for(2 - 1, 2) == 1          # the face tier's GPU on a 2-GPU box


def test_pin_gpu_sets_the_visible_device_only_when_asked(monkeypatch):
    from ai.src.runtime.inference_server import pin_gpu

    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    pin_gpu(None)
    assert "CUDA_VISIBLE_DEVICES" not in os.environ
    pin_gpu(1)
    assert os.environ["CUDA_VISIBLE_DEVICES"] == "1"


def test_detect_gpus_never_reports_zero(monkeypatch):
    from ai.src.app import run_split_pipeline

    monkeypatch.setattr(run_split_pipeline.shutil, "which", lambda _name: None)
    assert run_split_pipeline.detect_gpus() == 1
