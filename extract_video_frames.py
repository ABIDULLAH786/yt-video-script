#!/usr/bin/env python3
"""
extract_video_frames.py - one folder of frames per source video, at native resolution.

WHY NOT model_frames.py / neo_dataset.py
----------------------------------------
Those pull YouTube footage of other people's drones and downscale it
(`scale='min(max_side,iw)':-2`) because the volume is huge and the source resolution
is arbitrary. This is for OUR OWN camera recordings, where neither applies:

  * 1280x960 IS the TensorRT engine's input shape, so downscaling would throw away
    exactly the pixels the detector actually receives on the edge. Native only.
  * the volume is small enough that 1 fps is affordable, and these frames are scarce
    real-domain data rather than bulk airframe variety.

MILESIGHT AVI QUIRK
-------------------
Milesight exports carry a second, bogus `rawvideo` stream with no pixel format. It
makes `ffprobe` abort during header parsing ("tag [0][0][0][0] has size 1414744396"),
so a file can look unreadable while decoding perfectly. Every call here selects the
real stream explicitly with `-map 0:v:0`, and the probe reads ffmpeg's own stderr
rather than ffprobe's JSON.

Usage:
  ./extract_video_frames.py --src "<videos dir>" --out "<frames dir>"
  ./extract_video_frames.py --src ... --out ... --fps 2      # denser
  ./extract_video_frames.py --src ... --out ... --probe      # report only, extract nothing
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

VIDEO_EXTS = (".avi", ".mp4", ".mkv", ".mov", ".m4v")

# "Stream #0:0: Video: h264 (High) (...), yuvj420p(...), 1280x960, 25 fps, ..."
RE_STREAM = re.compile(r"Stream #\d+:\d+.*?: Video: (\w+).*?, (\d{2,5})x(\d{2,5})[, ]")
RE_FPS = re.compile(r"(\d+(?:\.\d+)?) fps")
RE_DUR = re.compile(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)")


def probe(path: Path):
    """codec, w, h, fps, seconds - read off ffmpeg's stderr, which survives the bad header."""
    p = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path)],
                       capture_output=True, text=True)
    err = p.stderr
    m = RE_STREAM.search(err)
    if not m:
        return None
    codec, w, h = m.group(1), int(m.group(2)), int(m.group(3))
    line = err[m.start():m.end() + 120]
    fps = float(RE_FPS.search(line).group(1)) if RE_FPS.search(line) else 0.0
    d = RE_DUR.search(err)
    secs = int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3)) if d else 0.0
    return codec, w, h, fps, secs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="folder of source videos")
    ap.add_argument("--out", required=True, help="folder to create one subfolder per video in")
    ap.add_argument("--fps", type=float, default=1.0, help="frames per second to keep")
    ap.add_argument("--quality", type=int, default=2, help="ffmpeg -q:v, 2 = near-lossless JPEG")
    ap.add_argument("--probe", action="store_true", help="report what is there, extract nothing")
    ap.add_argument("--only", metavar="WxH",
                    help="take only videos at this exact frame size, e.g. 1280x960. A source "
                         "folder named for one resolution often is not all one resolution, and "
                         "mixing them silently mislabels the dataset built from it.")
    args = ap.parse_args()

    src, out = Path(args.src), Path(args.out)
    if not src.is_dir():
        sys.exit(f"source not found: {src}")
    vids = sorted(p for p in src.iterdir()
                  if p.is_file() and p.suffix.lower() in VIDEO_EXTS)
    if not vids:
        sys.exit(f"no videos under {src}")

    print(f"{len(vids)} videos under {src}")
    print(f"{'file':<44}{'codec':<7}{'size':<12}{'fps':>5}{'dur':>7}{'keep':>7}")
    print("-" * 82)

    plan, sizes = [], set()
    for v in vids:
        info = probe(v)
        if not info:
            print(f"{v.name:<44}{'UNREADABLE':<7}")
            continue
        codec, w, h, fps, secs = info
        keep = max(1, int(secs * args.fps))
        size = f"{w}x{h}"
        sizes.add(size)
        if args.only and size != args.only:
            print(f"{v.name:<44}{codec:<7}{size:<12}{fps:>5.0f}{secs:>7.0f}{'skip':>7}")
            continue
        plan.append((v, keep))
        print(f"{v.name:<44}{codec:<7}{size:<12}{fps:>5.0f}{secs:>7.0f}{keep:>7}")

    print("-" * 82)
    print(f"{'TOTAL':<44}{'':<7}{'':<12}{'':>5}{'':>7}{sum(k for _, k in plan):>7}")
    if len(sizes) > 1:
        print(f"\nNOTE: mixed frame sizes in this folder: {', '.join(sorted(sizes))}")
    if args.probe:
        return

    out.mkdir(parents=True, exist_ok=True)
    print()
    total = 0
    for v, expect in plan:
        stem = v.stem
        dest = out / stem
        dest.mkdir(parents=True, exist_ok=True)
        existing = list(dest.glob("*.jpg"))
        if existing:
            print(f"SKIP  {stem}  ({len(existing)} frames already there)")
            total += len(existing)
            continue
        cmd = ["ffmpeg", "-v", "error", "-y",
               "-i", str(v),
               "-map", "0:v:0",                 # the real stream, not the bogus rawvideo one
               "-vf", f"fps={args.fps}",        # no scale filter - native resolution is the point
               "-q:v", str(args.quality),
               str(dest / f"{stem}_%05d.jpg")]
        subprocess.run(cmd, check=False)
        n = len(list(dest.glob("*.jpg")))
        total += n
        print(f"  ok  {stem:<44}{n:>6} frames")

    print(f"\n{total} frames across {len(plan)} folders")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
