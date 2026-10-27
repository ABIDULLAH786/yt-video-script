#!/usr/bin/env python3
"""
prelabel_range.py - model-assisted pre-labelling for the measured-distance PTZ capture.

WHAT THIS IS
------------
A first pass that proposes one box per frame so a human reviews and corrects rather than
draws from scratch. It does NOT produce finished labels. Everything it writes is a
proposal; REVIEW.csv says which proposals to distrust and why.

WHY imgsz 1280 AND NOT 640
--------------------------
These frames are 2592x1944 and the drone is ~26 px across. Ultralytics letterboxes to the
longest side, so at imgsz 640 that 26 px arrives as ~6 px - at or under YOLO11's stride-8
detection floor. Measured on 40 frames of the 50-10 m band:

    imgsz 640  -> 48% of frames detected
    imgsz 1280 -> 95%
    imgsz 1920 -> 95%   (no better, ~4x slower)

So 1280. Deployment runs its own imgsz; this number is for RECALL while labelling, where a
missed drone costs a hand-drawn box and a false positive costs one click to reject.

ONE DRONE PER FRAME
-------------------
The capture is a single drone flown out to measured distance, so each frame holds exactly
one. At imgsz 1280 roughly 60% of frames come back with MORE than one box - the extra
recall brings extra false positives, the effect config.py records as 54 FPs at 640 against
391 at 1920. The tool therefore keeps the HIGHEST-confidence box and records the rest as
rejected, drawing them in orange on the annotated frame so a reviewer can see what was
discarded and overrule it.

REVIEWING A 26 px DRONE IN A 2592 px FRAME
------------------------------------------
It is not visible in a full-frame thumbnail. Every frame therefore also gets a `crops/`
image: the area around the kept box, upscaled, with the box drawn. That is the image to
judge "is this actually a drone?" on. The full annotated frame is for context.

OUTPUT
------
    <out>/<band>/images/      originals, copied, untouched
    <out>/<band>/annotated/   full frame, kept box green, rejected boxes orange
    <out>/<band>/crops/       zoomed crop around the kept box - review on these
    <out>/<band>/labels/      YOLO txt, class 0, one per image (empty file = no proposal)
    <out>/<band>/REVIEW.csv   every frame, its flags and why

FLAGS
    MISS        no detection - needs a hand-drawn box, label file is empty
    LOW_CONF    best box under 0.50
    AMBIGUOUS   runner-up within 0.15 of the best - the tool may have kept the wrong one
    MULTI       more than one detection (informational; extras were rejected)
    LARGE       box over 15% of frame width - implausible past ~30 m, check it
    TINY        box under 8 px - at or below the detection floor, likely noise
    EDGE        box touches the frame border - probably clipped or a partial object

Usage:
  ./prelabel_range.py --src "<...>/PTZ_10-300m_R-2592/50-10 M 2592" --out "<...>/labelled/..."
  ./prelabel_range.py --src <band dir> --out <dir> --limit 40      # smoke test
"""

import argparse
import csv
import os
import shutil
import sys
from pathlib import Path

