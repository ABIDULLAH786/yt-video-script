#!/usr/bin/env python3
"""
rename_frames.py - give the filtered frames short names: f1_b1_00001.jpg.

The collected names carry their whole history —
`0.70__dji_air_3s__dji_air_3s_t0_PX9p7VES3bk_00343.jpg` is confidence, model,
search term, YouTube id and frame number. That is unreadable in an annotation
tool, so this renames them to `<family>_b<band>_<serial>`.

WHAT THAT WOULD THROW AWAY, AND HOW IT IS KEPT. Six scripts read the video id
out of the filename: rebuild_frames (which recovered 34,201 frames after the
f3 sync destroyed them), dedupe_frames, dedupe_categories, video_index,
model_frames and video_registry. A short name has no video id in it, so the
rename writes `rename_map.csv` in each category — new stem, old stem, video id,
model, confidence, band — and moves the old name into MANIFEST.csv as
`orig_stem`. Nothing is lost; it moves from the filename into two files.

images/, annotated/ and labels/ are renamed together, so the three stay
matched. MANIFEST.csv is rewritten in the same pass, because ai_triage_sync.py
looks frames up by stem and would otherwise find none of them — which is the
0 percent match that destroyed f3.

    ./rename_frames.py                       # dry run, shows the mapping
    ./rename_frames.py --families f1_folding_quad
    ./rename_frames.py --apply
    ./rename_frames.py --undo                # back to the original names
"""
from __future__ import annotations

import argparse
import csv
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILTERED = HERE / "filtered" / "AI Filtered"
MAP = "rename_map.csv"
BANDS = ["1_fires_high_conf70plus", "2_fires_mid_conf50to70",
         "3_fires_low_conf25to50", "4_silent_no_detection"]
SUBDIRS = ["images", "annotated", "labels"]

SHORT = {"f1_folding_quad": "f1", "f2_micro_selfie": "f2", "f3_ducted_fpv": "f3",
         "f4_open_frame_fpv": "f4", "f5_enterprise_quad": "f5",
         "f6_heavy_lift": "f6", "f6_heavy_lift_v2": "f6v2"}

OLD_RE = re.compile(
    r"^(?P<conf>[0-9.]+|MISS)__(?P<model>.+?)__(?P=model)"
    r"_t(?P<term>\d+)_(?P<vid>[A-Za-z0-9_-]{11})_(?P<num>\d{5})$")
NEW_RE = re.compile(r"^(?P<fam>f\d+v?\d*)_b(?P<band>\d)_(?P<serial>\d{5})$")


def short_of(cat: str) -> str:
    return SHORT.get(cat) or re.sub(r"[^a-z0-9]", "", cat.split("_")[0])


def plan_category(cat: Path):
    """[(band, old_stem, new_stem, meta)] — serials run per family and band."""
    out = []
    fam = short_of(cat.name)
    for band in BANDS:
        d = cat / band / "images"
        if not d.is_dir():
            continue
        bn = band[0]
        for i, img in enumerate(sorted(d.glob("*.jpg")), 1):
            old = img.stem
            if NEW_RE.match(old):          # already renamed
                continue
            m = OLD_RE.match(old)
            meta = m.groupdict() if m else {"conf": "", "model": "", "term": "",
                                            "vid": "", "num": ""}
            out.append((band, old, f"{fam}_b{bn}_{i:05d}", meta))
    return out


def rewrite_manifest(cat: Path, mapping: dict, apply: bool):
    """Point MANIFEST.csv at the new stems, keeping the old one beside it."""
    man = cat / MANIFEST_NAME
    if not man.exists():
        return 0
    rows = list(csv.DictReader(open(man)))
    if not rows:
        return 0
    hit = 0
    for r in rows:
        new = mapping.get(r["stem"])
        if new:
            r.setdefault("orig_stem", r["stem"])
            if "orig_stem" not in r or not r["orig_stem"]:
                r["orig_stem"] = r["stem"]
            r["orig_stem"] = r.get("orig_stem") or r["stem"]
            r["stem"] = new
            hit += 1
        else:
            r.setdefault("orig_stem", r["stem"])
    if apply:
        cols = list(rows[0].keys())
        if "orig_stem" not in cols:
            cols.insert(cols.index("stem") + 1, "orig_stem")
        tmp = man.with_suffix(".tmp")
        with open(tmp, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
        tmp.replace(man)
    return hit


MANIFEST_NAME = "MANIFEST.csv"


def cmd_undo(cats, apply):
    total = 0
    for cat in cats:
        mp = cat / MAP
        if not mp.exists():
            continue
        rows = list(csv.DictReader(open(mp)))
        back = {r["new_stem"]: r["orig_stem"] for r in rows}
        n = 0
        for band in BANDS:
            for sub in SUBDIRS:
                d = cat / band / sub
                if not d.is_dir():
                    continue
                for f in list(d.iterdir()):
                    if f.stem in back:
                        n += 1
                        if apply:
                            f.rename(d / f"{back[f.stem]}{f.suffix}")
        print(f"  {cat.name}: {n:,} files")
        total += n
        if apply:
            # put MANIFEST back
            man = cat / MANIFEST_NAME
            if man.exists():
                rws = list(csv.DictReader(open(man)))
                for r in rws:
                    if r.get("orig_stem"):
                        r["stem"] = r["orig_stem"]
                cols = [c for c in rws[0].keys() if c != "orig_stem"]
                with open(man, "w", newline="") as fh:
                    w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
                    w.writeheader()
                    w.writerows(rws)
            mp.unlink()
    print(f"\n{'renamed back' if apply else 'DRY RUN — would rename back'} "
          f"{total:,} files")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n")[1],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--families", default="")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--undo", action="store_true",
                    help="restore the original names from rename_map.csv")
    args = ap.parse_args()

    fams = {f.strip() for f in args.families.split(",") if f.strip()}
    cats = [c for c in sorted(FILTERED.iterdir())
            if c.is_dir() and (not fams or c.name in fams)]
    if not cats:
        sys.exit("no categories matched")

    if args.undo:
        cmd_undo(cats, args.apply)
        return

    grand = 0
    for cat in cats:
        rows = plan_category(cat)
        if not rows:
            print(f"{cat.name}: nothing to rename")
            continue
        mapping = {old: new for _b, old, new, _m in rows}
        n_files = 0
        for band, old, new, _m in rows:
            for sub in SUBDIRS:
                for ext in (".jpg", ".txt"):
                    src = cat / band / sub / f"{old}{ext}"
                    if src.exists():
                        n_files += 1
                        if args.apply:
                            src.rename(cat / band / sub / f"{new}{ext}")
        hit = rewrite_manifest(cat, mapping, args.apply)
        if args.apply:
            with open(cat / MAP, "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["new_stem", "orig_stem", "band", "video_id",
                            "model", "conf", "term", "frame_no"])
                for band, old, new, m in rows:
                    w.writerow([new, old, band, m["vid"], m["model"],
                                m["conf"], m["term"], m["num"]])
        print(f"{cat.name:<26} {len(rows):>6,} frames, {n_files:>6,} files, "
              f"manifest rows updated {hit:,}")
        grand += n_files
        if rows:
            b, o, nw, _ = rows[0]
            print(f"     e.g. {o}")
            print(f"       -> {nw}")

    if args.apply:
        print(f"\nrenamed {grand:,} files. Mapping in <category>/{MAP}; "
              f"MANIFEST.csv keeps the old name as orig_stem.")
    else:
        print(f"\nDRY RUN — {grand:,} files would be renamed. Re-run with --apply.")


if __name__ == "__main__":
    main()
