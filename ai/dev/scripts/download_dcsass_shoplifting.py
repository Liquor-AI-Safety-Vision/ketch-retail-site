"""Download the labelled Shoplifting clips of DCSASS (UCF-Crime repack) from HuggingFace.

Only the Shoplifting class, and only the clips that appear in Shoplifting.csv - the repo also
carries ~450 loose clips in Normal/normal/shoplifting/"see and let" folders that have no label
row, inconsistent naming and unclear provenance, so they are skipped.

Each clip: 4s, 320x240, 30fps, h264, real store CCTV. Labels are clip-level (0 normal / 1 theft).

    python3 scripts/download_dcsass_shoplifting.py            # download everything missing
    python3 scripts/download_dcsass_shoplifting.py --workers 12

License: DCSASS is CC BY-NC-SA 4.0 - NonCommercial. Fine for research/evaluation, not for
shipping in a commercial product. For that, pull UCF-Crime itself (CC0 on Kaggle) instead.
"""
from pathlib import Path as _Path
import argparse
import csv
import os
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = str(_Path(__file__).resolve().parents[1])
ROOT = str(_Path(__file__).resolve().parents[3])
OUT_DIR = f"{ROOT}/data/datasets/DCSASS_Shoplifting"
BASE = ("https://huggingface.co/datasets/Centrique/tcc-shoplifting/"
        "resolve/main/DCSASS_Dataset")
CSV_URL = f"{BASE}/Shoplifting.csv"


def fetch(url, dest, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    tmp = dest + ".part"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, dest)          # never leave a half-written mp4 that a later run would skip
    return len(data)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, help="stop after N clips (for a quick trial)")
    a = ap.parse_args()

    os.makedirs(f"{OUT_DIR}/clips", exist_ok=True)

    csv_path = f"{OUT_DIR}/Shoplifting.csv"
    if not os.path.exists(csv_path):
        print(f"fetching label CSV -> {csv_path}")
        fetch(CSV_URL, csv_path)

    rows = [r for r in csv.reader(open(csv_path)) if len(r) >= 3 and r[0].strip()]
    jobs, skipped = [], 0
    for stem, _cls, label in rows:
        stem, label = stem.strip(), label.strip()
        m = re.match(r"(Shoplifting\d+_x264)_\d+$", stem)
        if not m:                   # the loose unlabelled folders (Normal, "see and let", ...)
            skipped += 1
            continue
        url = f"{BASE}/{m.group(1)}.mp4/{stem}.mp4"
        jobs.append((url, f"{OUT_DIR}/clips/{stem}.mp4", label))

    if skipped:
        print(f"skipped {skipped} CSV rows that are not Shoplifting###_x264_N clips")

    todo = [(u, d, l) for u, d, l in jobs if not os.path.exists(d)]
    if a.limit:
        todo = todo[:a.limit]
    have = len(jobs) - len([1 for _, d, _ in jobs if not os.path.exists(d)])
    print(f"{len(jobs)} labelled clips total | already on disk: {have} | downloading: {len(todo)}")

    ok, failed, nbytes = 0, [], 0
    if todo:
        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            futs = {ex.submit(fetch, u, d): (u, d) for u, d, _ in todo}
            for i, f in enumerate(as_completed(futs), 1):
                u, d = futs[f]
                try:
                    nbytes += f.result()
                    ok += 1
                except Exception as e:
                    failed.append((os.path.basename(d), repr(e)))
                if i % 50 == 0 or i == len(todo):
                    print(f"  {i}/{len(todo)}  ok={ok} failed={len(failed)} "
                          f"({nbytes/1e6:.0f} MB)")

    # labels.csv covering only what actually landed on disk, so downstream code can trust it
    present = [(os.path.basename(d)[:-4], l) for _, d, l in jobs if os.path.exists(d)]
    with open(f"{OUT_DIR}/labels.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["clip", "label"])
        w.writerows(sorted(present))

    n_pos = sum(1 for _, l in present if l == "1")
    print(f"\nclips on disk: {len(present)}  ({n_pos} shoplifting / {len(present)-n_pos} normal, "
          f"{100*n_pos/max(len(present),1):.1f}% positive)")
    print(f"wrote {OUT_DIR}/labels.csv")
    if failed:
        print(f"\n{len(failed)} failed (rerun to retry):")
        for name, err in failed[:10]:
            print(f"  {name}: {err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
