#!/usr/bin/env python3
"""
export_labels.py - write a labels/ folder beside each band's images/ and annotated/.

Runs the current model over the CLEAN frames and saves what it finds as YOLO
label files, so the frames can be uploaded for annotation already boxed instead
of paying a foundation model to re-discover detections we already have.

    <band>/images/      the clean frames, what gets uploaded
    <band>/annotated/   the same frames with boxes burned in — review aid only
    <band>/labels/      written here: "0 cx cy w h", normalised, one line per box

WHY NOT JUST READ annotated/. Those boxes are pixels, not numbers. ai_triage.py
computes the coordinates, draws them and throws them away, so the only way to
get them back is to run the model again. That is free and local — about twenty
minutes on the GTX 1650 — against paying per image for a cloud model.

A FRAME WITH NO DETECTION GETS NO FILE, NOT AN EMPTY ONE. The difference
matters. An empty .txt is a positive claim — "I looked, there is nothing here" —
and Roboflow reads it as annotated-with-no-objects, which takes the image out of
the labelling queue. Band 4 holds 3,832 frames kept precisely because a human
saw a drone the model missed; an empty label would assert the opposite, bake a
false negative into the ground truth, and guarantee nobody ever labels them.
Frames with no file stay unannotated and get queued, which is the truth.

    ./export_labels.py --plan                    # counts, no model loaded
    ./export_labels.py --families f1_folding_quad
    ./export_labels.py                           # everything
    ./export_labels.py --empty-labels            # also write empty files (see above)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILTERED = HERE / "filtered" / "AI Filtered"
MODEL = HERE.parent / "model-testing" / "models" / "v16-best.pt"
BANDS = ["1_fires_high_conf70plus", "2_fires_mid_conf50to70",
         "3_fires_low_conf25to50", "4_silent_no_detection"]


def bands_of(cat: Path):
    for b in BANDS:
        if (cat / b / "images").is_dir():
            yield cat / b


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n")[1],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--families", default="", help="comma separated; default all")
    ap.add_argument("--bands", default="",
                    help="comma separated band names or leading numbers; "
                         "default all four")
    ap.add_argument("--model", default=str(MODEL))
    ap.add_argument("--conf", type=float, default=0.25,
                    help="same floor ai_triage used, so the labels match the "
                         "boxes drawn on the annotated copies")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--device", default="")
    ap.add_argument("--empty-labels", action="store_true",
                    help="write an empty .txt for frames with no detection. "
                         "Marks them as 'no objects here', which removes them "
                         "from the annotation queue — read the note above.")
    ap.add_argument("--overwrite", action="store_true",
                    help="redo frames that already have a label file")
    ap.add_argument("--plan", action="store_true", help="counts only, no model")
    args = ap.parse_args()

    fams = {f.strip() for f in args.families.split(",") if f.strip()}
    want_bands = {b.strip() for b in args.bands.split(",") if b.strip()}

    targets = []
    for cat in sorted(p for p in FILTERED.iterdir() if p.is_dir()):
        if fams and cat.name not in fams:
            continue
        for band in bands_of(cat):
            if want_bands and band.name not in want_bands \
                    and band.name.split("_", 1)[0] not in want_bands:
                continue
            targets.append(band)
    if not targets:
        sys.exit("nothing matched — check --families / --bands")

    todo = []
    for band in targets:
        lab = band / "labels"
        for img in sorted((band / "images").glob("*.jpg")):
            if not args.overwrite and (lab / f"{img.stem}.txt").exists():
                continue
            todo.append((band, img))

    if args.plan:
        import collections
        per = collections.Counter(b.parent.name for b, _ in todo)
        byband = collections.Counter(b.name for b, _ in todo)
        print(f"{'family':<30}{'frames':>9}")
        print("-" * 40)
        for k in sorted(per):
            print(f"{k:<30}{per[k]:>9,}")
        print("-" * 40)
        print(f"{'TOTAL':<30}{len(todo):>9,}\n")
        for k in BANDS:
            if byband.get(k):
                note = ("-> expect boxes" if k != "4_silent_no_detection"
                        else "-> expect NO boxes; no file is written unless --empty-labels")
                print(f"  {k:<28}{byband[k]:>7,}  {note}")
        return

    from ultralytics import YOLO
    model = YOLO(args.model)
    print(f"model {Path(args.model).name}, conf {args.conf}, imgsz {args.imgsz}")
    print(f"{len(todo):,} frames to label\n")

    written = empty = boxes = 0
    for i in range(0, len(todo), args.batch):
        chunk = todo[i:i + args.batch]
        res = model.predict([str(p) for _, p in chunk], conf=args.conf,
                            imgsz=args.imgsz, verbose=False,
                            **({"device": args.device} if args.device else {}))
        for (band, img), r in zip(chunk, res):
            lab = band / "labels"
            lab.mkdir(exist_ok=True)
            lines = []
            if r.boxes is not None and len(r.boxes):
                for xywhn in r.boxes.xywhn.cpu().numpy():
                    cx, cy, w, h = (float(v) for v in xywhn)
                    lines.append(f"0 {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
            if lines:
                (lab / f"{img.stem}.txt").write_text("\n".join(lines) + "\n")
                written += 1
                boxes += len(lines)
            elif args.empty_labels:
                (lab / f"{img.stem}.txt").write_text("")
                written += 1
                empty += 1
            else:
                empty += 1
        done = min(i + args.batch, len(todo))
        if done % (args.batch * 50) == 0 or done == len(todo):
            print(f"  {done:,}/{len(todo):,}", flush=True)

    print(f"\n{written:,} label files written, {boxes:,} boxes")
    print(f"{empty:,} frames had no detection — "
          + ("empty files written" if args.empty_labels
             else "no file written, so they stay in the annotation queue"))


if __name__ == "__main__":
    main()
