#!/usr/bin/env python3
"""
band_trace.py - find the drone across a whole band at once, instead of tracing outward
from a seed.

The incremental trace loses the drone wherever a frame is weak, and every stall has to be
rescued by hand. But the camera is static for most of a band, so the drone is the only thing
in the sky that moves smoothly - which means the whole band can be scanned first and linked
afterwards, and the right track is then the one that is long and smooth.

Three steps, each its own command:

  mask    build a noise mask: the treeline and rooftops shimmer in every frame and swamp
          the sky, so those pixels are excluded before anything is measured.
  scan    temporal differencing against the median of the neighbouring frames, every frame,
          all candidate blobs kept.
  link    join the blobs into tracks with constant-velocity prediction, and report the
          longest few so the drone can be picked on evidence.

Then `emit` writes the chosen track out. Check it against the neighbouring band's anchor
before emitting: the 2592 bands are one flight, so a band's last frame carries straight on
into the next band's first frame.

Usage:
  ./band_trace.py mask --band "170-140 M 2592"
  ./band_trace.py scan --band "170-140 M 2592"
  ./band_trace.py link --band "170-140 M 2592"
  ./band_trace.py emit --band "170-140 M 2592" --track 0
"""

import argparse
import csv
import glob
import pickle
import re
import shutil
import sys
from pathlib import Path

import numpy as np

ROOT = ("/home/khive/Downloads/Sampel files/Images/own datasets/Day time/outdoor/"
        "distance-and-resulations based frames")
WORK = Path("/tmp/claude-1000/-home-khive-Khive-PROJECTS-Dron-Monitoring/"
            "d271cb5d-9882-444c-bdd9-87c34cf1ddc4/scratchpad/bands")

CLASS_ID = 0
STANDOFF = 5
CROP_PAD = 6.0
CROP_OUT = 640
MIN_W, MIN_H = 7.0, 4.0
# differencing
NB_OFFSETS = (-4, -3, -2, 2, 3, 4)
DIFF_THRESH = 2.4
DIFF_FLOOR = 4.0
# linking
MAX_GAP = 6
GATE_PX = 46.0
GATE_CAP = 230.0


def band_dirs(band):
    out = Path(ROOT) / "labelled" / "PTZ_10-300m_R-2592" / band
    src = Path(ROOT) / "PTZ_10-300m_R-2592" / band
    return src, out


def frame_index(d, ext="jpg"):
    return {int(re.search(rf"_(\d+)\.{ext}$", p).group(1)): Path(p)
            for p in sorted(glob.glob(str(d / f"*.{ext}")))}


def work_dir(band):
    w = WORK / band.replace(" ", "_")
    w.mkdir(parents=True, exist_ok=True)
    return w


def cmd_mask(args):
    import cv2
    src, out = band_dirs(args.band)
    idx = frame_index(out / "images") or frame_index(src)
    if not idx:
        sys.exit(f"no frames for {args.band}")
    # Measure each pixel's OWN noise floor rather than masking regions. One global threshold
    # cannot serve both halves of the frame: the sky is still enough that an 8 px drone
    # stands out, while the treeline shimmers so hard it produces thousands of blobs a frame.
    #
    # Use how often and how hard a pixel moves across the band, never the maximum - the
    # drone crosses a pixel once, and a max would bake its own trail into the floor and then
    # hide it from the scan.
    s1 = s2 = None
    total, prev = 0, None
    for n in sorted(idx)[::3]:
        im = cv2.cvtColor(cv2.imread(str(idx[n])), cv2.COLOR_BGR2GRAY).astype(np.float32)
        if prev is not None:
            d = np.abs(im - prev)
            s1 = d if s1 is None else s1 + d
            s2 = d * d if s2 is None else s2 + d * d
            total += 1
        prev = im
    mean = s1 / total
    sd = np.sqrt(np.maximum(s2 / total - mean * mean, 0))
    lvl = cv2.GaussianBlur(mean + 2.0 * sd, (0, 0), 3)
    np.save(work_dir(args.band) / "noise.npy", lvl.astype(np.float32))
    q = np.percentile(lvl, [50, 90, 99])
    print(f"{args.band}: per-pixel noise floor from {total} frame pairs - "
          f"median {q[0]:.1f}, p90 {q[1]:.1f}, p99 {q[2]:.1f} grey levels")


