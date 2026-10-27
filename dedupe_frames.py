#!/usr/bin/env python3
"""
dedupe_frames.py - remove frames that are the same picture as another frame.

Three kinds of repetition end up in a triage band, and this finds all three:

  1 same_source_frame   one video pulled in under two aircraft searches, so the
                        identical frame is stored twice under different model
                        names. Usually byte-for-byte identical.
  2 reuploaded_video    the same video uploaded to YouTube under two ids. The
                        filenames share nothing, so only the pixels give it
                        away.
  3 near_duplicate      consecutive frames of the same clip. At fps 0.5 they are
                        2 s apart, and a hovering drone or a slow pan makes them
                        the same picture.

EVERY deleted frame is a duplicate of a frame that STAYS. Nothing unique is
removed: for each file dropped, the same picture is still in the folder under
another name. The CSV names that survivor on every row so it can be checked.

    ./dedupe_frames.py --band "<band folder>"                    # dry run
    ./dedupe_frames.py --band "<band folder>" --level moderate
    ./dedupe_frames.py --band "<band folder>" --apply

A band folder is the one holding annotated/ and images/. Both are kept in step:
a frame dropped from annotated/ has its twin dropped from images/ in the same
pass, so ai_triage_sync.py has nothing left to repair.

Levels are perceptual-hash distances. 0 means the two pictures are identical
after downscaling; higher tolerates more difference.

    identical   0   only pictures with no visible difference at all
    moderate    4   the default, and the line we settled on for f6_v2 band 1
    aggressive  8   starts removing genuinely different moments — check first
"""
from __future__ import annotations

import argparse
import collections
import csv
import re
import sys
from pathlib import Path

from frame_meta import video_id as _vid_of

try:
    import imagehash
    from PIL import Image
except ImportError:
    # Re-run under the project venv. Launching this as ./dedupe_frames.py uses
    # the system python via the shebang, and the system python has neither
    # library — so the script failed for anyone who had not activated the venv
    # first, which is everyone who just copied the command out of a chat.
    # _RE_EXEC stops this looping if the venv is missing them too.
    import os
    _venv = Path(__file__).resolve().parent.parent / ".venv" / "bin" / "python3"
    if _venv.exists() and not os.environ.get("_DEDUPE_RE_EXEC"):
        os.environ["_DEDUPE_RE_EXEC"] = "1"
        os.execv(str(_venv), [str(_venv), str(Path(__file__).resolve()), *sys.argv[1:]])
    sys.exit("needs pillow and imagehash. Either activate the venv:\n"
             f"    source {_venv.parent}/activate\n"
             "or install them:\n"
             "    pip install pillow imagehash")

LEVELS = {"identical": 0, "moderate": 4, "aggressive": 8}
# "<conf>__<model>__<model>_t<term>_<videoid>_<frame>.jpg"
NAME_RE = re.compile(
    r"^(?P<conf>[0-9.]+|MISS)__(?P<model>.+?)__(?P=model)"
    r"_t(?P<term>\d+)_(?P<vid>[A-Za-z0-9_-]{11})_(?P<num>\d{5})\.jpg$")


