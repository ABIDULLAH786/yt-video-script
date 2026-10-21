#!/usr/bin/env python3
"""
rebuild_frames.py - re-create the original extracted frames for a category.

Reads the category's MANIFEST.csv, which records the source_path of every frame
ever triaged, works out which YouTube videos those came from, downloads each one
once, re-extracts, and writes the frames back under their original names in
raw/models/<category>/<model>/.

This reproduces the files EXACTLY. Extraction is deterministic — same video,
same fps, same scale, same frame number — verified byte-for-byte on 2026-10-02:
a re-derived frame had md5 4cf519bb838afe635dbaf2e6f8355f90, identical to the
surviving original.

    ./rebuild_frames.py --category f3_ducted_fpv              # dry run, shows the plan
    ./rebuild_frames.py --category f3_ducted_fpv --apply
    ./rebuild_frames.py --category f3_ducted_fpv --apply --only-missing

RESUMABLE per video: a video whose frames are already on disk is skipped, so an
interrupted run is continued by re-issuing the same command.

One video often feeds several model folders (the same clip comes back for
different aircraft searches), so it is downloaded once and fanned out.
"""

import argparse
import csv
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
TRIAGE = HERE / "filtered" / "AI Filtered"
RAW = HERE / "raw" / "models"

# Must match what model_frames.py fetch used, or the frames will not line up.
FPS = 0.5
MAX_SIDE = 1280
JPEG_Q = 2

NAME_RE = re.compile(r"^(?P<model>.+)_t(?P<term>\d+)_(?P<vid>.+)_(?P<num>\d{5})\.jpg$", re.I)


def _tool(name: str) -> str:
    """Find a CLI tool, preferring the project venv.

    Running this script as ./rebuild_frames.py uses the system python via the
    shebang, so the venv's bin/ is not on PATH and yt-dlp is simply missing.
    Resolving it here means the script works whether or not the venv is active.
    """
    venv = HERE.parent / ".venv" / "bin" / name
    if venv.exists():
        return str(venv)
    found = shutil.which(name)
    if not found:
        sys.exit(f"{name} not found — activate the venv or install it")
    return found


def plan_from_manifest(cat: str):
    """{video_id: [(model, term, frame_num, original_filename), ...]}"""
    man = TRIAGE / cat / "MANIFEST.csv"
    if not man.exists():
        sys.exit(f"no MANIFEST.csv for {cat} at {man}")
    want = defaultdict(list)
    for row in csv.DictReader(open(man)):
        src = Path(row["source_path"])
        m = NAME_RE.match(src.name)
        if not m:
            continue
        want[m.group("vid")].append(
            (src.parent.name, m.group("term"), m.group("num"), src.name))
    return want


def extract(video: Path, workdir: Path) -> dict:
    """Re-extract the whole video; returns {frame_number: path}."""
    out = workdir / "f_%05d.jpg"
    subprocess.run(
        [_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", str(video),
         "-vf", f"fps={FPS},scale='min({MAX_SIDE},iw)':-2",
         "-q:v", str(JPEG_Q), str(out)],
        check=False)
    return {p.name[2:7]: p for p in sorted(workdir.glob("f_*.jpg"))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only-missing", action="store_true",
                    help="skip frames already present on disk")
    ap.add_argument("--cookies-from-browser", default=None)
    ap.add_argument("--js-runtime", default="node")
    ap.add_argument("--remote-components", default="ejs:github")
    ap.add_argument("--sleep-interval", type=float, default=0)
    ap.add_argument("--limit", type=int, default=0, help="stop after N videos")
    args = ap.parse_args()

    want = plan_from_manifest(args.category)
    total_frames = sum(len(v) for v in want.values())
    dest_root = RAW / args.category

    missing_videos = []
    for vid, items in want.items():
        if args.only_missing and all((dest_root / m / fn).exists()
                                     for m, _t, _n, fn in items):
            continue
        missing_videos.append(vid)

    print(f"{args.category}: {len(want)} videos, {total_frames:,} frames in the manifest")
    print(f"to rebuild: {len(missing_videos)} videos")
    print(f"destination: {dest_root}/<model>/\n")
    if not args.apply:
        for vid in missing_videos[:10]:
            models = sorted({m for m, _, _, _ in want[vid]})
            print(f"  {vid}  {len(want[vid]):>5} frames -> {', '.join(models[:3])}"
                  + (" …" if len(models) > 3 else ""))
        if len(missing_videos) > 10:
            print(f"  … and {len(missing_videos)-10} more videos")
        print("\nDRY RUN — re-run with --apply.")
        return

    done = written = failed = 0
    for i, vid in enumerate(missing_videos, 1):
        if args.limit and i > args.limit:
            break
        items = want[vid]
        print(f"[{i}/{len(missing_videos)}] {vid}  ({len(items)} frames)", flush=True)
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cmd = [_tool("yt-dlp")]
            if args.js_runtime:
                cmd += ["--js-runtimes", args.js_runtime]
            if args.remote_components:
                cmd += ["--remote-components", args.remote_components]
            if args.cookies_from_browser:
                cmd += ["--cookies-from-browser", args.cookies_from_browser]
            if args.sleep_interval:
                cmd += ["--sleep-interval", str(args.sleep_interval)]
            cmd += ["-f", "bv*[height<=1080]/b[height<=1080]/bv*/b", "--no-warnings",
                    "-o", str(td / "%(id)s.%(ext)s"),
                    f"https://www.youtube.com/watch?v={vid}"]
            r = subprocess.run(cmd, capture_output=True, text=True)
            vids = [p for p in td.iterdir() if p.suffix.lower() in
                    (".mp4", ".mkv", ".webm", ".m4v", ".ts", ".mov")]
            if r.returncode != 0 or not vids:
                err = r.stderr.strip()[:120]
                print(f"    DOWNLOAD FAILED: {err}", file=sys.stderr)
                if "not a bot" in err or "Sign in to confirm" in err:
                    print("\n!! YouTube is rate-limiting this IP. Re-run with "
                          "--cookies-from-browser chrome, or wait.\n", file=sys.stderr)
                    break
                failed += 1
                continue

            frames = extract(vids[0], td)
            n = 0
            for model, _term, num, fname in items:
                src = frames.get(num)
                if src is None:
                    continue
                dst = dest_root / model / fname
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                n += 1
            written += n
            done += 1
            print(f"    restored {n}/{len(items)} frames", flush=True)

    print(f"\nrebuilt {done} videos, {written:,} frames written, {failed} failed")
    print(f"next: re-run ai_triage.py on {dest_root} to re-bucket by confidence")


if __name__ == "__main__":
    main()
