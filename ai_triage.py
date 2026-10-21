#!/usr/bin/env python3
"""
ai_triage.py - sort collected frames by what the CURRENT model sees, to make hand-review cheap.

WHAT THIS IS NOT
----------------
This does NOT decide which frames contain a drone. A model cannot be its own ground truth:
if v16 picks the training data for v17, then v17 only ever learns airframes v16 already
knows, and the frames that justify collecting 150 models in the first place - the unfamiliar
silhouettes - get silently discarded. That is the exact failure this tool is designed to
avoid, not to cause.

It is a TRIAGE AID. It sorts 15,000 frames into piles ordered by how much your attention
each one deserves, and draws the model's boxes on a copy so "was it right?" is answered by
looking rather than hunting for a speck.

THE FOUR QUADRANTS
------------------
The model splits the COLUMNS. Your eye decides the ROWS.

                    |  v16 FIRES              |  v16 SILENT
    ----------------+-------------------------+---------------------------
    drone present   |  A: correct detection   |  B: MISS
                    |     low value           |     HIGHEST VALUE
    ----------------+-------------------------+---------------------------
    no drone        |  C: false positive      |  D: correct rejection
                    |     HIGH value as neg   |     ZERO value - delete

    A  keep a SUBSAMPLE as positives. v16 already handles these, so they add airframe
       variety but little else. Every 3rd-5th frame is plenty.
    B  KEEP ALL, label carefully. A drone the model cannot see is direct evidence of a
       gap, and closing those gaps is the entire point of v17.
    C  KEEP, CAPPED at a few hundred to ~1,000. Real false positives are potent training
       signal, but over-weighting hard negatives pushes the model toward under-detecting.
    D  DELETE. The model already gets these right; they teach nothing and cost epochs.

THE CONFIDENCE RULE - read this before harvesting any negative
--------------------------------------------------------------
    Keep a false positive ONLY if BOTH hold:
      1. it fires at or above 0.50  - at or above ANY threshold we might run at
      2. the scene is something a mast-mounted PTZ could really frame

    Band 3 (0.25-0.50) fails test 1 by definition, so BAND 3 IS NOT A NEGATIVE HARVEST.
    It is a hunt for MISSED DRONES. Going through it, ask one question:

        Is there actually a drone in this frame?   No -> delete. Yes -> keep as a positive.

    Do not keep a band-3 false positive because it looks interesting. The operating threshold
    is NOT SETTLED - it may land anywhere from ~0.40 to 0.80 - but it will never be set below
    0.30, so a 0.25 detection can never fire in the field under any value we choose. 0.50 is
    the harvest line precisely because it is at or above every threshold we might pick, so it
    does not go stale when we settle on one. Training on 0.25-level false positives teaches
    the model to suppress at the bottom of its confidence range - which is exactly where faint
    distant drones live. v16 gained +12.6 points at 0-8px and +3.3 at 8-12px over v15; feeding
    it "suppress weak detections" signal is the most direct way to give that back.

    Observed band-3 false positives on f4, all at 0.25, none containing a drone: a bush in a
    field, a picnic table, a human face, a Cancel button, a keyboard, fence posts. None of
    them would fire at any threshold we would set. None is worth keeping.

OUTPUT LAYOUT
-------------
    <out>/1_fires_high_conf70plus/  images/  annotated/
    <out>/2_fires_mid_conf50to70/   images/  annotated/
    <out>/3_fires_low_conf25to50/   images/  annotated/
    <out>/4_silent_no_detection/    images/  annotated/
    <out>/MANIFEST.csv
    <out>/README.md

Filenames lead with the confidence (or MISS), so an alphabetical listing in any image
viewer is already sorted by how much the frame needs you. Review `annotated/`, delete what
you do not want, then run ai_triage_sync.py to mirror those deletions onto `images/`.

Usage:
  ./ai_triage.py --src "filtered/f4_open_frame_fpv (still in process)" \
                 --out "filtered/AI Filtered v2" \
                 --model ../model-testing/models/v16-best.pt
"""

