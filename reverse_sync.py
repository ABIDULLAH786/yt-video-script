#!/usr/bin/env python3
"""
reverse_sync.py - mirror deletions made in images/ onto annotated/.

The companion to ai_triage_sync.py, which goes the other way. Use this when the
review was done in images/ by mistake: the deletions are real review decisions
and worth keeping, they are simply recorded in the wrong folder, so the fix is
to mirror them rather than undo them.

    ./reverse_sync.py --out "filtered/AI Filtered/f6_heavy_lift"          # dry run
    ./reverse_sync.py --out "filtered/AI Filtered/f6_heavy_lift" --apply

SAFE BY DEFAULT: prints the plan and stops. --apply actually deletes.

It refuses a band where images/ is completely empty, which is far more likely to
mean a mis-deleted folder than a total rejection. --allow-empty overrides.

Files present in images/ but NOT in annotated/ are reported and left alone —
that is annotation having failed for those frames, not a review decision, and
deleting the surviving copy would lose data.
"""

import argparse
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="a category folder, or one band")
    ap.add_argument("--apply", action="store_true", help="actually delete")
    ap.add_argument("--allow-empty", action="store_true",
                    help="permit a band whose images/ is now completely empty")
    args = ap.parse_args()

    root = Path(args.out)
    if not root.is_dir():
        sys.exit(f"not a directory: {root}")

    bands = ([root] if (root / "annotated").is_dir()
             else sorted(p for p in root.iterdir() if (p / "annotated").is_dir()))
    if not bands:
        sys.exit(f"no annotated/ folders under {root}")

    total = 0
    plan = []
    for b in bands:
        A, I = b / "annotated", b / "images"
        if not I.is_dir():
            print(f"  {b.name}: no images/, skipping")
            continue
        a = {p.name for p in A.glob("*.jpg")}
        i = {p.name for p in I.glob("*.jpg")}
        drop, orphan = a - i, i - a
        if not drop and not orphan:
            print(f"  {b.name}: already in step")
            continue
        if not i and not args.allow_empty:
            print(f"  {b.name}: images/ is EMPTY — refusing. "
                  f"Use --allow-empty if you really rejected all {len(a):,}.",
                  file=sys.stderr)
            continue
        print(f"  {b.name}: delete {len(drop):,} from annotated/"
              + (f"   ({len(orphan):,} in images/ have no annotated twin — left alone)"
                 if orphan else ""))
        plan.append((A, drop))
        total += len(drop)

    if not args.apply:
        print(f"\nDRY RUN — would delete {total:,} files from annotated/. "
              f"Re-run with --apply.")
        return

    for A, drop in plan:
        for n in drop:
            (A / n).unlink(missing_ok=True)
    print(f"\ndeleted {total:,} files from annotated/.")


if __name__ == "__main__":
    main()
