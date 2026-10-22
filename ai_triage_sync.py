#!/usr/bin/env python3
"""
ai_triage_sync.py - mirror deletions you made in annotated/ onto images/.

You review the annotated copies (boxes drawn, easy to judge) and delete the frames you do
not want. This walks the manifest and removes the matching file from images/ wherever its
annotated twin is gone, so the two folders stay in step.

SAFE BY DEFAULT: prints what it would delete and stops. Pass --apply to actually delete.

It also refuses to run if an ENTIRE band's annotated folder is empty, because that is far
more likely to mean "I deleted the wrong folder" or "the viewer moved them" than "I rejected
every single frame". Override with --allow-empty if you really did reject all of them.

Usage:
  ./ai_triage_sync.py --out "filtered/AI Filtered v2"            # dry run, shows the plan
  ./ai_triage_sync.py --out "filtered/AI Filtered v2" --apply    # do it
"""

import argparse
import csv
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="the triage output folder")
    ap.add_argument("--apply", action="store_true", help="actually delete (default is a dry run)")
    ap.add_argument("--allow-empty", action="store_true",
                    help="permit syncing a band whose annotated/ folder is now completely empty")
    ap.add_argument("--bands", default="",
                    help="only these bands, comma separated (a name or just its "
                         "leading number, e.g. '3' or '1,2'). Use it when one "
                         "band's review is finished and the others are still "
                         "being worked on or are still to arrive — syncing a "
                         "band against a stale annotated/ deletes images the "
                         "newer review would have kept.")
    args = ap.parse_args()

    out = Path(args.out)
    man = out / "MANIFEST.csv"
    if not man.exists():
        sys.exit(f"no MANIFEST.csv in {out} - run ai_triage.py first")

    rows = list(csv.DictReader(open(man)))
    bands = sorted({r["band"] for r in rows})

    if args.bands:
        want = {b.strip() for b in args.bands.split(",") if b.strip()}
        chosen = [b for b in bands
                  if b in want or b.split("_", 1)[0] in want]
        missing = want - {b for b in chosen} - {b.split("_", 1)[0] for b in chosen}
        if missing:
            sys.exit(f"no such band(s): {', '.join(sorted(missing))}\n"
                     f"available: {', '.join(bands)}")
        print(f"bands: {', '.join(chosen)} "
              f"({len(bands) - len(chosen)} other band(s) left untouched)\n")
        bands = chosen
        rows = [r for r in rows if r["band"] in set(bands)]

    # guard: an empty annotated/ almost always means a mistake, not a decision
    for b in bands:
        ann = out / b / "annotated"
        if not ann.is_dir():
            sys.exit(f"missing folder: {ann}")
        if not any(ann.iterdir()) and not args.allow_empty:
            sys.exit(f"\n{ann} is EMPTY.\n"
                     f"That would delete all {sum(1 for r in rows if r['band']==b)} images in "
                     f"{b}/images/.\nIf you really meant to reject every frame in this band, "
                     f"re-run with --allow-empty.")

    # guard: annotated/ holding files that are not in this manifest means the folder belongs
    # to ANOTHER category. A zero-percent match is a mismatch, not a review decision, and
    # reading it as "every frame was rejected" is what destroyed f3 on 2026-10-02: its bands
    # 1-3 held f6 files, so the sync deleted all 11,227 clean images. Never trust counts here;
    # the names are the only thing that identifies the category.
    for b in bands:
        ann = out / b / "annotated"
        names = {p.stem for p in ann.glob("*.jpg")}
        if not names:
            continue
        band_stems = {r["stem"] for r in rows if r["band"] == b}
        hit = len(names & band_stems)
        pct = 100.0 * hit / len(names)
        if hit == 0:
            sample = sorted(names)[:3]
            expect = sorted(band_stems)[:3]
            sys.exit(f"\nREFUSING: not one of the {len(names):,} files in {ann}\n"
                     f"appears in this category's MANIFEST.csv. That folder belongs to a\n"
                     f"different category, and syncing it would delete every image in "
                     f"{b}/images/.\n\n"
                     f"  found in annotated/ : {sample[0] if sample else '-'}\n"
                     f"  manifest expects    : {expect[0] if expect else '-'}\n\n"
                     f"Put the right annotated/ folder in place, then re-run.")
        if pct < 50.0:
            print(f"WARNING  {b}: only {hit:,}/{len(names):,} ({pct:.1f}%) of annotated/ "
                  f"is in this manifest - check the folder is the right one", file=sys.stderr)

    total_keep = total_del = 0
    plan = []
    for b in bands:
        kept = {p.stem for p in (out / b / "annotated").glob("*.jpg")}
        for r in (x for x in rows if x["band"] == b):
            img = out / b / "images" / f"{r['stem']}{r['suffix']}"
            if r["stem"] in kept:
                total_keep += 1
            else:
                if img.exists():
                    plan.append((b, img, r["source_path"]))
                total_del += 1

    print(f"{'band':32}{'keep':>8}{'delete':>8}")
    print("-" * 48)
    for b in bands:
        k = sum(1 for r in rows if r["band"] == b and (out / b / "annotated" / f"{r['stem']}.jpg").exists())
        d = sum(1 for r in rows if r["band"] == b) - k
        print(f"{b:32}{k:>8}{d:>8}")
    print("-" * 48)
    print(f"{'TOTAL':32}{total_keep:>8}{total_del:>8}")

    if not plan:
        print("\nnothing to delete - images/ already matches annotated/")
        return

    if not args.apply:
        print(f"\nDRY RUN. {len(plan)} files would be deleted from images/. Examples:")
        for b, img, _ in plan[:5]:
            print(f"   {b}/images/{img.name}")
        print("\nre-run with --apply to delete them.")
        return

    for _, img, _ in plan:
        img.unlink(missing_ok=True)
    print(f"\ndeleted {len(plan)} files from images/")
    print("the ORIGINAL collected frames under --src were not touched.")


if __name__ == "__main__":
    main()
