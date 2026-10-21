#!/usr/bin/env python3
"""
dedupe_categories.py - remove frames from a category whose video already lives elsewhere.

The same YouTube video comes back for searches of different aircraft, so one
video's frames end up in several categories. Reviewing it twice is wasted work,
and keeping it twice feeds the same footage to training twice.

    ./dedupe_categories.py                       # dry run, shows the plan
    ./dedupe_categories.py --apply               # do it

Defaults clean f6_heavy_lift and f6_heavy_lift_v2 against every other category.

TWO KINDS OF DUPLICATE, both handled:
  * video also in ANOTHER category  -> remove from the target, the other keeps it
  * video in both f6 v1 and v2      -> v1 keeps it, v2 loses it, because v2 was
                                       collected to add NEW material

SAFE BY DEFAULT: prints what it would delete and stops. --apply actually deletes.
Both annotated/ and images/ are removed together so the two stay in step, which
is what ai_triage_sync.py would otherwise have to repair.
"""

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
TRIAGE = HERE / "filtered" / "AI Filtered"
RAW = HERE / "raw" / "models"
FRAME_RE = re.compile(r"^.*_t\d+_(.+?)_\d{5}\.(?:jpg|jpeg|png)$", re.I)

DEFAULT_TARGETS = "f6_heavy_lift,f6_heavy_lift_v2"


def category_root(name: str) -> Path:
    """Triage output if it exists, otherwise the raw collection."""
    t = TRIAGE / name
    return t if t.is_dir() else RAW / name


def video_ids(root: Path) -> set:
    out = set()
    if not root.exists():
        return out
    for f in root.rglob("*.jpg"):
        m = FRAME_RE.match(f.name)
        if m:
            out.add(m.group(1))
    return out


def frames_for(root: Path, vids: set, protect: set = frozenset()):
    """Frames of `vids` under root, skipping any band named in `protect`.

    Bands are per-FRAME confidence buckets of the same footage, not separate
    videos — measured on f1, 158 of its 177 videos have frames in all four
    bands. So a band is never a sensible thing to dedupe AGAINST; protecting
    one simply means "do not delete from here", which is what --protect-bands
    is for.
    """
    hits = defaultdict(list)
    if not root.exists():
        return hits
    for f in root.rglob("*.jpg"):
        m = FRAME_RE.match(f.name)
        if not (m and m.group(1) in vids):
            continue
        if protect and any(part in protect for part in f.relative_to(root).parts):
            continue
        hits[m.group(1)].append(f)
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", default=DEFAULT_TARGETS,
                    help="categories to clean, comma separated")
    ap.add_argument("--protect-bands", default="",
                    help="comma-separated band folder names never to delete "
                         "from, e.g. 3_fires_low_conf25to50")
    ap.add_argument("--apply", action="store_true", help="actually delete")
    args = ap.parse_args()

    targets = [t.strip() for t in args.targets.split(",") if t.strip()]
    protect = {b.strip() for b in args.protect_bands.split(",") if b.strip()}
    all_cats = sorted({p.name for p in TRIAGE.iterdir() if p.is_dir()} |
                      {p.name for p in RAW.iterdir() if p.is_dir()}) \
        if TRIAGE.exists() or RAW.exists() else []
    references = [c for c in all_cats if c not in targets]

    print(f"targets    : {', '.join(targets)}")
    if protect:
        print(f"protected  : {', '.join(sorted(protect))} (never deleted from)")
    print(f"references : {', '.join(references)}\n")

    ref_vids = set()
    for c in references:
        ref_vids |= video_ids(category_root(c))
    print(f"{len(ref_vids):,} videos held by the reference categories\n")

    # Targets are processed in the order given, so the FIRST one keeps a video
    # the others also have — v1 before v2 under the default.
    claimed = set()
    total_files = total_vids = 0
    for cat in targets:
        root = category_root(cat)
        if not root.exists():
            print(f"{cat}: not found, skipping")
            continue
        mine = video_ids(root)
        drop = (mine & ref_vids) | (mine & claimed)
        claimed |= (mine - drop)

        hits = frames_for(root, drop, protect)
        n_files = sum(len(v) for v in hits.values())
        total_files += n_files
        total_vids += len(hits)
        print(f"{cat}: {len(mine)} videos, dropping {len(hits)} ({n_files:,} files)")
        for vid in sorted(hits, key=lambda v: -len(hits[v]))[:12]:
            why = "in another category" if vid in ref_vids else "already in an earlier target"
            print(f"    {vid}  {len(hits[vid]):>5} files  — {why}")
        if len(hits) > 12:
            print(f"    … and {len(hits)-12} more")

        if args.apply:
            for files in hits.values():
                for f in files:
                    f.unlink(missing_ok=True)
        print()

    if not args.apply:
        print(f"DRY RUN — would delete {total_files:,} files "
              f"covering {total_vids} videos. Re-run with --apply to do it.")
    else:
        print(f"deleted {total_files:,} files covering {total_vids} videos.")


if __name__ == "__main__":
    main()
