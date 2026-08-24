"""Turn an approved divider line into the warehouse-floor polygon doc 02 asks for.

Doc 02 section 4 is explicit that the zone must be a polygon covering the whole
warehouse floor, not a narrow line at the door, because a line only fires if the
track is seen on both sides at the moment of crossing - and a doorway is the
worst place to count on that. The half-plane on the inside of the line, clipped
to the frame, is exactly that polygon, so nothing drawn so far is wasted.

Output is `zone_norm` in 0-1 coordinates, the format doc 02 section 8.1 uses.
"""
import argparse
import json
import os

import cv2
import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# points: the divider, already reviewed on the traffic map.
# inside_ref: a pixel that is unambiguously ON THE WAREHOUSE FLOOR.
ROOMS = {
    "storage_1": dict(
        points=[(1425, 0), (1470, 560), (1920, 1080)],
        inside_ref=(600, 700),
        note="camera stands inside the cold storage; the door is on the right "
             "(EMPLOYEES ONLY reads mirrored, so we are behind it)"),
    "storage_2": dict(
        points=[(0, 395), (960, 420), (1920, 465)],
        inside_ref=(960, 900),
        note="the whole corridor is the store room; the far doorway is how you "
             "get in, so the floor near the camera is the inside"),
}


def _on_border(pt, W, H):
    return (pt[0] <= 0.5 or pt[0] >= W - 1.5
            or pt[1] <= 0.5 or pt[1] >= H - 1.5)


def _ray_to_border(origin, direction, W, H):
    """First point where a ray leaves the frame rectangle."""
    if _on_border(origin, W, H):
        return np.asarray(origin, np.float64)   # already there; nothing to extend
    best, d = None, np.asarray(direction, np.float64)
    for denom, num in ((d[0], -origin[0]), (d[0], W - 1 - origin[0]),
                       (d[1], -origin[1]), (d[1], H - 1 - origin[1])):
        if abs(denom) < 1e-9:
            continue
        t = num / denom
        if t <= 1e-9:
            continue
        q = np.asarray(origin, np.float64) + d * t
        if -0.5 <= q[0] <= W - 0.5 and -0.5 <= q[1] <= H - 0.5:
            if best is None or t < best[0]:
                best = (t, q)
    return best[1]


def _border_t(pt, W, H):
    """Position along the frame perimeter, clockwise from the top-left corner."""
    x, y = float(pt[0]), float(pt[1])
    if y <= 0.5:
        return x
    if x >= W - 1.5:
        return (W - 1) + y
    if y >= H - 1.5:
        return (W - 1) + (H - 1) + ((W - 1) - x)
    return 2 * (W - 1) + (H - 1) + ((H - 1) - y)


def half_plane_polygon(points, inside_ref, W, H):
    """The inside region of a divider, as one simple polygon.

    Built geometrically rather than by rasterising a sign field. The obvious
    shortcut - label each pixel by the sign of its distance to the nearest
    segment - breaks at a bend: in the wedge past the corner the two adjacent
    segments disagree, so the mask comes out ragged and the traced contour
    self-intersects. Walking the frame border to close the shape cannot do that.
    """
    p = np.asarray(points, np.float64)
    a = _ray_to_border(p[0], p[0] - p[1], W, H)      # extend backwards
    b = _ray_to_border(p[-1], p[-1] - p[-2], W, H)   # and forwards
    chain = np.vstack([a, p, b])

    per = 2 * ((W - 1) + (H - 1))
    corners = [(0, 0), (W - 1, 0), (W - 1, H - 1), (0, H - 1)]
    ta, tb = _border_t(a, W, H), _border_t(b, W, H)

    for direction in (+1, -1):
        walk, t = [], tb
        for _ in range(5):
            nxt = None
            for c in corners:
                tc = _border_t(c, W, H)
                step = ((tc - t) * direction) % per
                if 1e-6 < step and (nxt is None or step < nxt[0]):
                    nxt = (step, c, tc)
            if nxt is None or ((ta - t) * direction) % per <= nxt[0]:
                break
            walk.append(nxt[1])
            t = nxt[2]
        poly = np.vstack([chain, np.array(walk, np.float64).reshape(-1, 2)])
        # an endpoint already on the border duplicates itself; zero-length edges
        # make downstream point-in-polygon and area checks unreliable
        keep = [poly[0]] + [q for i, q in enumerate(poly[1:])
                            if np.linalg.norm(q - poly[i]) > 1.0]
        if np.linalg.norm(keep[-1] - keep[0]) <= 1.0:
            keep = keep[:-1]
        poly = np.array(keep)
        if cv2.pointPolygonTest(poly.astype(np.float32),
                                (float(inside_ref[0]), float(inside_ref[1])),
                                False) >= 0:
            return poly.astype(np.float32)
    raise SystemExit("could not close the polygon around inside_ref")


