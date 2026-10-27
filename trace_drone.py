#!/usr/bin/env python3
"""
trace_drone.py - follow the drone across frames where the model cannot see it.

THE PROBLEM THIS SOLVES
-----------------------
Once the drone drops below the skyline it is over trees, rooftops and haze. There is no
longer a dark object on bright sky, so the detector returns nothing at any threshold, and
a dark-pixel search returns the whole treeline. On the 50-10 m band the model goes blind
from file 259 to 269 and again from 278, and those are not frames where the drone is
absent - it is there in every one of them.

WHAT WORKS INSTEAD
------------------
The camera is holding still and the background is static; the drone is the only thing
moving. So compare each frame against the MEDIAN of its neighbours a few frames either
side: the trees cancel, the drone does not. It shows up as a compact patch that is darker
than what the background is at that spot the rest of the time.

Validated against frames where a model label already exists and agreed closely:

    frame   differencing          existing label
    258     cx 851, 33 px wide    cx 851, 33 px
    270     cx 858, 35 px         cx 857, 36 px
    271     cx 859, 36 px         cx 858, 40 px

HOW IT FOLLOWS
--------------
It is seeded from a frame that is already labelled, predicts the next position from the
last two, and searches a window around that prediction - so a blob somewhere else in the
treeline cannot capture it. Where several candidates sit in the window it takes the one
nearest the prediction whose size matches what the drone has been, not the biggest or the
darkest. If nothing plausible is found the frame is left unlabelled and the trace keeps
going on prediction alone for a few frames before giving up.

It writes ONLY the frames in the range asked for. Every other file in the band is left
exactly as it is.

Usage:
  ./trace_drone.py --out "<band dir>" --src "<frames dir>" --from 259 --to 269
  ./trace_drone.py --out ... --src ... --from 278 --to 300 --radius 200
"""

import argparse
import glob
import re
import sys
from pathlib import Path

import numpy as np

CLASS_ID = 0
STANDOFF = 5
CROP_PAD = 6.0
CROP_OUT = 640
NB_OFFSETS = (-3, -2, 2, 3)      # neighbours the median background is built from


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


