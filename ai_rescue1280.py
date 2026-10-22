#!/usr/bin/env python3
"""
ai_rescue1280.py - second look at the SILENT pile only, at imgsz 1280, with no duplication.

WHY A SEPARATE SCRIPT
---------------------
The obvious way to do a 1280 rescue - point ai_triage.py at 4_silent_no_detection/images/ -
writes a SECOND copy of every silent frame into a rescue folder. On a 40k family that is
~30,000 duplicated files and a mangled double-prefixed filename
("0.87__images__MISS__model__frame.jpg"). This script avoids both.

It re-reads the ORIGINAL source paths from MANIFEST.csv, re-scores only the band-4 rows at
1280, and for the ones that now fire it MOVES the existing 640 copies out of
4_silent_no_detection/ into 5_rescued_1280/, replacing the annotation with the 1280 one.
Frames that stay silent are not touched at all.

Every frame ends up in exactly one band folder, exactly once. MANIFEST.csv stays
authoritative, so ai_triage_sync.py keeps working unchanged.

WHY ONLY THE SILENT PILE
------------------------
Measured on f4's 15,153 frames, 640 and 1280 disagree on 3,381 of them, and the
disagreement is purely about object size:

    detections LOST at 1280 -> median box width 65% of frame (close-ups)
    detections NEW  at 1280 -> median box width 11% of frame (small/distant)

v16 was trained at 640, so doubling the input doubles apparent object size and pushes
frame-filling drones outside the scale it learned. Running 1280 over everything would lose
1,175 good close-up detections to gain 2,206 mostly-weak ones. Running it only where 640
found nothing cannot lose anything - there was no detection to lose - and on f4 it recovered
298 drones at >=0.70 that 640 missed completely.

Usage:
  ./ai_rescue1280.py --out "filtered/AI Filtered/f1_folding_quad"
  ./ai_rescue1280.py --out "..." --batch 2        # if 4 OOMs on a small card
"""

import argparse
import csv
import shutil
import sys
from pathlib import Path

SILENT = "4_silent_no_detection"
RESCUED = "5_rescued_1280"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="a family folder produced by ai_triage.py")
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--batch", type=int, default=4,
                    help="4 fits a 4 GB card at 1280; batch 16 there dies with CUDA OOM")
    ap.add_argument("--model", default="../model-testing/models/v16-best.pt")
    args = ap.parse_args()

    try:
        import cv2
        from ultralytics import YOLO
    except ModuleNotFoundError as e:
        sys.exit(f"missing {e.name} - use the project-root venv: ../.venv/bin/python")

    out = Path(args.out)
    man = out / "MANIFEST.csv"
    if not man.exists():
        sys.exit(f"no MANIFEST.csv in {out} - run the 640 pass first")

    rows = list(csv.DictReader(open(man)))
    todo = [r for r in rows if r["band"] == SILENT]
    if not todo:
        sys.exit(f"nothing in {SILENT} - nothing to rescue")
    print(f"{len(rows)} frames total, {len(todo)} silent at 640 -> re-scoring at {args.imgsz}")

    (out / RESCUED / "images").mkdir(parents=True, exist_ok=True)
    (out / RESCUED / "annotated").mkdir(parents=True, exist_ok=True)

    model = YOLO(args.model)
    by_src = {r["source_path"]: r for r in todo}
    paths = list(by_src)
    moved = 0
    hi = 0

    for start in range(0, len(paths), args.batch):
        chunk = paths[start:start + args.batch]
        preds = model.predict(chunk, conf=args.conf, imgsz=args.imgsz, verbose=False)
        for src_path, res in zip(chunk, preds):
            boxes = res.boxes
            n = 0 if boxes is None else len(boxes)
            if n == 0:
                continue                      # still silent - leave it exactly where it is
            best = float(boxes.conf.max())
            r = by_src[src_path]
            old_img = out / SILENT / "images" / f"{r['stem']}{r['suffix']}"
            old_ann = out / SILENT / "annotated" / f"{r['stem']}.jpg"

            # keep the original stem, just swap the MISS prefix for the 1280 confidence
            new_stem = f"{best:.2f}__{r['stem'].split('__', 1)[1]}"
            new_img = out / RESCUED / "images" / f"{new_stem}{r['suffix']}"

            if old_img.exists():
                shutil.move(str(old_img), str(new_img))   # MOVE, not copy
            old_ann.unlink(missing_ok=True)

            im = cv2.imread(str(new_img if new_img.exists() else src_path))
            if im is not None:
                for (x1, y1, x2, y2), c in zip(boxes.xyxy.cpu().numpy(),
                                               boxes.conf.cpu().numpy()):
                    cv2.rectangle(im, (int(x1), int(y1)), (int(x2), int(y2)), (0, 0, 255), 2)
                    cv2.putText(im, f"{c:.2f}", (int(x1), max(16, int(y1) - 6)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                cv2.putText(im, f"RESCUED at {args.imgsz} - 640 saw nothing",
                            (8, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 140, 255), 2)
                cv2.imwrite(str(out / RESCUED / "annotated" / f"{new_stem}.jpg"), im)

            r["band"] = RESCUED
            r["stem"] = new_stem
            r["n_detections"] = n
            r["best_conf"] = round(best, 3)
            moved += 1
            hi += best >= 0.70

        done = start + len(chunk)
        if done % (args.batch * 50) == 0 or done == len(paths):
            print(f"  {done}/{len(paths)}   rescued so far: {moved}", flush=True)

    with open(man, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    still = len(todo) - moved
    print(f"\nrescued {moved} of {len(todo)} silent frames  ({moved/len(todo):.1%})")
    print(f"  of those, {hi} at conf >= 0.70  <- drones 640 could not see at all")
    print(f"  {still} stayed silent at both sizes and were left in {SILENT}/")
    print(f"\nreview: {out}/{RESCUED}/annotated/")
    print("no frame was duplicated - each one lives in exactly one band folder.")


if __name__ == "__main__":
    main()
