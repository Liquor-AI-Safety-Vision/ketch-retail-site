"""Convert the RetailS LSTM-AE checkpoint to ONNX and evaluate it locally on both
labelled test splits (test_staged, test_realworld), reusing the corrected frame-alignment
logic from retails_baseline.ipynb section 7 (frame_idx is absolute in the clip, multi-track
frames are combined by max).

    python3 scripts/retails_export_eval.py
"""
from pathlib import Path as _Path
import glob
import json
import os

import numpy as np
import onnxruntime as ort
import torch
import torch.nn as nn
from sklearn.metrics import average_precision_score, roc_auc_score, roc_curve

HERE = str(_Path(__file__).resolve().parents[1])
ROOT = str(_Path(__file__).resolve().parents[2])
DATA_ROOT = f"{ROOT}/data/datasets/RetailS"
CKPT_PATH = f"{ROOT}/backend/models/lstm_ae_best.pt"
ONNX_PATH = f"{ROOT}/backend/models/lstm_ae_best.onnx"

SEG_LEN = 12   # v2: matches GEPC/STG-NF default (was 24)


class LSTMAutoencoder(nn.Module):
    def __init__(self, input_dim=34, hidden=64, latent=32, num_layers=1):
        super().__init__()
        self.encoder = nn.LSTM(input_dim, hidden, num_layers, batch_first=True)
        self.to_latent = nn.Linear(hidden, latent)
        self.from_latent = nn.Linear(latent, hidden)
        self.decoder = nn.LSTM(hidden, hidden, num_layers, batch_first=True)
        self.out = nn.Linear(hidden, input_dim)

    def forward(self, x):
        T = x.size(1)
        _, (h, _) = self.encoder(x)
        z = self.to_latent(h[-1])
        dec_in = self.from_latent(z).unsqueeze(1).repeat(1, T, 1)
        dec_out, _ = self.decoder(dec_in)
        return self.out(dec_out)


def export_onnx():
    model = LSTMAutoencoder()
    model.load_state_dict(torch.load(CKPT_PATH, map_location="cpu"))
    model.eval()
    dummy = torch.zeros(1, SEG_LEN, 34)
    torch.onnx.export(
        model, dummy, ONNX_PATH,
        input_names=["pose_window"], output_names=["reconstruction"],
        opset_version=17,
    )
    print(f"exported {ONNX_PATH}")


def contiguous_runs(frame_dict):
    idxs = sorted(int(k) for k in frame_dict.keys())
    runs, run = [], [idxs[0]]
    for a, b in zip(idxs, idxs[1:]):
        if b - a == 1:
            run.append(b)
        else:
            runs.append(run)
            run = [b]
    runs.append(run)
    return runs


def track_to_array(frame_dict, idxs):
    return np.array([frame_dict[str(i)]["keypoints"] for i in idxs],
                     dtype=np.float32).reshape(-1, 17, 3)


def normalize_window(kps):
    """kps: (T, 17, 3) raw -> feat (T, 34)

    STG-NF-style: center on the mean of every keypoint across the whole window, scale by the
    std of the Y-coordinate across the whole window - computed per window, not per frame/track.
    """
    xy = kps[..., :2]
    center = xy.reshape(-1, 2).mean(axis=0)
    scale = xy[..., 1].std()
    scale = scale if scale >= 1e-3 else 1.0
    xy_norm = (xy - center) / scale
    return xy_norm.reshape(len(kps), -1).astype(np.float32)


class OnnxScorer:
    def __init__(self, onnx_path):
        so = ort.SessionOptions()
        so.log_severity_level = 3
        self.sess = ort.InferenceSession(onnx_path, so, providers=["CPUExecutionProvider"])
        self.inp = self.sess.get_inputs()[0].name

    def _forward(self, x):  # x: (1, seg_len, 34)
        return self.sess.run(None, {self.inp: x})[0]

    def sliding_infer(self, raw_kps, seg_len=SEG_LEN):
        """raw_kps: (T, 17, 3) raw, one contiguous run -> per-frame reconstruction error (T,).
        Each candidate window is normalized independently, matching training."""
        T = raw_kps.shape[0]
        if T <= seg_len:
            pad = seg_len - T
            arr_p = np.pad(raw_kps, ((0, pad), (0, 0), (0, 0)), mode="edge")
            feat = normalize_window(arr_p)
            rec = self._forward(feat[None])[0]
            return ((rec - feat) ** 2).mean(-1)[:T]
        err_sum, count = np.zeros(T), np.zeros(T)
        for start in range(0, T - seg_len + 1):
            feat = normalize_window(raw_kps[start:start + seg_len])
            rec = self._forward(feat[None])[0]
            e = ((rec - feat) ** 2).mean(-1)
            err_sum[start:start + seg_len] += e
            count[start:start + seg_len] += 1
        return err_sum / np.maximum(count, 1)

    def score_track(self, frames_dict, seg_len=SEG_LEN):
        out = {}
        for run in contiguous_runs(frames_dict):
            arr = track_to_array(frames_dict, run)   # raw (L, 17, 3)
            scores = self.sliding_infer(arr, seg_len)
            out.update(zip(run, scores))
        return out


def eval_split(scorer, pose_dir, gt_dir, label):
    files = sorted(glob.glob(f"{pose_dir}/*.json"))
    y_true, y_score, n_multitrack, n_dropped = [], [], 0, 0
    for f in files:
        stem = os.path.splitext(os.path.basename(f))[0]
        gt = np.load(f"{gt_dir}/{stem}.npy")
        L = len(gt)
        d = json.load(open(f))
        n_multitrack += len(d) > 1

        full = np.full(L, np.nan)
        for frames in d.values():
            for fidx, s in scorer.score_track(frames).items():
                if 0 <= fidx < L:
                    full[fidx] = s if np.isnan(full[fidx]) else max(full[fidx], s)
                else:
                    n_dropped += 1

        mask = ~np.isnan(full)
        y_true.append(gt[mask]); y_score.append(full[mask])
    y_true, y_score = np.concatenate(y_true), np.concatenate(y_score)

    auc_roc = roc_auc_score(y_true, y_score)
    auc_pr = average_precision_score(y_true, y_score)
    fpr, tpr, _ = roc_curve(y_true, y_score)
    fnr = 1 - tpr
    idx = np.nanargmin(np.abs(fnr - fpr))
    eer = (fpr[idx] + fnr[idx]) / 2

    print(f"{label}: {len(files)} clips, {n_multitrack} multi-track "
          f"({n_dropped} track-frames outside clip range, dropped)")
    print(f"{label}: AUC-ROC={auc_roc:.3f}  AUC-PR={auc_pr:.3f}  EER={eer:.3f}  "
          f"(n_frames={len(y_true)}, positive_rate={y_true.mean()*100:.1f}%)")


if __name__ == "__main__":
    export_onnx()
    scorer = OnnxScorer(ONNX_PATH)
    eval_split(scorer, f"{DATA_ROOT}/RetailS_test_staged/pose/test",
               f"{DATA_ROOT}/RetailS_test_staged/gt/test_frame_mask", "test_staged")
    eval_split(scorer, f"{DATA_ROOT}/RetailS_test_realworld/pose/test",
               f"{DATA_ROOT}/RetailS_test_realworld/gt/test_frame_mask", "test_realworld")