def read_label(lbl, W, H):
    try:
        v = lbl.read_text().split()
    except OSError:
        return None
    if len(v) < 5:
        return None
    cx, cy, w, h = (float(x) for x in v[1:5])
    return cx * W, cy * H, w * W, h * H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="the labelled band folder")
    ap.add_argument("--src", required=True, help="the original frames folder")
    ap.add_argument("--from", dest="f0", type=int, required=True)
    ap.add_argument("--to", dest="f1", type=int, required=True)
    ap.add_argument("--radius", type=float, default=160.0,
                    help="px searched around the predicted position")
    ap.add_argument("--max-miss", type=int, default=6,
                    help="frames it may fail on before the trace stops")
    ap.add_argument("--expect-width", type=float, default=0.0,
                    help="px: the drone's true width through this stretch. Use it where the "
                         "drone is moving fast - differencing smears the blob along the "
                         "direction of travel, each box comes out a little wider than the "
                         "last, and the running median drifts up with them until the "
                         "oversize guard stops firing. Measured on files 416-425: boxes ran "
                         "40,43,48,48,61 px while the drone is 26-30 px either side.")
    ap.add_argument("--backward", action="store_true",
                    help="walk from --to down to --from. The 2592 bands are the return leg of "
                         "one flight, so a band's LAST frames continue the NEXT band's FIRST "
                         "frames. Starting a fresh band at frame 1 means starting blind; "
                         "starting at its last frame means starting from a known position.")
    ap.add_argument("--seed", default="",
                    help="x,y,w,h to start from, when the band has no labelled frame yet")
    ap.add_argument("--dry-run", action="store_true", help="report, write nothing")
    args = ap.parse_args()

    import cv2

    out, src = Path(args.out), Path(args.src)
    frames = sorted(src.glob("*.jpg"))
    by_num = {}
    for p in frames:
        m = re.search(r"_(\d+)\.jpg$", p.name)
        if m:
            by_num[int(m.group(1))] = p
    if not by_num:
        sys.exit(f"no numbered frames in {src}")

    # seed from the two labelled frames before the range
    probe = cv2.imread(str(by_num[args.f0]))
    H, W = probe.shape[:2]
    step = -1 if args.backward else 1
    start, stop = (args.f1, args.f0) if args.backward else (args.f0, args.f1)
    hist = []
    if args.seed:
        sx, sy, sw, sh = (float(v) for v in args.seed.split(","))
        hist = [(start - step, (sx, sy, sw, sh))]
    else:
        probe_from = start - step
        for n in range(probe_from, probe_from - 8 * step, -step):
            p = out / "labels" / f"{by_num[n].stem}.txt" if n in by_num else None
            if p is None:
                continue
            v = read_label(p, W, H)
            if v:
                hist.append((n, v))
            if len(hist) == 2:
                break
        if not hist:
            sys.exit(f"no labelled frame adjacent to {start} to seed from; pass --seed")
        hist.reverse()
    widths = [h[1][2] for h in hist]
    heights = [h[1][3] for h in hist]
    print(f"seeded from file {hist[-1][0]}: x{hist[-1][1][0]:.0f} y{hist[-1][1][1]:.0f} "
          f"{hist[-1][1][2]:.0f}px\n")

    print(f"{'frame':>6}  {'found at':>22}  {'size':>9}  {'off pred':>9}")
    print("-" * 56)
    written = miss = 0
    for n in range(start, stop + step, step):
        if n not in by_num:
            continue
        # predict
        (ln, (lx, ly, lw, lh)) = hist[-1]
        if len(hist) >= 2:
            (pn, (px_, py_, _, _)) = hist[-2]
            g = max(1, (ln - pn) * step)
            vx, vy = (lx - px_) / g, (ly - py_) / g
        else:
            vx = vy = 0.0
        gap = (n - ln) * step
        ex, ey = lx + vx * gap, ly + vy * gap

        R = int(args.radius)
        x0, y0 = int(max(0, ex - R)), int(max(0, ey - R))
        x1, y1 = int(min(W, ex + R)), int(min(H, ey + R))
        if x1 - x0 < 40 or y1 - y0 < 40:
            print(f"{n:>6}  prediction has left the frame - stopping")
            break
        cur = cv2.cvtColor(cv2.imread(str(by_num[n]))[y0:y1, x0:x1],
                           cv2.COLOR_BGR2GRAY).astype(np.float32)
        nbs = []
        for d in NB_OFFSETS:
            k = n + d
            if k in by_num:
                nbs.append(cv2.cvtColor(cv2.imread(str(by_num[k]))[y0:y1, x0:x1],
                                        cv2.COLOR_BGR2GRAY).astype(np.float32))
        if len(nbs) < 2:
            print(f"{n:>6}  too few neighbours")
            miss += 1
            continue
        med = np.median(np.stack(nbs), 0)
        # ABSOLUTE difference, not signed. Against sky the drone is darker than the
        # background it covers; against foliage and shadowed rooftops it is LIGHTER. A
        # signed test finds it above the skyline and goes blind below it, which is exactly
        # where this tool is needed - it missed files 279-282 over the treeline until the
        # sign was dropped.
        diff = np.abs(med - cur)
        m = (diff > max(9.0, diff.std() * 2.8)).astype(np.uint8)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        nl, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)

        # How far it could plausibly have moved: its recent speed, with headroom, and a
        # floor so a stationary drone can still be picked up. A flat radius let the trace
        # jump 158 px onto a 14 px fragment at file 286 and then run off the frame.
        speed = float(np.hypot(vx, vy))
        reach = max(45.0, min(R, (speed * 2.2 + 35.0) * gap))
        # Size gate off a RUNNING MEDIAN, not the last box. Differencing sometimes returns
        # only part of the airframe: at file 346 it gave 14 px, which set the gate to
        # 8-25 px and then rejected the real 32 px drone at 347, losing the trace right
        # where it turned around. A median over recent frames rides through one bad box.
        ref = (args.expect_width if args.expect_width
               else (float(np.median(widths[-6:])) if widths else lw))
        lo, hi = ref * 0.45, ref * 2.1
        best = None
        for i in range(1, nl):
            x, y, w, h, a = st[i]
            if a < 20 or h < 4 or h > 70 or w < lo or w > hi:
                continue
            bx, by = x0 + x + w / 2, y0 + y + h / 2
            d = float(np.hypot(bx - ex, by - ey))
            if d > reach:
                continue
            if best is None or d < best[0]:
                best = (d, x0 + x, y0 + y, w, h)
        if best is None:
            print(f"{n:>6}  -- not found --")
            miss += 1
            if miss > args.max_miss:
                print(f"\nstopped: {args.max_miss} consecutive misses")
                break
            continue
        miss = 0
        d, bx, by, bw, bh = best
        # A blob much larger than the drone has been has merged with something else - its
        # own motion trail, a shadow on the wall, wind in the foliage beside it. Measured
        # on file 412: 43x37 where the neighbours are ~28x17, with the drone sitting in one
        # corner and the rest of the box empty ground. Re-threshold inside the blob and
        # keep the compact core, so the box holds the drone and not the space beside it.
        refh = (args.expect_width * 0.6 if args.expect_width
                else (float(np.median(heights[-6:])) if heights else lh))
        if bw > ref * 1.5 or bh > refh * 1.8:
            sub = diff[by - y0:by - y0 + bh, bx - x0:bx - x0 + bw]
            if sub.size:
                # Raise the threshold until the core comes back the size the drone has
                # been. A single fixed percentile does not work: on file 412 the 72nd kept
                # the whole merged 43x37 blob and only the 86th cut it to 24x13, which is
                # what the neighbours measure.
                for p in (76, 82, 86, 90, 93):
                    t = float(np.percentile(sub, p))
                    sm = (sub > t).astype(np.uint8)
                    sm = cv2.morphologyEx(sm, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
                    n2, _, s2, _ = cv2.connectedComponentsWithStats(sm, 8)
                    pick = None
                    for i in range(1, n2):
                        x, y, w, h, a = s2[i]
                        if a < 10:
                            continue
                        if pick is None or a > pick[4]:
                            pick = (x, y, w, h, a)
                    if pick is None:
                        break
                    x, y, w, h, _ = pick
                    if w <= ref * 1.45 and h <= refh * 1.7:
                        bx, by, bw, bh = bx + x, by + y, w, h
                        break
        box = (float(bx), float(by), float(bx + bw), float(by + bh))
        print(f"{n:>6}  x{box[0]:.0f}-{box[2]:.0f} y{box[1]:.0f}-{box[3]:.0f}"
              f"  {bw:>3.0f}x{bh:<3.0f}  {d:>7.0f}px")
        hist.append((n, ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2, bw, bh)))
        hist = hist[-2:]
        widths.append(float(bw)); widths = widths[-6:]
        heights.append(float(bh)); heights = heights[-6:]

        if args.dry_run:
            continue
        im = cv2.imread(str(by_num[n]))
        stem = by_num[n].stem
        (out / "labels" / f"{stem}.txt").write_text(
            f"{CLASS_ID} {(box[0]+box[2])/2/W:.6f} {(box[1]+box[3])/2/H:.6f} "
            f"{(box[2]-box[0])/W:.6f} {(box[3]-box[1])/H:.6f}\n")
        ann = im.copy()
        s = STANDOFF
        cv2.rectangle(ann, (int(box[0]) - s, int(box[1]) - s),
                           (int(box[2]) + s, int(box[3]) + s), (0, 0, 255), 1)
        cv2.imwrite(str(out / "annotated" / f"{stem}.jpg"), ann,
                    [cv2.IMWRITE_JPEG_QUALITY, 88])
        cv2.imwrite(str(out / "crops" / f"{stem}.jpg"), crop_tile(im, box))
        written += 1

    print(f"\n{written} frame(s) written. Nothing outside "
          f"{args.f0}-{args.f1} was touched.")


if __name__ == "__main__":
    main()