def plan(folder: Path, threshold: int, progress=True):
    """[(kind, victim, survivor, video)] plus the files that were scanned."""
    files = sorted(folder.glob("*.jpg"))
    meta, skipped = {}, []
    for p in files:
        m = NAME_RE.match(p.name)
        if m:
            meta[p.name] = m.groupdict()
            continue
        # Short names (f1_b1_00001) carry no video id; frame_meta reads it
        # back out of the category's rename_map.csv.
        vid = _vid_of(p)
        num = re.sub(r"\D", "", p.stem)[-5:] or "0"
        if vid:
            meta[p.name] = {"conf": "", "model": "", "term": "0",
                            "vid": vid, "num": num}
        else:
            skipped.append(p.name)
    if skipped:
        print(f"  ! {len(skipped)} filename(s) not in the expected form, left alone",
              file=sys.stderr)

    order = sorted(meta, key=lambda n: (meta[n]["vid"], int(meta[n]["num"])))
    drop: dict[str, tuple[str, str]] = {}

    # 1 — identical source frame under two model names. Filename-only, no
    #     hashing needed: same video id AND same frame number IS the same frame.
    same = collections.defaultdict(list)
    for n in order:
        same[(meta[n]["vid"], meta[n]["num"])].append(n)
    for group in same.values():
        if len(group) > 1:
            keep = sorted(group)[0]
            for n in sorted(group)[1:]:
                drop[n] = ("1_same_source_frame", keep)

    # 2 and 3 — one pass over the pixels. Done together because the only
    #     difference between them is whether the match landed in another video,
    #     and that is known only after the match is found.
    H = {}
    live = [n for n in order if n not in drop]
    for i, n in enumerate(live, 1):
        H[n] = imagehash.phash(Image.open(folder / n).convert("L").resize((128, 128)))
        if progress and i % 1000 == 0:
            print(f"  hashed {i:,}/{len(live):,}", flush=True)

    kept: list[str] = []
    for n in live:
        hit = next((k for k in reversed(kept) if (H[n] - H[k]) <= threshold), None)
        if hit is None:
            kept.append(n)
            continue
        kind = ("2_reuploaded_video" if meta[hit]["vid"] != meta[n]["vid"]
                else "3_near_duplicate_frame")
        drop[n] = (kind, hit)

    rows = [(drop[n][0], n, drop[n][1], meta[n]["vid"]) for n in order if n in drop]
    return rows, len(meta)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n")[1],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--band", help="folder holding annotated/ and images/")
    g.add_argument("--dir", help="a single folder of frames")
    ap.add_argument("--level", choices=sorted(LEVELS), default="moderate",
                    help="how alike two frames must be to count as the same "
                         "picture (default: moderate)")
    ap.add_argument("--apply", action="store_true",
                    help="actually delete (default is a dry run)")
    ap.add_argument("--csv", default="", help="where to write the plan")
    args = ap.parse_args()

    root = Path(args.band or args.dir)
    if not root.is_dir():
        sys.exit(f"not a directory: {root}")
    scan = root / "annotated" if args.band else root
    if not scan.is_dir():
        sys.exit(f"no annotated/ inside {root} — use --dir for a plain folder")
    mirror = root / "images" if args.band and (root / "images").is_dir() else None

    th = LEVELS[args.level]
    print(f"scanning {scan}")
    print(f"level: {args.level} (phash distance <= {th})\n")
    rows, total = plan(scan, th)

    by_kind = collections.Counter(r[0] for r in rows)
    print(f"\n{'kind':<26}{'frames':>8}")
    print("-" * 34)
    for k in sorted(by_kind):
        print(f"{k:<26}{by_kind[k]:>8,}")
    print("-" * 34)
    print(f"{'TOTAL to delete':<26}{len(rows):>8,}")
    print(f"{'KEEP':<26}{total - len(rows):>8,}"
          f"   ({100 * len(rows) / total:.1f}% redundant)" if total else "")

    out = Path(args.csv) if args.csv else root.parent / f"{root.name}_duplicates.csv"
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["kind", "delete_this", "because_it_duplicates", "video"])
        w.writerows(rows)
    print(f"\nplan written to {out}")

    if mirror:
        twins = sum(1 for _, n, _, _ in rows if (mirror / n).exists())
        print(f"images/ twins that would also go: {twins:,} of {len(rows):,}")
    else:
        print("no images/ beside it — only annotated/ would change")

    if not args.apply:
        print(f"\nDRY RUN — nothing deleted. Check the CSV, then re-run with --apply.")
        return

    n = m = 0
    for _, name, _, _ in rows:
        if (scan / name).exists():
            (scan / name).unlink()
            n += 1
        if mirror and (mirror / name).exists():
            (mirror / name).unlink()
            m += 1
    print(f"\ndeleted {n:,} from annotated/" + (f" and {m:,} from images/" if mirror else ""))
    print(f"{total - n:,} frames remain.")


if __name__ == "__main__":
    main()
