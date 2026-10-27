#!/usr/bin/env python3
"""
seed_band.py - set a band up for labelling and find where its LAST frame's drone is.

The 2592 bands are one continuous flight flown home, so a band's last frames carry straight
on into the next band's first frames. That makes the next band's frame 1 an anchor: look for
the drone near that position in this band's final frames and you have a seed, instead of
starting a fresh band blind at 8 px.

It creates images/ annotated/ crops/ labels/ (empty labels), then reports what it finds near
the anchor in the last few frames, by detection and by differencing, so the seed can be
chosen on evidence rather than assumed.

Usage:
  ./seed_band.py --band "140-110 M 2592" --next "110-80 M 2592"
"""

import argparse
import glob
import os
import re
import shutil
import sys
from pathlib import Path

import numpy as np

ROOT = ("/home/khive/Downloads/Sampel files/Images/own datasets/Day time/outdoor/"
        "distance-and-resulations based frames")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--band", required=True, help="the band to set up, e.g. '140-110 M 2592'")
    ap.add_argument("--next", dest="nxt", required=True,
                    help="the already-labelled band that FOLLOWS it in time")
    ap.add_argument("--model", default="../model-testing/models/v16-best.pt")
    ap.add_argument("--root", default=ROOT)
    args = ap.parse_args()

    import cv2
    from ultralytics import YOLO

    src = Path(args.root) / "PTZ_10-300m_R-2592" / args.band
    out = Path(args.root) / "labelled" / "PTZ_10-300m_R-2592" / args.band
    nxt = Path(args.root) / "labelled" / "PTZ_10-300m_R-2592" / args.nxt
    if not src.is_dir():
        sys.exit(f"no such band: {src}")

    frames = sorted(src.glob("*.jpg"))
    for d in ("images", "annotated", "crops", "labels"):
        (out / d).mkdir(parents=True, exist_ok=True)
    for p in frames:
        if not (out / "images" / p.name).exists():
            shutil.copy(p, out / "images" / p.name)
        lp = out / "labels" / f"{p.stem}.txt"
        if not lp.exists():
            lp.write_text("")
    print(f"{args.band}: {len(frames)} frames set up")

    # the anchor: frame 1 of the band that follows
    first = sorted(nxt.glob("labels/*.txt"))
    if not first:
        sys.exit(f"{args.nxt} has no labels to anchor from")
    v = first[0].read_text().split()
    if len(v) < 5:
        sys.exit(f"{first[0].name} is empty - cannot anchor")
    probe = cv2.imread(str(frames[0]))
    H, W = probe.shape[:2]
    ax, ay = float(v[1]) * W, float(v[2]) * H
    aw = float(v[3]) * W
    print(f"anchor from {args.nxt} frame 1: x{ax:.0f} y{ay:.0f} {aw:.0f}px\n")

    model = YOLO(args.model)
    n_last = len(frames)
    print(f"{'frame':>6}  {'detection':<34}{'differencing'}")
    print("-" * 78)
    for n in range(n_last - 4, n_last + 1):
        im = cv2.imread(str(frames[n - 1]))
        det = ""
        r = model.predict(im, conf=0.04, imgsz=1280, verbose=False)[0]
        if r.boxes is not None and len(r.boxes):
            xy = r.boxes.xyxy.cpu().numpy(); cf = r.boxes.conf.cpu().numpy()
            c = np.stack([(xy[:, 0] + xy[:, 2]) / 2, (xy[:, 1] + xy[:, 3]) / 2], 1)
            dd = np.hypot(c[:, 0] - ax, c[:, 1] - ay)
            j = int(dd.argmin())
            if dd[j] < 320:
                det = (f"x{c[j,0]:.0f} y{c[j,1]:.0f} {int(xy[j][2]-xy[j][0])}px"
                       f"@{cf[j]:.2f} ({dd[j]:.0f}px off)")
        dif = ""
        R = 170
        x0, y0 = int(max(0, ax - R)), int(max(0, ay - R))
        x1, y1 = int(min(W, ax + R)), int(min(H, ay + R))
        cur = cv2.cvtColor(im[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY).astype(np.float32)
        nbs = [cv2.cvtColor(cv2.imread(str(frames[n + k - 1]))[y0:y1, x0:x1],
                            cv2.COLOR_BGR2GRAY).astype(np.float32)
               for k in (-4, -3, -2) if 1 <= n + k <= n_last]
        if len(nbs) >= 2:
            df = np.abs(np.median(np.stack(nbs), 0) - cur)
            mk = (df > max(4.5, df.std() * 2.3)).astype(np.uint8)
            mk = cv2.morphologyEx(mk, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
            nl, _, st, _ = cv2.connectedComponentsWithStats(mk, 8)
            best = None
            for i in range(1, nl):
                x, y, w, h, a = st[i]
                if a < 4 or w < 3 or w > 30 or h > 18:
                    continue
                d = float(np.hypot(x0 + x + w / 2 - ax, y0 + y + h / 2 - ay))
                if best is None or d < best[0]:
                    best = (d, x0 + x, y0 + y, w, h)
            if best:
                d, x, y, w, h = best
                dif = f"x{x+w//2} y{y+h//2} {w}x{h} ({d:.0f}px off)  --seed {x+w//2},{y+h//2},{w},{h}"
        print(f"{n:>6}  {det:<34}{dif}")


if __name__ == "__main__":
    main()
