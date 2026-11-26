"""Draw named polygon zones on a frame of a video and save them to JSON.

    python3 zone_editor.py --video videos/top-side-view.mp4 --frame 1600

Two shapes, because a boundary is often easier to draw than a region:

  polygon  a closed area - "inside this is the staff floor"
  line     an open polyline dividing the frame in two - "staff is past this"

Left-click adds a vertex. Coordinates are stored in the video's native
resolution regardless of how much the preview window was scaled to fit the
screen, so a zone drawn here is valid for the full-size frames the tracker sees.

Keys
  ENTER / n   close as a POLYGON (needs 3+ vertices) and name it
  l           close as a LINE divider (needs 2+ vertices) and name it
  u           undo the last vertex (or the last zone, if none in progress)
  r           restart the current shape
  s           save and keep editing
  q / ESC     save and quit
"""
import argparse
import json
import os

import cv2
import numpy as np

MAX_W, MAX_H = 1600, 900     # preview window ceiling
PALETTE = [(80, 220, 80), (80, 160, 255), (255, 180, 80),
           (200, 120, 255), (80, 240, 240), (255, 100, 160)]


def grab_frame(video, index):
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise SystemExit(f"cannot open {video}")
    cap.set(cv2.CAP_PROP_POS_FRAMES, index)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise SystemExit(f"cannot read frame {index}")
    return frame


def render(base, zones, current, scale):
    img = base.copy()
    overlay = img.copy()
    for i, z in enumerate(zones):
        pts = (np.array(z["points"], np.float32) * scale).astype(np.int32)
        c = PALETTE[i % len(PALETTE)]
        if z.get("type", "polygon") == "line":
            cv2.polylines(img, [pts], False, c, 3)
            for p in pts:
                cv2.circle(img, tuple(p), 5, c, -1)
        else:
            cv2.fillPoly(overlay, [pts], c)
            cv2.polylines(img, [pts], True, c, 2)
        cx, cy = pts.mean(axis=0).astype(int)
        label = f"{z['name']} [{z.get('type', 'polygon')}]"
        cv2.putText(img, label, (cx - 40, cy), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (0, 0, 0), 4)
        cv2.putText(img, label, (cx - 40, cy), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, c, 2)
    cv2.addWeighted(overlay, 0.25, img, 0.75, 0, img)

    if current:
        pts = (np.array(current, np.float32) * scale).astype(np.int32)
        if len(pts) > 1:
            cv2.polylines(img, [pts], False, (255, 255, 255), 2)
        for p in pts:
            cv2.circle(img, tuple(p), 4, (255, 255, 255), -1)

    hud = (f"zones:{len(zones)}  vertices:{len(current)}   "
           "click=add  ENTER=polygon  l=LINE  u=undo  r=restart  s=save  q=quit")
    cv2.rectangle(img, (0, 0), (img.shape[1], 26), (0, 0, 0), -1)
    cv2.putText(img, hud, (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                (255, 255, 255), 1)
    return img


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--video", required=True)
    p.add_argument("--frame", type=int, default=0, help="frame index to draw on")
    p.add_argument("--image", help="draw on this image instead of a video frame "
                                   "(e.g. a traffic heatmap); must match the "
                                   "video's resolution, since zones are stored "
                                   "in video coordinates")
    p.add_argument("--out", help="default: <video stem>_zones.json")
    p.add_argument("--names", help="comma-separated names used in order as "
                                   "shapes are closed, instead of prompting. "
                                   "Needed when the editor is launched without "
                                   "a terminal on stdin, where input() would "
                                   "hang after the first shape")
    a = p.parse_args()
    queued = [n for n in (a.names or "").split(",") if n]
    out = a.out or f"{os.path.splitext(os.path.basename(a.video))[0]}_zones.json"

    if a.image:
        frame = cv2.imread(a.image)
        if frame is None:
            raise SystemExit(f"cannot read {a.image}")
        ref = grab_frame(a.video, a.frame)
        if frame.shape[:2] != ref.shape[:2]:
            raise SystemExit(
                f"{a.image} is {frame.shape[1]}x{frame.shape[0]} but "
                f"{a.video} is {ref.shape[1]}x{ref.shape[0]}; zones would be "
                "saved at the wrong scale")
    else:
        frame = grab_frame(a.video, a.frame)
    H, W = frame.shape[:2]
    scale = min(MAX_W / W, MAX_H / H, 1.0)
    base = cv2.resize(frame, (int(W * scale), int(H * scale)))

    zones, current = [], []
    if os.path.exists(out):
        zones = json.load(open(out))["zones"]
        print(f"loaded {len(zones)} existing zones from {out}")

    def on_mouse(event, x, y, flags, _):
        if event == cv2.EVENT_LBUTTONDOWN:
            # store in native video coords, not preview coords
            current.append([round(x / scale, 1), round(y / scale, 1)])

    win = "zone editor"
    cv2.namedWindow(win)
    cv2.setMouseCallback(win, on_mouse)
    print(f"frame {a.frame} of {a.video} ({W}x{H}, preview {scale:.2f}x)")

    def save():
        # Points are kept in native pixels while drawing, because that is what
        # the mouse gives and what render() draws, but they are SAVED as
        # fractions of the frame. A zone is a property of where the camera
        # points, not of the sensor it was drawn on -- and a file of pixels
        # used at another resolution is wrong silently, with roles simply
        # coming out on the wrong side and no error to show for it.
        payload = {
            "video": a.video,
            "drawn_on": {"width": W, "height": H},
            "note": ("points_norm is fractions of frame width/height. drawn_on "
                     "is provenance only -- never used to scale."),
            "zones": [{**z, "points_norm": [[round(x / W, 6), round(y / H, 6)]
                                            for x, y in z["points"]],
                       "points": None} for z in zones],
        }
        for z in payload["zones"]:
            del z["points"]
        json.dump(payload, open(out, "w"), indent=2)
        print(f"saved {len(zones)} zones (normalized) -> {out}")

    while True:
        cv2.imshow(win, render(base, zones, current, scale))
        k = cv2.waitKey(20) & 0xFF

        if k in (13, ord("n"), ord("l")):            # ENTER = polygon, l = line
            kind = "line" if k == ord("l") else "polygon"
            need = 2 if kind == "line" else 3
            if len(current) < need:
                print(f"a {kind} needs at least {need} vertices "
                      f"(have {len(current)}) - press 'l' for a 2-point divider")
                continue
            if queued:
                name = queued.pop(0)
            elif a.names:
                name = f"zone{len(zones)}"
            else:
                name = input(f"{kind} name: ").strip() or f"zone{len(zones)}"
            zones.append({"name": name, "type": kind, "points": list(current)})
            current.clear()
            print(f"  added {kind} '{name}' ({len(zones[-1]['points'])} vertices)")
        elif k == ord("u"):
            if current:
                current.pop()
            elif zones:
                print(f"  removed zone '{zones.pop()['name']}'")
        elif k == ord("r"):
            current.clear()
        elif k == ord("s"):
            save()
        elif k in (ord("q"), 27):                    # ESC
            if current:
                # quitting here would silently discard the shape and rewrite the
                # file with only what was loaded at startup - say so, don't guess
                print(f"! {len(current)} vertices are still open and will be LOST. "
                      "Close them first (ENTER / l), or press q again to discard.")
                current.clear()
                continue
            break

    cv2.destroyAllWindows()
    save()


if __name__ == "__main__":
    main()
