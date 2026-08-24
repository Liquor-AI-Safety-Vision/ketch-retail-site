"""Run run_counter.py over every counter-generated clip and tabulate the result.

    python3 scripts/batch_counter.py [--limit N] [--no-video]

One subprocess per clip rather than one long-lived process with the models held
in memory. That costs ~5 s of session setup per clip, and buys two things worth
more than the time: the command run here is character-for-character the command
a person would run by hand on one clip, and a clip that crashes the pipeline
takes its own process down instead of the whole batch.

Writes per-clip artefacts under outputs/counter-generated/ and a summary CSV.
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIPS = f"{ROOT}/videos/counter-generated"
OUT = f"{ROOT}/outputs/counter-generated"
ZONES = f"{ROOT}/outputs/top-side-view/top-side-view_zones.json"

# the numbers run_counter prints at the end, which are the point of the batch
PAT = {
    "persons_mean": r"persons/frame: mean=([\d.]+)",
    "persons_max": r"persons/frame: mean=[\d.]+ max=(\d+)",
    "ids_seen": r"ids seen=(\d+)",
    "till_frames": r"till detected in (\d+)/",
    "staff": r"roles: staff=(\d+)",
    "customer": r"customer=(\d+) unresolved",
    "unresolved": r"unresolved=(\d+)",
    "fps": r"\(([\d.]+) fps\)",
}


def grab(pat, text, cast=float):
    m = re.search(pat, text)
    return cast(m.group(1)) if m else None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, help="only the first N clips per side")
    p.add_argument("--no-video", action="store_true",
                   help="discard the annotated mp4, keep events only")
    a = p.parse_args()

    os.makedirs(f"{OUT}/annotated", exist_ok=True)
    os.makedirs(f"{OUT}/events", exist_ok=True)

    jobs = []
    for side in ("Left", "Right"):
        vids = sorted(f"{CLIPS}/{side}/{v}" for v in os.listdir(f"{CLIPS}/{side}")
                      if v.endswith(".mp4"))
        jobs += [(side, v) for v in (vids[:a.limit] if a.limit else vids)]

    print(f"{len(jobs)} clips, zones={os.path.basename(ZONES)}\n")
    rows, t0 = [], time.perf_counter()
    for i, (side, video) in enumerate(jobs, 1):
        stem = os.path.splitext(os.path.basename(video))[0]
        tag = f"{stem}{side}"
        out_v = f"{OUT}/annotated/{tag}.mp4"
        out_e = f"{OUT}/events/{tag}.jsonl"
        cmd = [sys.executable, f"{ROOT}/scripts/run_counter.py",
               "--video", video, "--zones", ZONES,
               "--out", out_v, "--events", out_e]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"[{i:2d}/{len(jobs)}] {tag}  FAILED\n{r.stderr[-500:]}")
            continue
        txt = r.stdout
        row = {"clip": stem, "side": side}
        for k, pat in PAT.items():
            row[k] = grab(pat, txt)

        ev = [json.loads(l) for l in open(out_e)]
        opens = [e for e in ev if e.get("event") == "TILL_STATE"
                 and e["state"] == "Open"]
        row["till_open_events"] = len(opens)
        row["first_open_t"] = opens[0]["t"] if opens else None
        row["open_till_idx"] = opens[0]["till"] if opens else None
        row["n_track_new"] = sum(1 for e in ev if e["event"] == "TRACK_NEW")
        if a.no_video and os.path.exists(out_v):
            os.remove(out_v)
        rows.append(row)
        print(f"[{i:2d}/{len(jobs)}] {tag}  staff={row['staff']:.0f} "
              f"cust={row['customer']:.0f} unres={row['unresolved']:.0f}  "
              f"ids={row['ids_seen']:.0f}  "
              f"open@{row['first_open_t']}s till{row['open_till_idx']}",
              flush=True)

    csv_path = f"{OUT}/batch_summary.csv"
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    wall = time.perf_counter() - t0
    print(f"\n{len(rows)}/{len(jobs)} clips in {wall/60:.1f} min")
    print(f"saved {csv_path}")

    def col(k):
        return [r[k] for r in rows if r[k] is not None]

    n_cust = sum(1 for r in rows if r["customer"] and r["customer"] > 0)
    n_unres = sum(1 for r in rows if r["unresolved"] and r["unresolved"] > 0)
    print(f"\nclips with a customer present : {n_cust}/{len(rows)}")
    print(f"clips with an unresolved role : {n_unres}/{len(rows)}")
    print(f"staff per clip                : "
          f"{min(col('staff')):.0f}-{max(col('staff')):.0f}")
    print(f"ids seen per clip             : "
          f"{min(col('ids_seen')):.0f}-{max(col('ids_seen')):.0f}")
    no_open = [r["clip"] for r in rows if not r["till_open_events"]]
    print(f"clips where no till ever opened: {len(no_open)}"
          + (f"  {no_open}" if no_open else ""))
    for side in ("Left", "Right"):
        sub = [r for r in rows if r["side"] == side]
        idx = {}
        for r in sub:
            idx[r["open_till_idx"]] = idx.get(r["open_till_idx"], 0) + 1
        print(f"{side:<6} open-till index distribution: {idx}")


if __name__ == "__main__":
    main()
