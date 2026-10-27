#!/usr/bin/env python3
"""
review_labels.py - check the labels export_labels.py wrote, before uploading them.

Three things, in the order you want them:

  --stats    how many boxes, how big, which families look wrong
  --sheets   contact sheets with the boxes drawn FROM THE TXT, so you are
             judging the numbers that will be uploaded rather than the picture
             ai_triage drew earlier
  --drop     delete the labels that fail a test, so those frames upload bare
             and get drawn by hand instead of arriving with a bad box

WHY THE DROP MATTERS. A pre-drawn box anchors the annotator: people nudge a
loose box instead of redrawing it, so a bad pre-annotation ends up in the
dataset where no box at all would have produced a good one. Measured on the
first 1,840 labels, 16.8 percent of boxes covered more than a quarter of the
frame and the worst covered 99.9 percent — those were not tight close-ups but
boxes that had swallowed the hands, the table and the controller.

    ./review_labels.py --stats
    ./review_labels.py --sheets 40                 # look at them
    ./review_labels.py --drop --max-area 0.35      # dry run
    ./review_labels.py --drop --max-area 0.35 --apply
"""
from __future__ import annotations

import argparse
import collections
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILTERED = HERE / "filtered" / "AI Filtered"
SHEETS = HERE / "label_review"


def boxes_of(p: Path):
    out = []
    for line in p.read_text().splitlines():
        parts = line.split()
        if len(parts) == 5:
            out.append(tuple(float(x) for x in parts[1:]))
    return out


def collect(families):
    """[(label_path, image_path, [boxes])]"""
    out = []
    for cat in sorted(x for x in FILTERED.iterdir() if x.is_dir()):
        if families and cat.name not in families:
            continue
        for lab in sorted(cat.rglob("labels/*.txt")):
            img = lab.parent.parent / "images" / f"{lab.stem}.jpg"
            if img.exists():
                out.append((lab, img, boxes_of(lab)))
    return out


def cmd_stats(items):
    per = collections.defaultdict(lambda: {"files": 0, "boxes": 0, "big": 0, "areas": []})
    for lab, _img, bs in items:
        fam = lab.parent.parent.parent.name
        band = lab.parent.parent.name
        for key in (fam, f"  {band}"):
            e = per[key]
            e["files"] += 1
            e["boxes"] += len(bs)
            for _cx, _cy, w, h in bs:
                e["areas"].append(w * h)
                if w * h > 0.25:
                    e["big"] += 1
    print(f"{'':<32}{'files':>8}{'boxes':>8}{'median':>9}{'>25%':>8}")
    print("-" * 68)
    for k in sorted(per, key=lambda s: (s.strip(), s.startswith(" "))):
        e = per[k]
        if not e["areas"]:
            continue
        a = sorted(e["areas"])
        med = a[len(a) // 2] * 100
        print(f"{k:<32}{e['files']:>8,}{e['boxes']:>8,}{med:>8.2f}%"
              f"{100*e['big']/len(a):>7.1f}%")
    allp = sorted(x for _l, _i, bs in items for _cx, _cy, w, h in bs for x in [w * h])
    if allp:
        print("-" * 68)
        q = lambda f: allp[min(int(len(allp) * f), len(allp) - 1)] * 100
        print(f"all boxes: {len(allp):,}   "
              f"5th {q(.05):.2f}%  median {q(.5):.2f}%  "
              f"95th {q(.95):.2f}%  99th {q(.99):.2f}%")


def cmd_sheets(items, n, max_area):
    from PIL import Image, ImageDraw
    SHEETS.mkdir(exist_ok=True)
    groups = {
        "biggest": sorted((x for x in items if x[2]),
                          key=lambda x: -max(w * h for _c, _d, w, h in x[2]))[:n],
        "multi_box": [x for x in items if len(x[2]) > 1][:n],
        "random": random.sample([x for x in items if x[2]],
                                min(n, sum(1 for x in items if x[2]))),
    }
    W, H = 400, 225
    for name, rows in groups.items():
        if not rows:
            continue
        cols = 3
        r = (len(rows) + cols - 1) // cols
        sheet = Image.new("RGB", (W * cols, (H + 18) * r), "white")
        d0 = ImageDraw.Draw(sheet)
        for i, (lab, img, bs) in enumerate(rows):
            im = Image.open(img).convert("RGB")
            iw, ih = im.size
            dr = ImageDraw.Draw(im)
            worst = 0
            for cx, cy, w, h in bs:
                worst = max(worst, w * h)
                col = (255, 60, 60) if w * h > max_area else (0, 230, 0)
                dr.rectangle([(cx - w / 2) * iw, (cy - h / 2) * ih,
                              (cx + w / 2) * iw, (cy + h / 2) * ih],
                             outline=col, width=max(3, iw // 300))
            x, y = (i % cols) * W, (i // cols) * (H + 18)
            sheet.paste(im.resize((W, H)), (x, y + 18))
            d0.text((x + 3, y + 4),
                    f"{worst*100:.0f}%  {len(bs)} box  {lab.stem[:44]}", fill="black")
        out = SHEETS / f"{name}.jpg"
        sheet.save(out, quality=88)
        print(f"  {out}  ({len(rows)} frames)")
    print(f"\nred box = over {max_area*100:.0f}% of the frame, green = under")


def cmd_drop(items, max_area, max_boxes, apply):
    doomed = []
    for lab, _img, bs in items:
        if not bs:
            continue
        why = None
        if any(w * h > max_area for _c, _d, w, h in bs):
            why = f"box covers >{max_area*100:.0f}%"
        elif len(bs) > max_boxes:
            why = f"{len(bs)} boxes"
        if why:
            doomed.append((lab, why))
    by = collections.Counter(w for _l, w in doomed)
    print(f"{len(doomed):,} of {sum(1 for x in items if x[2]):,} labels fail:")
    for w, n in by.most_common():
        print(f"    {n:>6,}  {w}")
    print("\nthose frames keep their image and lose only the pre-drawn box, so "
          "they\nupload bare and go into the annotation queue to be drawn by hand.")
    if not apply:
        print(f"\nDRY RUN — nothing deleted. Re-run with --apply.")
        return
    for lab, _ in doomed:
        lab.unlink(missing_ok=True)
    print(f"\ndeleted {len(doomed):,} label files.")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n")[1],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--families", default="")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--sheets", type=int, default=0, metavar="N",
                    help="write contact sheets of N frames per group")
    ap.add_argument("--drop", action="store_true")
    ap.add_argument("--max-area", type=float, default=0.35,
                    help="a box bigger than this share of the frame is suspect")
    ap.add_argument("--max-boxes", type=int, default=6)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    fams = {f.strip() for f in args.families.split(",") if f.strip()}
    items = collect(fams)
    if not items:
        sys.exit("no labels found — run export_labels.py first")
    print(f"{len(items):,} label files, "
          f"{sum(len(b) for _l, _i, b in items):,} boxes\n")

    if args.stats or not (args.sheets or args.drop):
        cmd_stats(items)
    if args.sheets:
        cmd_sheets(items, args.sheets, args.max_area)
    if args.drop:
        cmd_drop(items, args.max_area, args.max_boxes, args.apply)


if __name__ == "__main__":
    main()
