"""Export the RetailS LSTM-AE to ONNX and score real videos with it.

Pipeline per video: YOLO-pose -> ReID+tracker (so each person becomes one pose sequence) ->
sliding 12-frame windows, normalized exactly as in training -> reconstruction MSE per window.
A video's score is an aggregate over its windows; several aggregates are reported because
"is this clip anomalous" is not the same question as "is this frame anomalous".

    python3 scripts/lstm_infer_videos.py                    # 10 + 10 from each dataset
    python3 scripts/lstm_infer_videos.py --n 20 --fps 10

Note the model was trained on RetailS, whose keypoints are stored (y, x); YOLO-pose emits
(x, y). This script feeds (x, y) - the same convention the fixed training code uses - so the
two agree. What it cannot fix is the domain gap: different store, camera, and pose extractor
(HRNet for RetailS vs YOLO-pose here).
"""
import argparse
import csv
import os
import random
import sys
import time

import cv2
import numpy as np
import onnxruntime as ort
import torch
import torch.nn as nn
from sklearn.metrics import roc_auc_score

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

CKPT = f"{ROOT}/models/lstm_ae_best.pt"
ONNX = f"{ROOT}/models/lstm_ae_best.onnx"
OUT_CSV = f"{ROOT}/outputs/lstm_video_scores.csv"

SEG_LEN = 12          # must match training
CONF_TH = 0.3         # window mean keypoint confidence below this is not scored

DATASETS = {
    "shoplifting": (f"{ROOT}/datasets/shoplifting/shoplifting",
                     f"{ROOT}/datasets/shoplifting/normal"),
    "Shop DataSet": (f"{ROOT}/datasets/Shop DataSet/shop lifters",
                      f"{ROOT}/datasets/Shop DataSet/non shop lifters"),
}


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
    m = LSTMAutoencoder()
    m.load_state_dict(torch.load(CKPT, map_location="cpu"))
    m.eval()
    torch.onnx.export(m, torch.zeros(1, SEG_LEN, 34), ONNX,
                      input_names=["pose_window"], output_names=["reconstruction"],
                      opset_version=17)
    # verify the export rather than assume it: a silently wrong graph would just look like
    # a weak model downstream, which is exactly the failure we are trying to rule out
    sess = ort.InferenceSession(ONNX, providers=["CPUExecutionProvider"])
    name = sess.get_inputs()[0].name
    x = np.random.randn(1, SEG_LEN, 34).astype(np.float32)
    with torch.no_grad():
        ref = m(torch.from_numpy(x)).numpy()
    got = sess.run(None, {name: x})[0]
    print(f"exported {ONNX}  (max |torch - onnx| = {np.abs(ref - got).max():.2e})")
    return sess, name


def normalize_window(kps):
    """kps: (T, 17, 3) as [x, y, conf] -> (feat (T,34), mean_conf). Same rule as training."""
    xy, conf = kps[..., :2], kps[..., 2]
    center = xy.reshape(-1, 2).mean(axis=0)
    scale = xy[..., 1].std()
    scale = scale if scale >= 1e-3 else 1.0
    return ((xy - center) / scale).reshape(len(kps), -1).astype(np.float32), float(conf.mean())


def pose_sequences(video, det, emb, Tracker, Detection, fps_target, keep_frames=False):
    """-> ({track_id: [(frame_idx, (17,3) kpts)]}, n_frames, boxes_by_frame, frames).

    boxes_by_frame/frames are only populated when keep_frames is set - they exist so the
    overlay pass can reuse the exact frames that were scored instead of decoding again and
    risking a different sampling.
    """
    cap = cv2.VideoCapture(video)
    W, H = int(cap.get(3)), int(cap.get(4))
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    tracker = Tracker(img_h=H, img_w=W, fps=fps_target)
    seqs, step, acc, idx = {}, fps_target / src_fps, 0.0, 0
    boxes_by_frame, frames = {}, []
    while True:
        if not cap.grab():
            break
        acc += step
        if acc < 1.0:
            continue
        acc -= 1.0
        ok, frame = cap.retrieve()
        if not ok:
            break
        boxes, kpts = det(frame)
        feats = emb(frame, boxes)
        live, _ = tracker.update(
            [Detection(boxes[i, :4], boxes[i, 4], feats[i], kpts[i]) for i in range(len(boxes))],
            idx)
        for t, _src in live:
            if t.kpts is not None:
                seqs.setdefault(t.track_id, []).append((idx, np.asarray(t.kpts, np.float32)))
        if keep_frames:
            frames.append(frame)
            boxes_by_frame[idx] = [(t.track_id, np.asarray(t.ltrb, float),
                                    None if t.kpts is None else np.asarray(t.kpts, np.float32))
                                   for t, _ in live]
        idx += 1
    cap.release()
    return seqs, idx, boxes_by_frame, frames