IMG_EXTS = (".jpg", ".jpeg", ".png")
CLASS_ID = 0                 # single class: drone
AMBIG_MARGIN = 0.15
LOW_CONF = 0.50
LARGE_PCT = 15.0
TINY_PX = 8
CROP_PAD = 6.0               # crop side = box width * this, so the drone sits in context
CROP_OUT = 640               # crops are upscaled to this


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="one band folder of frames")
    ap.add_argument("--out", required=True, help="output root; a <band> folder is made inside")
    ap.add_argument("--model", default="../model-testing/models/v16-best.pt")
    ap.add_argument("--imgsz", type=int, default=1280, help="1280 measured best for recall here")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--batch", type=int, default=4, help="4 fits a 4 GB card at 1280")
    ap.add_argument("--limit", type=int, default=0, help="stop after N frames (0 = all)")
    args = ap.parse_args()

    try:
        import cv2
        from ultralytics import YOLO
    except ModuleNotFoundError as e:
        sys.exit(f"missing {e.name} - use the project-root venv: ../.venv/bin/python")

    src = Path(args.src)
    if not src.is_dir():
        sys.exit(f"source not found: {src}")
    files = sorted(p for p in src.iterdir() if p.suffix.lower() in IMG_EXTS)
    if args.limit:
        files = files[:args.limit]
    if not files:
        sys.exit(f"no images in {src}")

    out = Path(args.out) / src.name
    for d in ("images", "annotated", "crops", "labels"):
        (out / d).mkdir(parents=True, exist_ok=True)

    print(f"{len(files)} frames  <- {src.name}")
    print(f"imgsz {args.imgsz}  conf {args.conf}  batch {args.batch}")
    print(f"out -> {out}\n")

    model = YOLO(args.model)
    rows, counts = [], {}

    def bump(k):
        counts[k] = counts.get(k, 0) + 1

    for start in range(0, len(files), args.batch):
        chunk = files[start:start + args.batch]
        preds = model.predict([str(p) for p in chunk], conf=args.conf,
                              imgsz=args.imgsz, verbose=False)
        for path, res in zip(chunk, preds):
            im = cv2.imread(str(path))
            if im is None:
                continue
            H, W = im.shape[:2]
            b = res.boxes
            n = 0 if b is None else len(b)

            flags = []
            kept = None
            rejected = []
            if n == 0:
                flags.append("MISS")
                bump("MISS")
            else:
                xy = b.xyxy.cpu().numpy()
                cf = b.conf.cpu().numpy()
                order = cf.argsort()[::-1]
                kept = (xy[order[0]], float(cf[order[0]]))
                rejected = [(xy[i], float(cf[i])) for i in order[1:]]
                if n > 1:
                    flags.append("MULTI")
                    bump("MULTI")
                    if float(cf[order[1]]) >= kept[1] - AMBIG_MARGIN:
                        flags.append("AMBIGUOUS")
                        bump("AMBIGUOUS")
                x1, y1, x2, y2 = kept[0]
                bw, bh = x2 - x1, y2 - y1
                if kept[1] < LOW_CONF:
                    flags.append("LOW_CONF"); bump("LOW_CONF")
                if bw / W * 100 > LARGE_PCT:
                    flags.append("LARGE"); bump("LARGE")
                if bw < TINY_PX or bh < TINY_PX:
                    flags.append("TINY"); bump("TINY")
                if x1 <= 1 or y1 <= 1 or x2 >= W - 1 or y2 >= H - 1:
                    flags.append("EDGE"); bump("EDGE")

            stem = path.stem
            shutil.copy(path, out / "images" / path.name)

            # ---- YOLO label: normalised cx cy w h, class 0. Empty file when MISS, so the
            #      count of label files always equals the count of images.
            lbl = out / "labels" / f"{stem}.txt"
            if kept is not None:
                x1, y1, x2, y2 = kept[0]
                cx, cy = (x1 + x2) / 2 / W, (y1 + y2) / 2 / H
                nw, nh = (x2 - x1) / W, (y2 - y1) / H
                lbl.write_text(f"{CLASS_ID} {cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}\n")
            else:
                lbl.write_text("")

            # ---- annotated full frame: kept green, rejected orange
            ann = im.copy()
            for xyxy, c in rejected:
                p1 = (int(xyxy[0]), int(xyxy[1])); p2 = (int(xyxy[2]), int(xyxy[3]))
                cv2.rectangle(ann, p1, p2, (0, 140, 255), 3)
                cv2.putText(ann, f"{c:.2f} rejected", (p1[0], max(22, p1[1] - 8)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 140, 255), 2)
            if kept is not None:
                x1, y1, x2, y2 = (int(v) for v in kept[0])
                cv2.rectangle(ann, (x1, y1), (x2, y2), (0, 200, 0), 3)
                cv2.putText(ann, f"{kept[1]:.2f}", (x1, max(22, y1 - 8)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 200, 0), 2)
            banner = " ".join(flags) if flags else "OK"
            cv2.putText(ann, banner, (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.1,
                        (0, 0, 255) if "MISS" in flags else (0, 200, 0), 3)
            cv2.imwrite(str(out / "annotated" / f"{stem}.jpg"), ann,
                        [cv2.IMWRITE_JPEG_QUALITY, 88])

            # ---- crop: the only view where a 26 px drone is actually judgeable
            if kept is not None:
                x1, y1, x2, y2 = kept[0]
                cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                half = max((x2 - x1), (y2 - y1)) * CROP_PAD / 2
                half = max(half, 48)
                cx1, cy1 = int(max(0, cx - half)), int(max(0, cy - half))
                cx2, cy2 = int(min(W, cx + half)), int(min(H, cy + half))
                crop = im[cy1:cy2, cx1:cx2].copy()
                if crop.size:
                    cv2.rectangle(crop, (int(x1 - cx1), int(y1 - cy1)),
                                  (int(x2 - cx1), int(y2 - cy1)), (0, 200, 0), 1)
                    s = CROP_OUT / max(crop.shape[0], crop.shape[1])
                    crop = cv2.resize(crop, None, fx=s, fy=s, interpolation=cv2.INTER_NEAREST)
                    cv2.putText(crop, f"{kept[1]:.2f}  {int(x2-x1)}px", (8, 26),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 0), 2)
                    cv2.imwrite(str(out / "crops" / f"{stem}.jpg"), crop)

            rows.append({
                "image": path.name,
                "flags": "|".join(flags),
                "n_detections": n,
                "best_conf": round(kept[1], 3) if kept else 0.0,
                "runner_up_conf": round(rejected[0][1], 3) if rejected else "",
                "box_w_px": int(kept[0][2] - kept[0][0]) if kept else "",
                "box_w_pct": round((kept[0][2] - kept[0][0]) / W * 100, 2) if kept else "",
                "needs_hand_box": "yes" if kept is None else "",
            })

        done = start + len(chunk)
        if done % (args.batch * 25) == 0 or done == len(files):
            print(f"  {done}/{len(files)}", flush=True)

    with open(out / "REVIEW.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)

    clean = sum(1 for r in rows if not r["flags"])
    print(f"\n{'':<14}{'frames':>8}{'share':>8}")
    print("-" * 30)
    print(f"{'clean':<14}{clean:>8}{clean/len(rows):>8.0%}")
    for k in sorted(counts, key=lambda x: -counts[x]):
        print(f"{k:<14}{counts[k]:>8}{counts[k]/len(rows):>8.0%}")
    print("-" * 30)
    print(f"{'TOTAL':<14}{len(rows):>8}")
    print(f"\nwrote {out}")
    print("review the crops/ folder first - a 26 px drone is invisible in annotated/")
    print("every label here is a PROPOSAL; REVIEW.csv lists what to check")


if __name__ == "__main__":
    main()
