#!/usr/bin/env python3
"""
video_index.py - every YouTube video the corpus came from, per category and globally.

    ./video_index.py                 # scan everything -> per-category + global CSVs
    ./video_index.py --dupes         # also print videos reused across categories
    ./video_index.py --dir "<path>"  # one folder only, ad hoc

WHAT IT SCANS
  filtered/AI Filtered/<category>/<band>/annotated/   for triaged categories
  raw/models/<category>/<model>/                      for categories not triaged yet (f7)

Either way the video id comes from the FILENAME, because that is the one thing
that survives: raw/models has been deleted for most categories as they were
triaged, taking their videos.json with it.

WHAT IT WRITES  (into video_index/)
  <category>.csv   one row per video in that category, with the bands and models it
                   appears in
  ALL.csv          one row per unique video across every category, so a video used
                   by two categories is a single row naming both

WHY. The same YouTube video is returned for searches of quite different aircraft,
so one video's frames end up in several model folders — in f6's silent band one
video had 1,067 frames spread over 8 models. Reviewing per folder means judging
that footage eight times. Decide per video instead.
"""

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
TRIAGE = HERE / "filtered" / "AI Filtered"
RAW = HERE / "raw" / "models"
OUTDIR = HERE / "video_index"
FRAME_RE = re.compile(r"^.*_t\d+_(.+?)_\d{5}\.(?:jpg|jpeg|png)$", re.I)
URL = "https://www.youtube.com/watch?v={}"


def _model_of(path: Path, root: Path) -> str:
    """Which drone model this frame belongs to.

    Triage bands are flat folders holding frames from every model, so the
    directory tells you nothing and the filename is the only source:
    "<BAND>__<model>__<model>_t0_<videoid>_00001.jpg". Raw layout is
    <category>/<model>/<frame>, so there the parent directory is right.
    """
    if "__" in path.name:
        return path.name.split("__")[1]
    rel = path.relative_to(root).parts
    return rel[0] if len(rel) > 1 else "(unknown)"


def scan(root: Path, root_for_model: Path = None):
    """{video_id: {"frames": n, "models": set, "bands": set}} under root."""
    out = {}
    if not root.exists():
        return out
    for f in root.rglob("*.jpg"):
        m = FRAME_RE.match(f.name)
        if not m:
            continue
        e = out.setdefault(m.group(1),
                           {"frames": 0, "models": set(), "bands": set()})
        e["frames"] += 1
        e["models"].add(_model_of(f, root_for_model or root))
        parts = f.relative_to(root).parts
        if len(parts) > 1 and parts[0][0].isdigit():
            e["bands"].add(parts[0])          # triage band folder
    return out


def load_titles():
    """Titles and durations from whatever videos.json still exists."""
    meta = {}
    for mj in HERE.rglob("videos.json"):
        try:
            for vid, m in json.loads(mj.read_text()).items():
                meta.setdefault(vid, (m.get("title", ""), m.get("duration", "")))
        except Exception:
            continue
    return meta


def write_csv(path: Path, rows, meta, extra_col=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        head = ["video_id", "url", "frames", "n_models", "models"]
        if extra_col:
            head.insert(3, extra_col[0])
        head += ["bands", "duration_s", "title"]
        w.writerow(head)
        for vid, e in rows:
            t, d = meta.get(vid, ("", ""))
            row = [vid, URL.format(vid), e["frames"], len(e["models"]),
                   " | ".join(sorted(e["models"]))]
            if extra_col:
                row.insert(3, extra_col[1](e))
            row += [" | ".join(sorted(e.get("bands", []))), d, t]
            w.writerow(row)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=None, help="scan one folder only, ad hoc")
    ap.add_argument("--dupes", action="store_true",
                    help="print videos reused across categories")
    ap.add_argument("--out", default=None, help="output file for --dir mode")
    args = ap.parse_args()

    meta = load_titles()

    if args.dir:
        d = Path(args.dir)
        idx = scan(d, d)
        if not idx:
            sys.exit(f"no frames found under {d}")
        rows = sorted(idx.items(), key=lambda kv: (-len(kv[1]["models"]), -kv[1]["frames"]))
        out = Path(args.out or "videos_in_folder.csv")
        write_csv(out, rows, meta)
        print(f"{len(idx):,} videos, {sum(e['frames'] for e in idx.values()):,} frames -> {out}")
        return

    # Triaged categories, then any raw category with no triage output yet.
    cats = {}
    for c in sorted(p for p in TRIAGE.iterdir() if p.is_dir()) if TRIAGE.exists() else []:
        idx = scan(c, c)
        if idx:
            cats[c.name] = idx
    for c in sorted(p for p in RAW.iterdir() if p.is_dir()) if RAW.exists() else []:
        if c.name in cats:
            continue
        idx = scan(c, c)
        if idx:
            cats[c.name] = idx

    if not cats:
        sys.exit("nothing found to scan")

    OUTDIR.mkdir(exist_ok=True)
    glob = {}
    print(f"{'category':<22}{'videos':>8}{'frames':>10}")
    for name, idx in cats.items():
        rows = sorted(idx.items(), key=lambda kv: (-len(kv[1]["models"]), -kv[1]["frames"]))
        write_csv(OUTDIR / f"{name}.csv", rows, meta)
        print(f"  {name:<20}{len(idx):>8,}{sum(e['frames'] for e in idx.values()):>10,}")
        for vid, e in idx.items():
            g = glob.setdefault(vid, {"frames": 0, "models": set(), "cats": set()})
            g["frames"] += e["frames"]
            g["models"] |= e["models"]
            g["cats"].add(name)

    grows = sorted(glob.items(), key=lambda kv: (-len(kv[1]["cats"]), -kv[1]["frames"]))
    write_csv(OUTDIR / "ALL.csv", grows, meta,
              extra_col=("categories", lambda e: " | ".join(sorted(e["cats"]))))

    cross = [(v, e) for v, e in grows if len(e["cats"]) > 1]
    print(f"\n  {'UNIQUE across all':<20}{len(glob):>8,}"
          f"{sum(e['frames'] for e in glob.values()):>10,}")
    print(f"  used by >1 category : {len(cross):,}")
    print(f"\nwrote {len(cats)} per-category files + ALL.csv -> {OUTDIR}/")

    if args.dupes and cross:
        print("\nreused across categories:")
        for vid, e in cross[:20]:
            print(f"  {vid}  {e['frames']:>5} frames  {' | '.join(sorted(e['cats']))}")


if __name__ == "__main__":
    main()