import argparse
import csv
import os
import shutil
import sys
from pathlib import Path

IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")

# (folder name, lower bound inclusive, upper bound exclusive)
BANDS = [
    ("1_fires_high_conf70plus", 0.70, 1.01),
    ("2_fires_mid_conf50to70",  0.50, 0.70),
    ("3_fires_low_conf25to50",  0.00, 0.50),   # anything that fired below 0.50
]
SILENT = "4_silent_no_detection"

README = """# AI Filtered v2 - triage output

Produced by `ai_triage.py`. The model sorted these; it did **not** decide what is in them.

## What to do with each folder

| folder | what the model did | what it probably is | action |
|---|---|---|---|
| `1_fires_high_conf70plus` | confident detection | almost always a real drone (quadrant **A**) | spot-check only. Keep a **subsample** - every 3rd-5th - as positives |
| `2_fires_mid_conf50to70` | moderate detection | mixed | review; split into positives and false positives |
| `3_fires_low_conf25to50` | weak detection | **the ambiguous band** | **hunt for missed drones, do not harvest negatives.** See the confidence rule below |
| `4_silent_no_detection` | saw nothing | mostly empty scenes (**D**), but any drone in here is a **MISS** (**B**) | hunt for drones. Everything else is deletable |

## The four quadrants

|  | model FIRES | model SILENT |
|---|---|---|
| **drone present** | A: correct - low value, subsample | **B: MISS - highest value, keep all** |
| **no drone** | **C: false positive - keep, capped ~1,000** | D: correct - zero value, delete |

**Highest value is B**: a drone the model cannot see. That is a gap, and closing gaps is
why the 150-model corpus exists. Frames the model already detects teach it very little.

**Delete D without hesitation.** It is the bulk of folder 4 and it costs training time for
nothing.

## The confidence rule - apply this to folder 3

Keep a false positive as a negative **only if both** hold:

1. it fires at or above **0.50** - at or above **any** threshold we might run at
2. the scene is something a **mast-mounted PTZ could really frame**

Folder 3 is 0.25-0.50, so it fails test 1 by definition. **Folder 3 is not a negative
harvest - it is a hunt for missed drones.** One question per frame:

> Is there actually a drone in this frame?  **No -> delete. Yes -> keep as a positive.**

Do not keep a band-3 false positive because it looks interesting. The operating threshold is
**not settled** - it may land anywhere from ~0.40 to 0.80 - but it will never be set below
0.30, so a 0.25 detection can never fire in the field under any value we choose. 0.50 is the
harvest line because it is **at or above every threshold we might pick**, which keeps it
correct whatever we settle on. Training on 0.25-level false positives teaches the model to
suppress at the bottom of its confidence range, which is exactly where faint distant drones
live. v16 gained **+12.6 points at 0-8px** over v15; this is the most direct way to hand
that back.

Real band-3 false positives seen on f4, all at 0.25, none with a drone in the box: a bush in
a field, a picnic table, a human face, a Cancel button, a keyboard, fence posts. None would
fire at any threshold we would set.

## Reviewing

Open `annotated/` in any image viewer. Filenames lead with the confidence so the listing is
already ordered by how much attention each frame deserves. Delete what you do not want from
`annotated/` only, then run:

    ./ai_triage_sync.py --out "<this folder>"

That mirrors your deletions onto `images/`, so the two stay in step and nothing is removed
that you did not look at.
"""