def score_video(seqs, sess, inp_name):
    """-> (pooled window scores, {track_id: {frame_idx: score}}).

    A window's score is assigned to every frame it covers; overlapping windows are averaged,
    the same scheme the notebook's eval uses, so the per-frame trace and the pooled numbers
    describe the same thing.
    """
    scores = []
    per_frame = {}
    for tid, items in seqs.items():
        items.sort(key=lambda x: x[0])
        idxs = [i for i, _ in items]
        arr = np.stack([k for _, k in items])            # (L, 17, 3), [x, y, conf]
        # only score runs of consecutive frames - a gap means the tracker lost the person,
        # and stitching across it would invent motion that never happened
        runs, cur = [], [0]
        for j in range(1, len(idxs)):
            if idxs[j] - idxs[j - 1] == 1:
                cur.append(j)
            else:
                runs.append(cur)
                cur = [j]
        runs.append(cur)
        acc_t, cnt_t = {}, {}
        for run in runs:
            a = arr[run]
            if len(a) < SEG_LEN:
                continue
            for s in range(0, len(a) - SEG_LEN + 1):
                feat, mconf = normalize_window(a[s:s + SEG_LEN])
                if mconf < CONF_TH:
                    continue
                rec = sess.run(None, {inp_name: feat[None]})[0][0]
                sc = float(((rec - feat) ** 2).mean())
                scores.append(sc)
                for j in run[s:s + SEG_LEN]:
                    f = idxs[j]
                    acc_t[f] = acc_t.get(f, 0.0) + sc
                    cnt_t[f] = cnt_t.get(f, 0) + 1
        if acc_t:
            per_frame[tid] = {f: acc_t[f] / cnt_t[f] for f in acc_t}
    return np.array(scores), per_frame


COCO_SKELETON = [
    (15, 13), (13, 11), (16, 14), (14, 12), (11, 12), (5, 11), (6, 12),
    (5, 6), (5, 7), (6, 8), (7, 9), (8, 10), (1, 2), (0, 1), (0, 2),
    (1, 3), (2, 4), (3, 5), (4, 6),
]


def to_h264(path):
    """OpenCV writes mp4v, which many players refuse. Re-encode in place if ffmpeg exists."""
    import shutil, subprocess
    if not shutil.which("ffmpeg"):
        return
    tmp = path.replace(".mp4", ".h264.mp4")
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path,
                        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
                        "-pix_fmt", "yuv420p", tmp], capture_output=True)
    if r.returncode == 0:
        os.replace(tmp, path)
    elif os.path.exists(tmp):
        os.remove(tmp)