def parse_points(s):
    """'1425,0 1470,560 1700,1080' -> [(1425,0), (1470,560), (1700,1080)]"""
    pts = [tuple(float(v) for v in tok.split(",")) for tok in s.split()]
    if len(pts) < 2 or any(len(p) != 2 for p in pts):
        raise SystemExit("--points needs 2+ 'x,y' pairs separated by spaces")
    return pts


ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
ap.add_argument("--room", help="only rebuild this room (default: all)")
ap.add_argument("--points", help="override the divider, e.g. "
                                 "'1425,0 1470,560 1700,1080'")
ap.add_argument("--inside", help="pixel known to be inside, e.g. '600,700'")
ap.add_argument("--from-editor", metavar="JSON",
                help="take a POLYGON drawn in zone_editor.py and use it as the "
                     "zone directly, instead of deriving one from a divider")
ap.add_argument("--zone-name", help="which zone in the editor file to take")
args = ap.parse_args()

if args.from_editor:
    if not args.room:
        raise SystemExit("--from-editor needs --room")
    data = json.load(open(args.from_editor))
    polys = [z for z in data["zones"] if z.get("type", "polygon") == "polygon"]
    if args.zone_name:
        polys = [z for z in polys if z["name"] == args.zone_name]
    if not polys:
        raise SystemExit(f"{args.from_editor} has no polygon zone"
                         + (f" named {args.zone_name!r}" if args.zone_name else "")
                         + " - draw one with ENTER, not 'l'")
    if len(polys) > 1:
        print(f"  {len(polys)} polygons present, using {polys[0]['name']!r}; "
              "pass --zone-name to choose")
    W, H = data["width"], data["height"]
    pts = np.asarray(polys[0]["points"], np.float64)
    cfg = {}
    if os.path.exists(f"{HERE}/outputs/zones/storage_zones.json"):
        cfg = json.load(open(f"{HERE}/outputs/zones/storage_zones.json"))
    cfg[args.room] = dict(
        zone_norm=[[round(float(x) / W, 4), round(float(y) / H, 4)] for x, y in pts],
        note=f"drawn by hand in zone_editor as {polys[0]['name']!r}")
    json.dump(cfg, open(f"{HERE}/outputs/zones/storage_zones.json", "w"), indent=2)
    print(f"{args.room}: {len(pts)} vertices from {polys[0]['name']!r}, "
          f"covers {cv2.contourArea(pts.astype(np.float32))/(W*H)*100:.0f}% "
          "of the frame")
    raise SystemExit(0)

if args.points or args.inside:
    if not args.room:
        raise SystemExit("--points/--inside need --room")
    if args.points:
        ROOMS[args.room]["points"] = parse_points(args.points)
    if args.inside:
        ROOMS[args.room]["inside_ref"] = parse_points(args.inside + " 0,0")[0]

# rebuilding one room must not drop the others from the config
out_cfg = {}
if os.path.exists(f"{HERE}/outputs/zones/storage_zones.json"):
    out_cfg = json.load(open(f"{HERE}/outputs/zones/storage_zones.json"))

for name, R in ROOMS.items():
    if args.room and name != args.room:
        continue
    cap = cv2.VideoCapture(f"{HERE}/videos/{name}.mp4")
    W, H = int(cap.get(3)), int(cap.get(4))
    cap.release()

    poly = half_plane_polygon(R["points"], R["inside_ref"], W, H)
    norm = [[round(float(x) / W, 4), round(float(y) / H, 4)] for x, y in poly]
    out_cfg[name] = dict(zone_norm=norm, note=R["note"])
    area = cv2.contourArea(poly) / (W * H)
    print(f"{name}: {len(poly)} vertices, covers {area*100:.0f}% of the frame")

    bg = cv2.imread(f"{HERE}/outputs/{name}/{name}_traffic.jpg")
    ov = bg.copy()
    cv2.fillPoly(ov, [poly.astype(np.int32)], (70, 210, 70))
    img = cv2.addWeighted(ov, 0.3, bg, 0.7, 0)
    cv2.polylines(img, [poly.astype(np.int32)], True, (255, 255, 255), 5)
    for tag, pos, col in (("INSIDE warehouse", R["inside_ref"], (90, 240, 90)),):
        cv2.putText(img, tag, (int(pos[0]) - 220, int(pos[1])),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 0, 0), 9)
        cv2.putText(img, tag, (int(pos[0]) - 220, int(pos[1])),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.4, col, 3)
    bar = np.zeros((60, W, 3), np.uint8)
    cv2.putText(bar, f"{name}: {R['note']}", (16, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.85, (235, 235, 235), 2)
    os.makedirs(f"{HERE}/outputs/{name}", exist_ok=True)
    cv2.imwrite(f"{HERE}/outputs/{name}/{name}_zone.jpg",
                cv2.resize(np.vstack([img, bar]), (1500, int(1500 * (H + 60) / W))))

json.dump(out_cfg, open(f"{HERE}/outputs/zones/storage_zones.json", "w"), indent=2)
print(f"wrote storage_zones.json + *_zone.jpg")
