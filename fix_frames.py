#!/usr/bin/env python3
"""
fix_frames.py - correct NAMED frames in a labelled band. Touches nothing else.

WHY THIS EXISTS
---------------
`track_range.py emit` rewrites a whole band. That is correct when first labelling it and
wrong for a correction: a review that reports two bad frames should cost two frames of
rework, not a re-review of the entire band. Re-emitting also changes frames that were
already approved, which silently discards the reviewer's work.

This writes only the frames named on the command line. Every other file in the band -
image, annotated, crop, label - is left exactly as it is, byte for byte.

WHAT IT DOES
  --near X,Y     re-detect that frame and keep the detection nearest X,Y.
                 Use it when the box sits on the wrong object: give roughly where the
                 drone actually is and it picks that one.
  --clear        delete the box: the frame has no drone. The label becomes empty.
  --box X1,Y1,X2,Y2  set an exact box, for when no detection lands on the drone at all.

REVIEW.csv is updated in place for the named frames; the rest of its rows are untouched.

Usage:
  ./fix_frames.py --out "<band dir>" --frames 56,57 --near 2060,423
  ./fix_frames.py --out "<band dir>" --frames 214 --clear
  ./fix_frames.py --out "<band dir>" --frames 91 --box 2040,410,2070,432
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
    ap.add_argument("--out", required=True, help="the labelled band folder")
    ap.add_argument("--frames", required=True, help="file numbers, e.g. 56,57 or 53-60")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--near", help="X,Y - keep the detection nearest this point")
    g.add_argument("--clear", action="store_true", help="no drone here; empty the label")
    g.add_argument("--box", help="X1,Y1,X2,Y2 - set this exact box")
    ap.add_argument("--model", default="../model-testing/models/v16-best.pt")
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--conf", type=float, default=0.10,
                    help="low, because a frame being corrected is one the model found hard")
    ap.add_argument("--radius", type=float, default=120.0,
                    help="px: how far from --near a detection may be and still be taken")
    args = ap.parse_args()

    import cv2

    out = Path(args.out)
    if not (out / "labels").is_dir():
        sys.exit(f"not a labelled band folder: {out}")

    want = []
    for part in args.frames.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            want += list(range(int(a), int(b) + 1))
        else:
            want.append(int(part))

    imgs = sorted((out / "images").glob("*.jpg"))
    by_num = {}
    for p in imgs:
        m = re.search(r"_(\d+)\.jpg$", p.name)
        if m:
            by_num[int(m.group(1))] = p

    near = tuple(float(v) for v in args.near.split(",")) if args.near else None
    fixed_box = tuple(float(v) for v in args.box.split(",")) if args.box else None
    model = None
    if near is not None:
        from ultralytics import YOLO
        model = YOLO(args.model)

    changes = {}
    for n in want:
        p = by_num.get(n)
        if p is None:
            print(f"  {n:05d}  NOT IN THIS BAND - skipped")
            continue
        im = cv2.imread(str(p))
        H, W = im.shape[:2]
        stem = p.stem
        lbl = out / "labels" / f"{stem}.txt"
        ann = out / "annotated" / f"{stem}.jpg"
        crp = out / "crops" / f"{stem}.jpg"

        if args.clear:
            lbl.write_text("")
            ann.unlink(missing_ok=True)
            crp.unlink(missing_ok=True)
            changes[p.name] = ("no", "", "", "cleared by review")
            print(f"  {n:05d}  cleared")
            continue

        if fixed_box is not None:
            box, cf = fixed_box, 1.0
        else:
            r = model.predict(im, conf=args.conf, imgsz=args.imgsz, verbose=False)[0]
            b = r.boxes
            if b is None or not len(b):
                print(f"  {n:05d}  no detection at conf {args.conf} - use --box to set one")
                continue
            xy = b.xyxy.cpu().numpy()
            cfs = b.conf.cpu().numpy()
            cen = np.stack([(xy[:, 0] + xy[:, 2]) / 2, (xy[:, 1] + xy[:, 3]) / 2], 1)
            d = np.hypot(cen[:, 0] - near[0], cen[:, 1] - near[1])
            j = int(d.argmin())
            if d[j] > args.radius:
                print(f"  {n:05d}  nearest detection is {d[j]:.0f}px away "
                      f"(limit {args.radius:.0f}) - use --box")
                continue
            box, cf = tuple(float(v) for v in xy[j]), float(cfs[j])

        x1, y1, x2, y2 = box
        cx, cy = (x1 + x2) / 2 / W, (y1 + y2) / 2 / H
        lbl.write_text(f"{CLASS_ID} {cx:.6f} {cy:.6f} {(x2-x1)/W:.6f} {(y2-y1)/H:.6f}\n")

        a = im.copy()
        m = STANDOFF
        cv2.rectangle(a, (int(x1) - m, int(y1) - m), (int(x2) + m, int(y2) + m), (0, 0, 255), 1)
        cv2.imwrite(str(ann), a, [cv2.IMWRITE_JPEG_QUALITY, 88])
        cv2.imwrite(str(crp), crop_tile(im, box))

        changes[p.name] = ("yes", round(cf, 3), int(x2 - x1), "corrected by review")
        print(f"  {n:05d}  box set at x{(x1+x2)/2:.0f} y{(y1+y2)/2:.0f}  {int(x2-x1)}px  conf {cf:.2f}")

    # REVIEW.csv: rewrite only the rows we changed
    rp = out / "REVIEW.csv"
    if rp.exists() and changes:
        rows = list(csv.DictReader(open(rp)))
        flds = list(rows[0])
        for r in rows:
            if r["image"] in changes:
                lab, cf, w, note = changes[r["image"]]
                r["labelled"] = lab
                r["conf"] = cf
                if "box_w_px" in r:
                    r["box_w_px"] = w
                r["note"] = note
        with open(rp, "w", newline="") as fh:
            wtr = csv.DictWriter(fh, fieldnames=flds); wtr.writeheader(); wtr.writerows(rows)

    print(f"\n{len(changes)} frame(s) changed. Every other frame in the band is untouched.")


if __name__ == "__main__":
    main()