def render_overlay(frames, boxes_by_frame, per_frame, thresh, out_path, fps, title):
    """Draw skeletons + per-track anomaly score, flag frames over `thresh`, write events.

    The score is raw reconstruction MSE, which has no absolute meaning across videos, so
    `thresh` is expected to be a calibrated number passed in by the caller - the overlay
    prints it so a viewer can see what the red boxes are relative to.
    """
    H, W = frames[0].shape[:2]
    s = max(1.0, W / 640.0)
    th, fs = max(1, int(round(2 * s))), 0.45 * s
    writer = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))

    events, open_ev = [], {}
    for i, frame in enumerate(frames):
        img = frame.copy()
        hot = False
        for tid, ltrb, kp in boxes_by_frame.get(i, []):
            sc = per_frame.get(tid, {}).get(i)
            over = sc is not None and sc >= thresh
            hot |= over
            col = (0, 0, 255) if over else (90, 200, 90)
            p1 = (int(ltrb[0]), int(ltrb[1]))
            cv2.rectangle(img, p1, (int(ltrb[2]), int(ltrb[3])), col, th)
            if kp is not None:
                for a, b in COCO_SKELETON:
                    if kp[a, 2] > 0.2 and kp[b, 2] > 0.2:
                        cv2.line(img, tuple(kp[a, :2].astype(int)),
                                 tuple(kp[b, :2].astype(int)), col, max(1, th - 1))
            tag = f"ID{tid}" + (f"  {sc:.4f}" if sc is not None else "  (no score)")
            cv2.putText(img, tag, (p1[0], max(12, p1[1] - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, fs, col, max(1, th - 1), cv2.LINE_AA)

            # event bookkeeping: one interval per track per contiguous over-threshold run
            if over and tid not in open_ev:
                open_ev[tid] = dict(track=int(tid), start_frame=i, peak=float(sc))
            elif over:
                open_ev[tid]["peak"] = max(open_ev[tid]["peak"], float(sc))
            elif tid in open_ev:
                ev = open_ev.pop(tid); ev["end_frame"] = i - 1; events.append(ev)

        for tid in [t for t in open_ev if t not in {b[0] for b in boxes_by_frame.get(i, [])}]:
            ev = open_ev.pop(tid); ev["end_frame"] = i - 1; events.append(ev)

        if hot:
            cv2.rectangle(img, (0, 0), (W - 1, H - 1), (0, 0, 255), th * 2)
        cv2.rectangle(img, (0, 0), (W, int(20 * s)), (0, 0, 0), -1)
        cv2.putText(img, f"{title}  f{i}  t={i/fps:.1f}s  thr={thresh:.4f}"
                         f"{'   ANOMALY' if hot else ''}",
                    (6, int(14 * s)), cv2.FONT_HERSHEY_SIMPLEX, fs,
                    (0, 0, 255) if hot else (230, 230, 230), max(1, th - 1), cv2.LINE_AA)
        writer.write(img)

    for tid, ev in open_ev.items():
        ev["end_frame"] = len(frames) - 1; events.append(ev)
    writer.release()
    to_h264(out_path)

    for e in events:
        e["start_s"] = round(e["start_frame"] / fps, 2)
        e["end_s"] = round(e["end_frame"] / fps, 2)
        e["peak"] = round(e["peak"], 5)
    return sorted(events, key=lambda e: e["start_frame"])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--n", type=int, default=10, help="videos per class per dataset")
    ap.add_argument("--fps", type=float, default=10.0)
    ap.add_argument("--pose", default=f"{ROOT}/models/yolo26m-pose.onnx")
    ap.add_argument("--conf", type=float, default=0.35)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--save-video", action="store_true",
                     help="write annotated mp4 + events.jsonl per video")
    ap.add_argument("--thresh", type=float,
                     help="absolute anomaly threshold; default is the given percentile of all "
                          "window scores from the NORMAL videos in this run (calibration set)")
    ap.add_argument("--thresh-pct", type=float, default=95.0)
    a = ap.parse_args()

    sess, inp_name = export_onnx()

    import stream_track as S
    from uae_tracker import Detection, Tracker
    S.MODEL, S.CONF = a.pose, a.conf
    det, emb = S.Detector(), S.Embedder()
    print(f"pose: {os.path.basename(a.pose)} @conf={a.conf}, sampling {a.fps} fps\n")

    rows, pending, all_scores = [], [], {}
    for ds, (pos_dir, neg_dir) in DATASETS.items():
        for label, d in ((1, pos_dir), (0, neg_dir)):
            vids = sorted(f for f in os.listdir(d) if f.lower().endswith(".mp4"))
            random.Random(a.seed).shuffle(vids)
            for v in vids[:a.n]:
                path = os.path.join(d, v)
                t0 = time.perf_counter()
                seqs, nframes, bbf, frames = pose_sequences(
                    path, det, emb, Tracker, Detection, a.fps, keep_frames=a.save_video)
                sc, per_frame = score_video(seqs, sess, inp_name)
                row = dict(dataset=ds, label=label, video=v, frames=nframes,
                           tracks=len(seqs), windows=len(sc),
                           mean=float(sc.mean()) if len(sc) else np.nan,
                           p95=float(np.percentile(sc, 95)) if len(sc) else np.nan,
                           max=float(sc.max()) if len(sc) else np.nan)
                rows.append(row)
                if a.save_video:
                    pending.append((row, bbf, frames, per_frame))
                print(f"[{ds:12s}] {'THEFT ' if label else 'normal'} {v:34s} "
                      f"tracks={len(seqs):2d} win={len(sc):4d} "
                      f"mean={row['mean']:.4f} p95={row['p95']:.4f} max={row['max']:.4f} "
                      f"({time.perf_counter()-t0:.0f}s)")
                if a.save_video:
                    all_scores.setdefault(label, []).extend(sc.tolist())

    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    with open(OUT_CSV, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    print(f"\nwrote {OUT_CSV}")

    if a.save_video:
        # Threshold from the NORMAL videos only, so "anomalous" means "unlike the normal
        # clips in this run" rather than a number pulled from the air. Rendering waits until
        # every video is scored precisely so this calibration set exists.
        import json
        if a.thresh is not None:
            thr = a.thresh
            how = "given on the command line"
        else:
            neg = np.array(all_scores.get(0, []))
            if len(neg) == 0:
                thr = float(np.percentile(np.array(all_scores.get(1, [0.0])), a.thresh_pct))
                how = f"p{a.thresh_pct:g} of THEFT windows (no normal videos in run)"
            else:
                thr = float(np.percentile(neg, a.thresh_pct))
                how = f"p{a.thresh_pct:g} of {len(neg)} normal-video windows"
        vid_dir = f"{ROOT}/outputs/lstm_video_out"
        os.makedirs(vid_dir, exist_ok=True)
        print(f"\nthreshold = {thr:.5f}  ({how})")
        n_ev = 0
        for row, bbf, frames, per_frame in pending:
            if not frames:
                continue
            stem = f"{row['dataset'].replace(' ', '_')}_{'theft' if row['label'] else 'normal'}_" \
                   f"{os.path.splitext(row['video'])[0]}"
            out_mp4 = f"{vid_dir}/{stem}.mp4"
            evs = render_overlay(frames, bbf, per_frame, thr, out_mp4, a.fps, stem)
            with open(f"{vid_dir}/{stem}_events.jsonl", "w") as fh:
                for e in evs:
                    fh.write(json.dumps(e) + "\n")
            n_ev += len(evs)
            print(f"  {stem}.mp4  ({len(evs)} event{'s' if len(evs) != 1 else ''})")
        print(f"\nwrote {len(pending)} videos + events to {vid_dir}  ({n_ev} events total)")

    print("\n" + "=" * 74)
    for ds in DATASETS:
        sub = [r for r in rows if r["dataset"] == ds and not np.isnan(r["mean"])]
        y = np.array([r["label"] for r in sub])
        print(f"\n{ds}   ({len(sub)} videos scored, {int(y.sum())} theft / {int((1-y).sum())} normal)")
        if len(set(y.tolist())) < 2:
            print("  only one class survived scoring - AUC undefined")
            continue
        for agg in ("mean", "p95", "max"):
            s = np.array([r[agg] for r in sub])
            auc = roc_auc_score(y, s)
            print(f"  {agg:4s}: theft={s[y==1].mean():.4f}  normal={s[y==0].mean():.4f}  "
                  f"AUC={auc:.3f}  {'' if s[y==1].mean() > s[y==0].mean() else '(INVERTED)'}")


if __name__ == "__main__":
    main()
