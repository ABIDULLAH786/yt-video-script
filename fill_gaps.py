#!/usr/bin/env python3
"""
fill_gaps.py - fill the one- and two-frame holes a trace leaves, each on its own evidence.

A trace loses the drone for a frame here and there: it blinks out of detection, or the
difference against its neighbours cancels because it barely moved. Those holes are
surrounded by known positions, so the drone's whereabouts is tightly constrained - but the
box should still come from the frame itself wherever the frame can supply one.

For each unlabelled frame it interpolates the expected position from the nearest labelled
frame on each side, then takes the first of:

    detection    a model detection within --radius of that point        (best evidence)
    difference   a moving blob within --radius, against the median of
                 neighbouring frames                                    (good evidence)
    interpolated position and size straight from the neighbours         (weakest - review)

The method used is written into REVIEW.csv per frame, so the interpolated ones can be
reviewed first. Frames with no labelled neighbour within --span on both sides are left
alone: those are the ends of a flight-out, not gaps.

Usage:
  ./fill_gaps.py --out "<band dir>" --src "<frames dir>"
  ./fill_gaps.py --out ... --src ... --dry-run
"""

import argparse
import csv
import re
import sys
from pathlib import Path

import numpy as np

CLASS_ID = 0
STANDOFF = 5
CROP_PAD = 6.0
CROP_OUT = 640