def cmd_scan(args):
    import cv2
    src, out = band_dirs(args.band)
    idx = frame_index(out / "images") or frame_index(src)
    w = work_dir(args.band)
    if not (w / "noise.npy").exists():
        sys.exit("run `mask` first")
    lvl = np.load(w / "noise.npy")
    cache = {}

    def g(n):
        if n not in cache:
            cache[n] = cv2.cvtColor(cv2.imread(str(idx[n])), cv2.COLOR_BGR2GRAY).astype(np.float32)
            if len(cache) > 14:
                cache.pop(next(iter(cache)))
        return cache[n]

    res = {}
    for n in sorted(idx):
        cur = g(n)
        nbs = [g(n + k) for k in NB_OFFSETS if n + k in idx]
        if len(nbs) < 3:
            res[n] = []
            continue
        df = np.abs(np.median(np.stack(nbs), 0) - cur)
        bar = np.maximum(max(DIFF_FLOOR, df.std() * args.thresh), lvl * args.noise_mult)
        mk = (df > bar).astype(np.uint8)
        mk = cv2.morphologyEx(mk, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        nl, _, st, _ = cv2.connectedComponentsWithStats(mk, 8)
        res[n] = [(x + bw / 2, y + bh / 2, bw, bh, a)
                  for x, y, bw, bh, a in (tuple(st[i]) for i in range(1, nl))
                  if a >= 5 and 3 <= bw <= 44 and 2 <= bh <= 24]
        if n % 50 == 0:
            print(f"  {n}", file=sys.stderr)
    pickle.dump(res, open(w / "blobs.pkl", "wb"))
    print(f"{len(res)} frames, {np.mean([len(v) for v in res.values()]):.0f} blobs/frame")


def cmd_link(args):
    w = work_dir(args.band)
    B = pickle.load(open(w / "blobs.pkl", "rb"))
    ns = sorted(B)
    tracks, used = [], set()
    for n in ns:
        for bi, (x, y, bw, bh, _) in enumerate(B[n]):
            if (n, bi) in used:
                continue
            used.add((n, bi))
            pts = {n: (x, y, bw, bh)}
            cn, cx, cy, vx, vy = n, x, y, 0.0, 0.0
            while True:
                best = None
                for m in range(cn + 1, min(cn + MAX_GAP, ns[-1]) + 1):
                    gap = m - cn
                    px, py = cx + vx * gap, cy + vy * gap
                    gate = min(GATE_PX * gap, GATE_CAP)
                    for bj, (x2, y2, w2, h2, _) in enumerate(B.get(m, [])):
                        if (m, bj) in used:
                            continue
                        d = np.hypot(x2 - px, y2 - py)
                        if d < gate and (best is None or d / gap < best[0]):
                            best = (d / gap, m, bj, x2, y2, w2, h2)
                    if best and best[1] == m:
                        break
                if not best:
                    break
                _, m, bj, x2, y2, w2, h2 = best
                used.add((m, bj))
                gap = m - cn
                vx, vy = (x2 - cx) / gap, (y2 - cy) / gap
                pts[m] = (x2, y2, w2, h2)
                cn, cx, cy = m, x2, y2
            if len(pts) >= 6:
                tracks.append(pts)
    tracks.sort(key=len, reverse=True)
    pickle.dump(tracks, open(w / "tracks.pkl", "wb"))
    print(f"{len(tracks)} tracks of 6+ points; longest {args.top}:\n")
    for i, t in enumerate(tracks[:args.top]):
        k = sorted(t)
        xs = [t[n][0] for n in k]
        ys = [t[n][1] for n in k]
        ws = [t[n][2] for n in k]
        steps = [np.hypot(t[k[j]][0] - t[k[j-1]][0], t[k[j]][1] - t[k[j-1]][1]) / (k[j] - k[j-1])
                 for j in range(1, len(k))]
        print(f"T{i:<2} n={len(t):<4} frames {k[0]}-{k[-1]:<5} "
              f"x {min(xs):.0f}..{max(xs):.0f}  y {min(ys):.0f}..{max(ys):.0f}  "
              f"w~{np.median(ws):.0f}  step~{np.median(steps):.0f}px")
        print("     start " + "  ".join(f"{n}:x{t[n][0]:.0f},y{t[n][1]:.0f}" for n in k[:4])
              + "   end " + "  ".join(f"{n}:x{t[n][0]:.0f},y{t[n][1]:.0f}" for n in k[-4:]))


def cmd_fill(args):
    """Walk outward from a chosen track, one frame at a time, re-measuring each missing
    frame against a lowered bar.

    The band scan has to hold one threshold over the whole frame, so at the far end of a
    flight the drone drops under it for runs of frames at a time. Here the position is
    already constrained to within a few pixels by the frames either side, so the bar can come
    down a long way without the search wandering: it only ever looks inside --radius of where
    the drone must be, and only accepts something the right size for it."""
    import cv2
    src, out = band_dirs(args.band)
    w = work_dir(args.band)
    track = load_track(args, w)
    idx = frame_index(out / "images") or frame_index(src)
    lvl = np.load(w / "noise.npy")
    cache = {}

    def g(n):
        if n not in cache:
            cache[n] = cv2.cvtColor(cv2.imread(str(idx[n])), cv2.COLOR_BGR2GRAY).astype(np.float32)
            if len(cache) > 20:
                cache.pop(next(iter(cache)))
        return cache[n]

    def measure(n, ex, ey, ew):
        R = int(args.radius)
        x0, y0 = int(max(0, ex - R)), int(max(0, ey - R))
        x1, y1 = int(min(lvl.shape[1], ex + R)), int(min(lvl.shape[0], ey + R))
        if x1 - x0 < 12 or y1 - y0 < 12:
            return None
        cur = g(n)[y0:y1, x0:x1]
        nbs = [g(n + k)[y0:y1, x0:x1] for k in (-5, -4, -3, 3, 4, 5) if n + k in idx]
        if len(nbs) < 3:
            return None
        df = np.abs(np.median(np.stack(nbs), 0) - cur)
        bar = np.maximum(max(args.floor, df.std() * args.fill_thresh),
                         lvl[y0:y1, x0:x1] * args.fill_noise)
        mk = cv2.morphologyEx((df > bar).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        nl, _, st, _ = cv2.connectedComponentsWithStats(mk, 8)
        # At this bar JPEG ringing starts to answer: 8-pixel macroblock edges, which come
        # out as slivers with no real contrast against the sky. The drone is wider than it
        # is tall at this range, and it is a genuinely darker (over foliage, lighter) spot.
        bg = cv2.GaussianBlur(cur, (0, 0), 11)
        best = None
        for i in range(1, nl):
            bx, by, bw, bh, a = (int(v) for v in st[i])
            if a < 3 or bw < 2 or bw > max(args.max_w, ew * 2.6) or bh > 16 or bw < bh:
                continue
            patch = cur[by:by + bh, bx:bx + bw]
            ring = bg[by:by + bh, bx:bx + bw]
            if patch.size == 0 or abs(float(patch.mean() - ring.mean())) < args.min_contrast:
                continue
            d = float(np.hypot(x0 + bx + bw / 2 - ex, y0 + by + bh / 2 - ey))
            if d <= args.radius and (best is None or d < best[0]):
                best = (d, x0 + bx + bw / 2, y0 + by + bh / 2, float(bw), float(bh))
        return best

    ns_all = sorted(idx)
    found = interp = 0
    for _ in range(args.passes):
        got = sorted(track)
        if len(got) == len(ns_all):
            break
        ws = [track[n][2] for n in got]
        before = len(track)
        for n in ns_all:
            if n in track:
                continue
            prev = [m for m in got if m < n][-3:]
            nxt = [m for m in got if m > n][:3]
            if not prev and not nxt:
                continue
            if prev and nxt and nxt[0] - prev[-1] <= args.span * 2:
                a, b = track[prev[-1]], track[nxt[0]]
                t = (n - prev[-1]) / (nxt[0] - prev[-1])
                ex, ey = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
            else:
                side = prev[::-1] if prev and (not nxt or n - prev[-1] <= nxt[0] - n) else nxt
                if abs(n - side[0]) > args.span:
                    continue
                vx = vy = 0.0
                if len(side) >= 2:
                    span = side[0] - side[1]
                    vx = (track[side[0]][0] - track[side[1]][0]) / span
                    vy = (track[side[0]][1] - track[side[1]][1]) / span
                step = n - side[0]
                ex, ey = track[side[0]][0] + vx * step, track[side[0]][1] + vy * step
            hit = measure(n, ex, ey, float(np.median(ws)))
            if hit:
                track[n] = (hit[1], hit[2], hit[3], hit[4])
                found += 1
        if len(track) == before:
            break

    if args.interpolate:
        got = sorted(track)
        mw = float(np.median([track[n][2] for n in got]))
        mh = float(np.median([track[n][3] for n in got]))
        for n in ns_all:
            if n in track:
                continue
            prev = [m for m in got if m < n]
            nxt = [m for m in got if m > n]
            if not prev or not nxt or nxt[0] - prev[-1] > args.span * 2:
                continue
            a, b = track[prev[-1]], track[nxt[0]]
            t = (n - prev[-1]) / (nxt[0] - prev[-1])
            track[n] = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, mw, mh)
            interp += 1

    pickle.dump(track, open(w / "filled.pkl", "wb"))
    left = sorted(set(ns_all) - set(track))
    print(f"measured {found} more frame(s), interpolated {interp}; "
          f"{len(track)}/{len(ns_all)} covered")
    if left:
        print(f"still missing: {left[:40]}{' ...' if len(left) > 40 else ''}")
    print(f"-> {w / 'filled.pkl'}")


def load_track(args, w):
    if args.from_pickle:
        return {int(n): tuple(float(v) for v in b)
                for n, b in pickle.load(open(args.from_pickle, "rb")).items()}
    name = "sky_tracks.pkl" if args.sky and (w / "sky_tracks.pkl").exists() else "tracks.pkl"
    return {n: tuple(float(v) for v in b)
            for n, b in pickle.load(open(w / name, "rb"))[args.track].items()}


def crop_tile(im, xyxy, H, W, size=CROP_OUT):
    import cv2
    x1, y1, x2, y2 = xyxy
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    half = max(max(x2 - x1, y2 - y1) * CROP_PAD / 2, 40)
    ox, oy = int(max(0, cx - half)), int(max(0, cy - half))
    a = im[oy:int(min(H, cy + half)), ox:int(min(W, cx + half))].copy()
    if a.size == 0:
        return np.zeros((size, size, 3), np.uint8)
    sc = size / max(a.shape[:2])
    a = cv2.resize(a, None, fx=sc, fy=sc, interpolation=cv2.INTER_NEAREST)
    m = 4
    cv2.rectangle(a, (int((x1 - ox) * sc) - m, int((y1 - oy) * sc) - m),
                     (int((x2 - ox) * sc) + m, int((y2 - oy) * sc) + m), (0, 0, 255), 1)
    o = np.zeros((size, size, 3), np.uint8)
    o[:a.shape[0], :a.shape[1]] = a[:size, :size]
    return o


def normalise(track):
    """A merged blob is far bigger than its neighbours and its centroid is pulled off the
    drone; a sliver is centred but too tight to train on. Fix the size from the local median
    either way, and the centre from the neighbours only when the blob was merged - never
    shrink a box onto the dark core, that clips the props."""
    ns = sorted(track)
    ws = np.array([track[n][2] for n in ns], float)
    hs = np.array([track[n][3] for n in ns], float)
    raw, changed = dict(track), []
    for i, n in enumerate(ns):
        x, y, w, h = raw[n]
        lo, hi = max(0, i - 8), min(len(ns), i + 9)
        mw, mh = float(np.median(ws[lo:hi])), float(np.median(hs[lo:hi]))
        big = w > 1.9 * mw or h > 2.4 * mh
        small = w < 0.55 * mw or h < 0.5 * mh
        if not (big or small):
            continue
        if big and 0 < i < len(ns) - 1:
            a, b = raw[ns[i - 1]], raw[ns[i + 1]]
            x, y = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        track[n] = (x, y, max(MIN_W, mw), max(MIN_H, mh))
        changed.append(n)
    return changed


def cmd_emit(args):
    import cv2
    src, out = band_dirs(args.band)
    w = work_dir(args.band)
    track = load_track(args, w)
    idx = frame_index(out / "images") or frame_index(src)
    probe = cv2.imread(str(idx[min(idx)]))
    H, W = probe.shape[:2]

    missing = sorted(set(idx) - set(track))
    if missing and not args.allow_gaps:
        sys.exit(f"{len(missing)} frame(s) have no box: {missing[:30]}"
                 f"{' ...' if len(missing) > 30 else ''}\nfill them, or pass --allow-gaps")
    print(f"normalised {len(normalise(track))} outlier box(es)")

    ns = sorted(track)
    steps = [np.hypot(track[ns[i]][0] - track[ns[i-1]][0], track[ns[i]][1] - track[ns[i-1]][1])
             for i in range(1, len(ns))]
    print(f"continuity: max {max(steps):.0f}px  mean {np.mean(steps):.1f}px  "
          f"steps>60px: {sum(s > 60 for s in steps)}")

    if args.dry_run:
        return
    for d in ("annotated", "crops"):
        shutil.rmtree(out / d, ignore_errors=True)
        (out / d).mkdir(parents=True)
    (out / "labels").mkdir(parents=True, exist_ok=True)

    rows = []
    for n in sorted(idx):
        stem = idx[n].stem
        if n not in track:
            (out / "labels" / f"{stem}.txt").write_text("")
            rows.append({"image": idx[n].name, "labelled": "no", "conf": "",
                         "box_w_px": "", "note": "no drone found"})
            continue
        cx, cy, bw, bh = track[n]
        bw, bh = max(bw, MIN_W), max(bh, MIN_H)
        x1, y1 = max(0.0, cx - bw / 2), max(0.0, cy - bh / 2)
        x2, y2 = min(W - 1.0, cx + bw / 2), min(H - 1.0, cy + bh / 2)
        (out / "labels" / f"{stem}.txt").write_text(
            f"{CLASS_ID} {(x1+x2)/2/W:.6f} {(y1+y2)/2/H:.6f} "
            f"{(x2-x1)/W:.6f} {(y2-y1)/H:.6f}\n")
        im = cv2.imread(str(idx[n]))
        ann = im.copy()
        s = STANDOFF
        cv2.rectangle(ann, (int(x1) - s, int(y1) - s), (int(x2) + s, int(y2) + s), (0, 0, 255), 1)
        cv2.imwrite(str(out / "annotated" / f"{stem}.jpg"), ann, [cv2.IMWRITE_JPEG_QUALITY, 88])
        cv2.imwrite(str(out / "crops" / f"{stem}.jpg"), crop_tile(im, (x1, y1, x2, y2), H, W))
        rows.append({"image": idx[n].name, "labelled": "yes", "conf": "",
                     "box_w_px": int(round(x2 - x1)), "note": "trajectory trace (differencing)"})

    with open(out / "REVIEW.csv", "w", newline="") as fh:
        wtr = csv.DictWriter(fh, fieldnames=["image", "labelled", "conf", "box_w_px", "note"])
        wtr.writeheader()
        wtr.writerows(rows)
    print(f"wrote {sum(r['labelled'] == 'yes' for r in rows)}/{len(rows)} frames")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["mask", "scan", "link", "fill", "emit"])
    ap.add_argument("--band", required=True)
    ap.add_argument("--noise-mult", type=float, default=1.3,
                    help="how far above its own noise floor a pixel must move to count")
    ap.add_argument("--thresh", type=float, default=DIFF_THRESH)
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--track", type=int, default=0)
    ap.add_argument("--sky", action="store_true", help="use the sky-only track list")
    ap.add_argument("--from-pickle", help="a hand-assembled {frame: (cx,cy,w,h)} instead")
    # fill
    ap.add_argument("--radius", type=float, default=22.0)
    ap.add_argument("--span", type=int, default=6)
    ap.add_argument("--passes", type=int, default=40)
    ap.add_argument("--fill-thresh", type=float, default=1.9)
    ap.add_argument("--fill-noise", type=float, default=0.85)
    ap.add_argument("--floor", type=float, default=2.2)
    ap.add_argument("--max-w", type=float, default=22.0,
                    help="widest blob the fill will accept")
    ap.add_argument("--min-contrast", type=float, default=2.0,
                    help="grey levels the blob must stand out from its surroundings")
    ap.add_argument("--interpolate", action="store_true",
                    help="fill what is still missing from the neighbours, for review")
    ap.add_argument("--allow-gaps", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    {"mask": cmd_mask, "scan": cmd_scan, "link": cmd_link,
     "fill": cmd_fill, "emit": cmd_emit}[args.cmd](args)


if __name__ == "__main__":
    main()