def band_for(conf, fired):
    if not fired:
        return SILENT
    for name, lo, hi in BANDS:
        if lo <= conf < hi:
            return name
    return BANDS[-1][0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="folder of collected frames (searched recursively)")
    ap.add_argument("--out", required=True, help="output folder")
    ap.add_argument("--model", default="../model-testing/models/v16-best.pt")
    ap.add_argument("--conf", type=float, default=0.25, help="detection floor; below this counts as silent")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--limit", type=int, default=0, help="stop after N images (0 = all), for a quick trial")
    args = ap.parse_args()

    try:
        import cv2
        from ultralytics import YOLO
    except ModuleNotFoundError as e:
        sys.exit(
            f"missing {e.name}. This project has several venvs and not all of them carry\n"
            f"opencv + ultralytics. Use the project-root one:\n\n"
            f"    \"$(git rev-parse --show-toplevel 2>/dev/null || echo ..)/.venv/bin/python\" "
            f"{sys.argv[0]} ...\n\n"
            f"or just run ./run_ai_triage.sh, which picks the right interpreter for you.")

    src, out = Path(args.src), Path(args.out)
    if not src.is_dir():
        sys.exit(f"source not found: {src}")

    files = sorted(p for p in src.rglob("*") if p.suffix.lower() in IMG_EXTS)
    if args.limit:
        files = files[:args.limit]
    if not files:
        sys.exit(f"no images under {src}")
    print(f"{len(files)} images under {src}")

    for b, _, _ in BANDS:
        (out / b / "images").mkdir(parents=True, exist_ok=True)
        (out / b / "annotated").mkdir(parents=True, exist_ok=True)
    (out / SILENT / "images").mkdir(parents=True, exist_ok=True)
    (out / SILENT / "annotated").mkdir(parents=True, exist_ok=True)

    model = YOLO(args.model)
    rows, counts = [], {}
    for start in range(0, len(files), args.batch):
        chunk = files[start:start + args.batch]
        preds = model.predict([str(p) for p in chunk], conf=args.conf,
                              imgsz=args.imgsz, verbose=False)
        for src_path, res in zip(chunk, preds):
            boxes = res.boxes
            n = 0 if boxes is None else len(boxes)
            best = float(boxes.conf.max()) if n else 0.0
            band = band_for(best, n > 0)
            counts[band] = counts.get(band, 0) + 1

            # model folder kept in the name so provenance survives the flattening
            model_dir = src_path.parent.name
            prefix = "MISS" if n == 0 else f"{best:.2f}"
            stem = f"{prefix}__{model_dir}__{src_path.stem}"

            shutil.copy(src_path, out / band / "images" / f"{stem}{src_path.suffix}")

            im = cv2.imread(str(src_path))
            if im is not None:
                for (x1, y1, x2, y2), c in zip(
                        boxes.xyxy.cpu().numpy() if n else [],
                        boxes.conf.cpu().numpy() if n else []):
                    cv2.rectangle(im, (int(x1), int(y1)), (int(x2), int(y2)), (0, 0, 255), 2)
                    cv2.putText(im, f"{c:.2f}", (int(x1), max(16, int(y1) - 6)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                banner = "NO DETECTION - is there a drone here?" if n == 0 else f"{n} detected"
                cv2.putText(im, banner, (8, 26), cv2.FONT_HERSHEY_SIMPLEX,
                            0.75, (0, 0, 255) if n == 0 else (0, 180, 0), 2)
                cv2.imwrite(str(out / band / "annotated" / f"{stem}.jpg"), im)

            rows.append({"band": band, "stem": stem, "suffix": src_path.suffix,
                         "n_detections": n, "best_conf": round(best, 3),
                         "model_folder": model_dir, "source_path": str(src_path)})

        done = start + len(chunk)
        if done % (args.batch * 20) == 0 or done == len(files):
            print(f"  {done}/{len(files)}", flush=True)

    with open(out / "MANIFEST.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (out / "README.md").write_text(README)

    print(f"\n{'band':32}{'images':>8}{'share':>8}")
    print("-" * 48)
    for b in [x[0] for x in BANDS] + [SILENT]:
        c = counts.get(b, 0)
        print(f"{b:32}{c:>8}{c/len(files):>8.1%}")
    print("-" * 48)
    print(f"{'TOTAL':32}{len(files):>8}")
    print(f"\nwrote {out}/MANIFEST.csv and README.md")
    print("review the annotated/ folders, delete what you do not want, then run ai_triage_sync.py")


if __name__ == "__main__":
    main()