def crop_tile(im, xyxy, size=CROP_OUT):
    import cv2
    H, W = im.shape[:2]
    x1, y1, x2, y2 = xyxy
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    half = max(max(x2 - x1, y2 - y1) * CROP_PAD / 2, 40)
    ox, oy = int(max(0, cx - half)), int(max(0, cy - half))
    a = im[oy:int(min(H, cy + half)), ox:int(min(W, cx + half))].copy()
    if a.size == 0:
        return np.zeros((size, size, 3), np.uint8)
    sc = size / max(a.shape[:2])
    a = cv2.resize(a, None, fx=sc, fy=sc, interpolation=cv2.INTER_NEAREST)
    m = 4
    cv2.rectangle(a, (int((x1 - ox) * sc) - m, int((y1 - oy) * sc) - m),
                     (int((x2 - ox) * sc) + m, int((y2 - oy) * sc) + m), (0, 0, 255), 1)
    out = np.zeros((size, size, 3), np.uint8)
    out[:a.shape[0], :a.shape[1]] = a[:size, :size]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--src", required=True)
    ap.add_argument("--model", default="../model-testing/models/v16-best.pt")
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--conf", type=float, default=0.04)
    ap.add_argument("--radius", type=float, default=55.0)
    ap.add_argument("--span", type=int, default=8,
                    help="how far either side to look for a labelled neighbour")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    import cv2
    from ultralytics import YOLO

    out, src = Path(args.out), Path(args.src)
    frames = {}
    for p in sorted(src.glob("*.jpg")):
        m = re.search(r"_(\d+)\.jpg$", p.name)
        if m:
            frames[int(m.group(1))] = p
    if not frames:
        sys.exit(f"no numbered frames in {src}")
    probe = cv2.imread(str(frames[min(frames)]))
    H, W = probe.shape[:2]

    def lab(n):
        if n not in frames:
            return None
        p = out / "labels" / f"{frames[n].stem}.txt"
        try:
            v = p.read_text().split()
        except OSError:
            return None
        if len(v) < 5:
            return None
        return (float(v[1]) * W, float(v[2]) * H, float(v[3]) * W, float(v[4]) * H)

    missing = [n for n in sorted(frames) if lab(n) is None]
    if not missing:
        print("no gaps")
        return
    print(f"{len(missing)} unlabelled frame(s): "
          f"{', '.join(str(n) for n in missing[:40])}{' ...' if len(missing) > 40 else ''}\n")

    model = YOLO(args.model)
    print(f"{'frame':>6}  {'method':<13}{'position':>18}{'size':>9}")
    print("-" * 50)
    done = {}
    for n in missing:
        p = q = None
        for d in range(1, args.span + 1):
            if p is None and lab(n - d):
                p = (n - d,) + lab(n - d)
            if q is None and lab(n + d):
                q = (n + d,) + lab(n + d)
        if p is None or q is None:
            print(f"{n:>6}  no neighbour both sides - left alone")
            continue
        t = (n - p[0]) / (q[0] - p[0])
        ex, ey = p[1] + (q[1] - p[1]) * t, p[2] + (q[2] - p[2]) * t
        ew, eh = (p[3] + q[3]) / 2, (p[4] + q[4]) / 2

        im = cv2.imread(str(frames[n]))
        box = meth = None

        r = model.predict(im, conf=args.conf, imgsz=args.imgsz, verbose=False)[0]
        if r.boxes is not None and len(r.boxes):
            xy = r.boxes.xyxy.cpu().numpy()
            c = np.stack([(xy[:, 0] + xy[:, 2]) / 2, (xy[:, 1] + xy[:, 3]) / 2], 1)
            dd = np.hypot(c[:, 0] - ex, c[:, 1] - ey)
            j = int(dd.argmin())
            # The size window is tight on purpose. A detection much bigger than the drone
            # has been is not a better box, it is the model's floor: below ~15 px it cannot
            # box anything smaller, so at 80 m it returns 17 px for a 9 px smudge with sky
            # inside the box. Reject that and fall through to differencing, which measures
            # the smudge. (At 25 px and up the opposite holds and the model box is better -
            # see the 50-10 m band, where thresholding clipped the props.)
            if dd[j] <= args.radius and ew * 0.6 <= (xy[j][2] - xy[j][0]) <= ew * 1.5:
                box, meth = tuple(float(v) for v in xy[j]), "detection"

        if box is None:
            R = int(args.radius) + 25
            x0, y0 = int(max(0, ex - R)), int(max(0, ey - R))
            x1, y1 = int(min(W, ex + R)), int(min(H, ey + R))
            if x1 - x0 > 20 and y1 - y0 > 20:
                cur = cv2.cvtColor(im[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY).astype(np.float32)
                nbs = [cv2.cvtColor(cv2.imread(str(frames[n + k]))[y0:y1, x0:x1],
                                    cv2.COLOR_BGR2GRAY).astype(np.float32)
                       for k in (-3, -2, 2, 3) if n + k in frames]
                if len(nbs) >= 2:
                    df = np.abs(np.median(np.stack(nbs), 0) - cur)
                    mk = (df > max(7.0, df.std() * 2.6)).astype(np.uint8)
                    mk = cv2.morphologyEx(mk, cv2.MORPH_CLOSE, np.ones((4, 4), np.uint8))
                    nl, _, stt, _ = cv2.connectedComponentsWithStats(mk, 8)
                    best = None
                    for i in range(1, nl):
                        x, y, w, h, a = stt[i]
                        if a < 8 or w < ew * 0.5 or w > ew * 1.8:
                            continue
                        dist = np.hypot(x0 + x + w / 2 - ex, y0 + y + h / 2 - ey)
                        if dist <= args.radius and (best is None or dist < best[0]):
                            best = (dist, x0 + x, y0 + y, w, h)
                    if best:
                        _, bx, by, bw, bh = best
                        box, meth = (float(bx), float(by), float(bx + bw), float(by + bh)), "difference"

        if box is None:
            box = (ex - ew / 2, ey - eh / 2, ex + ew / 2, ey + eh / 2)
            meth = "interpolated"

        x1, y1, x2, y2 = box
        print(f"{n:>6}  {meth:<13}{f'x{(x1+x2)/2:.0f} y{(y1+y2)/2:.0f}':>18}"
              f"{f'{x2-x1:.0f}x{y2-y1:.0f}':>9}")
        done[n] = (box, meth)
        if args.dry_run:
            continue
        stem = frames[n].stem
        (out / "labels" / f"{stem}.txt").write_text(
            f"{CLASS_ID} {(x1+x2)/2/W:.6f} {(y1+y2)/2/H:.6f} "
            f"{(x2-x1)/W:.6f} {(y2-y1)/H:.6f}\n")
        ann = im.copy()
        s = STANDOFF
        cv2.rectangle(ann, (int(x1) - s, int(y1) - s), (int(x2) + s, int(y2) + s), (0, 0, 255), 1)
        cv2.imwrite(str(out / "annotated" / f"{stem}.jpg"), ann, [cv2.IMWRITE_JPEG_QUALITY, 88])
        cv2.imwrite(str(out / "crops" / f"{stem}.jpg"), crop_tile(im, box))

    by_meth = {}
    for _, (_, m) in done.items():
        by_meth[m] = by_meth.get(m, 0) + 1
    print("\n" + "  ".join(f"{k}: {v}" for k, v in sorted(by_meth.items())))
    if not args.dry_run and done:
        rp = out / "REVIEW.csv"
        rows = []
        if rp.exists():
            rows = list(csv.DictReader(open(rp)))
        known = {r["image"] for r in rows} if rows else set()
        flds = list(rows[0]) if rows else ["image", "labelled", "conf", "box_w_px", "note"]
        for n, (box, meth) in done.items():
            nm = frames[n].name
            row = {k: "" for k in flds}
            row.update({"image": nm, "labelled": "yes",
                        "box_w_px": int(box[2] - box[0]), "note": f"gap filled by {meth}"})
            if nm in known:
                for r in rows:
                    if r["image"] == nm:
                        r.update(row)
            else:
                rows.append(row)
        with open(rp, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=flds); w.writeheader(); w.writerows(rows)
        print(f"\nwrote {len(done)} frame(s); REVIEW.csv records the method for each")


if __name__ == "__main__":
    main()
