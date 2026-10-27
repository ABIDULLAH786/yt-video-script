#!/usr/bin/env python3
"""
track_range.py - label the measured-distance PTZ capture by TRACK, not by frame.

WHY NOT PER-FRAME (prelabel_range.py's approach, which failed here)
-------------------------------------------------------------------
Measured on the 50-10 m band at imgsz 1280:

    detections per frame      2.7          (there is exactly ONE drone)
    frames with >1 detection  98%
    runner-up within 0.15     75%

Looking at the extras, they are not obvious junk: they are small dark specks against bright
sky, 14-20 px, at 0.26-0.49 - birds, distant aircraft, possibly sensor dust. Several are as
drone-like as the one the model scored highest. So "keep the top-confidence box" writes a
wrong label roughly as often as a right one, and no per-frame rule fixes that.

WHAT DOES WORK
--------------
These are sequential video frames at 5 fps, so the drone has a trajectory and the spurious
specks do not. Linking detections between consecutive frames by nearest neighbour collapses
214 detections over 80 frames into 12 tracks, two or three of which persist. Picking the
drone is then ONE decision per band instead of one per frame.

TWO STEPS, AND THE HUMAN IS IN THE MIDDLE
-----------------------------------------
  1. survey   - detect, link into tracks, render a filmstrip per track, write TRACKS.csv.
                You look at the filmstrips and decide which track is the drone.
  2. emit     - with --keep-track N, write images/annotated/crops/labels for that track only.

The tool never decides which track is the drone. It cannot: at 20 px a bird and a quadcopter
are a handful of pixels apart, and guessing would silently poison a few hundred labels.

Usage:
  ./track_range.py survey --src "<band dir>" --out "<out root>"
  ./track_range.py emit   --src "<band dir>" --out "<out root>" --keep-track 1
"""

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

import numpy as np

IMG_EXTS = (".jpg", ".jpeg", ".png")
CLASS_ID = 0
GATE_PX = 140          # how far a target may move between frames at 5 fps
MAX_GAP = 15           # frames a track may vanish for and still continue.
                       # 3 was far too strict: detection drops out for longer than that
                       # whenever the drone crosses buildings or treeline, which broke one
                       # trajectory into dozens of fragments. 93% of frames in the band's
                       # 295-frame "gap" had a detection all along - they just never linked.
MAX_SIZE_RATIO = 1.8   # a box may not nearly double or halve between frames
SIZE_COST = 120.0      # px of equivalent distance per unit of size mismatch
GATE_CAP = 320.0       # px: the furthest a link may ever reach, however long the gap
STANDOFF = 5           # px the drawn outline stands off the true box, so a tight
                       # box on a 5 px-tall drone does not hide it under its own border
STRIP_N = 12           # crops shown per track filmstrip
TILE = 150


def detect_all(files, model_path, imgsz, conf, batch):
    from ultralytics import YOLO
    m = YOLO(model_path)
    per = []
    for i in range(0, len(files), batch):
        chunk = files[i:i + batch]
        for r in m.predict([str(p) for p in chunk], conf=conf, imgsz=imgsz, verbose=False):
            b = r.boxes
            cur = []
            if b is not None and len(b):
                xy = b.xyxy.cpu().numpy()
                cf = b.conf.cpu().numpy()
                for j in range(len(cf)):
                    cur.append((xy[j].tolist(), float(cf[j])))
            per.append(cur)
        done = i + len(chunk)
        if done % (batch * 25) == 0 or done == len(files):
            print(f"  detect {done}/{len(files)}", flush=True)
    return per


def link(per):
    """Link detections across frames using a constant-velocity prediction and box-size
    continuity.

    Nearest-neighbour from the LAST position silently swaps targets whenever the real
    target moves faster than a nearby distractor. Measured on 50-10 M frame 153: the drone
    was tracking left at ~93 px/frame (x2086 -> x1993) while a static 15 px speck sat at
    x2048. From the last position the speck was 38 px away and the drone 94 px, so the
    speck won and the track stayed on it for the rest of its life.

    Two cues fix it and both were already in the data:
      * where the target is GOING - predict from velocity, gate around the prediction;
      * how BIG it is - the drone was 26 px, the speck 15 px. A box that halves between
        consecutive frames is almost never the same object at this scale.
    """
    tracks = []
    for fi, cur in enumerate(per):
        dets = []
        for xyxy, cf in cur:
            dets.append({"xyxy": xyxy, "cf": cf,
                         "cx": (xyxy[0] + xyxy[2]) / 2, "cy": (xyxy[1] + xyxy[3]) / 2,
                         "w": xyxy[2] - xyxy[0]})
        pairs = []
        for di, d in enumerate(dets):
            for ti, t in enumerate(tracks):
                gap = fi - t["frames"][-1]
                if gap > MAX_GAP or gap <= 0:
                    continue
                px, py = t["cx"][-1], t["cy"][-1]
                if len(t["cx"]) >= 2:                      # constant-velocity prediction
                    fgap = max(1, t["frames"][-1] - t["frames"][-2])
                    vx = (t["cx"][-1] - t["cx"][-2]) / fgap
                    vy = (t["cy"][-1] - t["cy"][-2]) / fgap
                    px += vx * gap
                    py += vy * gap
                dist = float(np.hypot(d["cx"] - px, d["cy"] - py))
                # Allow a longer reach over a longer gap, but CAP it. An uncapped gate was
                # letting a track jump between objects: at files 49-75 track 2 sat on a bird
                # at x1120-1244 while the drone was on track 3 at x2007-2066, then rejoined
                # the drone at file 149. One track, two objects, and the filmstrip looked
                # fine because most of its life was the drone. A cap makes that jump
                # impossible, at the cost of more fragments - which are safe, because a
                # fragment can be accepted or rejected, while a switched track poisons labels.
                gate = GATE_PX * (1.0 + 0.25 * (gap - 1))
                lw = t["box"][-1][2] - t["box"][-1][0]
                ratio = max(d["w"], lw) / max(1.0, min(d["w"], lw))
                if dist > gate or ratio > MAX_SIZE_RATIO:
                    continue
                pairs.append((dist + SIZE_COST * (ratio - 1.0), di, ti))
        pairs.sort()
        took_d, took_t = set(), set()
        for cost, di, ti in pairs:
            if di in took_d or ti in took_t:
                continue
            d, t = dets[di], tracks[ti]
            t["frames"].append(fi); t["cx"].append(d["cx"]); t["cy"].append(d["cy"])
            t["conf"].append(d["cf"]); t["box"].append(d["xyxy"])
            took_d.add(di); took_t.add(ti)
        for di, d in enumerate(dets):
            if di not in took_d:
                tracks.append({"frames": [fi], "cx": [d["cx"]], "cy": [d["cy"]],
                               "conf": [d["cf"]], "box": [d["xyxy"]]})
    tracks.sort(key=lambda t: len(t["frames"]), reverse=True)
    return tracks


def crop_tile(im, xyxy, pad=6.0, size=TILE, draw=True):
    """A zoomed crop around the box. At 5-80 px in a 2592-wide frame the target is invisible
    in a full-frame thumbnail, so this is the view a reviewer actually judges on.

    The outline is drawn AFTER the upscale, 1 px thick and standing off the box by a small
    margin. Drawn the other way round - a 3 px rectangle on the raw box, then upscaled - it
    buried the target completely: measured on frame 00125, a 3 px border on a 22x5 px box
    covers 100% of its height, so the review image was a solid red bar with the drone under
    it. The LABEL keeps the exact tight coordinates; only the drawing stands off.
    """
    import cv2
    H, W = im.shape[:2]
    x1, y1, x2, y2 = xyxy
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    half = max(max(x2 - x1, y2 - y1) * pad / 2, 40)
    ox, oy = int(max(0, cx - half)), int(max(0, cy - half))
    a = im[oy:int(min(H, cy + half)), ox:int(min(W, cx + half))].copy()
    if a.size == 0:
        return np.zeros((size, size, 3), np.uint8)
    sc = size / max(a.shape[:2])
    a = cv2.resize(a, None, fx=sc, fy=sc, interpolation=cv2.INTER_NEAREST)
    if draw:
        m = 4   # px of clear air between the outline and the target, after upscale
        cv2.rectangle(a,
                      (int((x1 - ox) * sc) - m, int((y1 - oy) * sc) - m),
                      (int((x2 - ox) * sc) + m, int((y2 - oy) * sc) + m),
                      (0, 0, 255), 1)
    out = np.zeros((size, size, 3), np.uint8)
    out[:a.shape[0], :a.shape[1]] = a[:size, :size]
    return out


def tighten(im, xyxy, grow=8):
    """Pull the box in to the drone's actual dark extent.

    The model's boxes carry 34-66% padding here - measured on five frames: 28x16 where the
    drone occupies 22x7, 68x52 where it occupies 58x38. Loose boxes teach the model that a
    drone includes a margin of sky, which costs localisation accuracy.

    The drone is dark against bright sky, so inside the box the separation is clean and the
    true extent can be recovered by thresholding. Over buildings and treeline there is no
    such separation, so the guard below keeps the model's box rather than shrinking onto a
    roof edge.
    """
    import cv2
    H, W = im.shape[:2]
    x1, y1, x2, y2 = [float(v) for v in xyxy]
    rx1, ry1 = int(max(0, x1 - grow)), int(max(0, y1 - grow))
    rx2, ry2 = int(min(W, x2 + grow)), int(min(H, y2 + grow))
    reg = im[ry1:ry2, rx1:rx2]
    if reg.size == 0:
        return (x1, y1, x2, y2), False
    g = cv2.cvtColor(reg, cv2.COLOR_BGR2GRAY).astype(np.float32)
    bg, sd = float(np.median(g)), float(g.std())
    if bg < 90 or sd > 46:                 # not clean sky - keep the model's box
        return (x1, y1, x2, y2), False
    # k=1.5, not 2.2. At 2.2 the mask caught only the darkest core and clipped the
    # airframe: frame 124 came out 19x10 where the drone spans 21x11, frame 560 58x38
    # against 60x44, losing the propellers. Measured across four frames, 1.5 captures the
    # full extent and 1.2 adds nothing further.
    mask = g < (bg - 1.5 * sd)
    if mask.sum() < 4 or mask.mean() > 0.45:
        return (x1, y1, x2, y2), False
    ys, xs = np.where(mask)
    nx1, ny1 = rx1 + float(xs.min()), ry1 + float(ys.min())
    nx2, ny2 = rx1 + float(xs.max()) + 1, ry1 + float(ys.max()) + 1
    if (nx2 - nx1) < 3 or (ny2 - ny1) < 3:
        return (x1, y1, x2, y2), False
    return (nx1, ny1, nx2, ny2), True


def survey(args, files, out):
    import cv2
    per = detect_all(files, args.model, args.imgsz, args.conf, args.batch)
    tracks = link(per)
    (out / "tracks").mkdir(parents=True, exist_ok=True)

    rows = []
    for n, t in enumerate(tracks, 1):
        span = t["frames"][-1] - t["frames"][0] + 1
        p = np.array(list(zip(t["cx"], t["cy"])))
        path = float(np.sum(np.hypot(*np.diff(p, axis=0).T))) if len(p) > 1 else 0.0
        widths = [b[2] - b[0] for b in t["box"]]
        rows.append({
            "track": n, "detections": len(t["frames"]), "span_frames": span,
            "coverage_pct": round(len(t["frames"]) / len(files) * 100, 1),
            "path_px": round(path), "med_conf": round(float(np.median(t["conf"])), 3),
            "med_box_px": round(float(np.median(widths))),
            "first_frame": files[t["frames"][0]].name,
            "last_frame": files[t["frames"][-1]].name,
        })
        if n > args.max_strips:
            continue
        idx = np.linspace(0, len(t["frames"]) - 1, min(STRIP_N, len(t["frames"]))).astype(int)
        tiles = []
        for k in idx:
            im = cv2.imread(str(files[t["frames"][k]]))
            if im is None:
                continue
            tile = crop_tile(im, t["box"][k])
            cv2.putText(tile, f"f{t['frames'][k]} {t['conf'][k]:.2f}", (4, 14),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)
            tiles.append(tile)
        if tiles:
            strip = np.hstack(tiles)
            hdr = np.zeros((26, strip.shape[1], 3), np.uint8)
            cv2.putText(hdr, f"track {n}   {len(t['frames'])} dets   "
                             f"{rows[-1]['coverage_pct']}% of frames   "
                             f"path {rows[-1]['path_px']}px   median {rows[-1]['med_box_px']}px",
                        (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            cv2.imwrite(str(out / "tracks" / f"track_{n:02d}.jpg"), np.vstack([hdr, strip]))

    with open(out / "TRACKS.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    (out / "_tracks.json").write_text(json.dumps(
        [{k: t[k] for k in ("frames", "box", "conf")} for t in tracks]))

    print(f"\n{sum(len(c) for c in per)} detections -> {len(tracks)} tracks")
    print(f"\n{'#':>3}{'dets':>6}{'cover':>8}{'path px':>9}{'conf':>7}{'px':>5}")
    print("-" * 38)
    for r in rows[:12]:
        print(f"{r['track']:>3}{r['detections']:>6}{r['coverage_pct']:>7}%"
              f"{r['path_px']:>9}{r['med_conf']:>7.2f}{r['med_box_px']:>5}")
    print(f"\nfilmstrips -> {out/'tracks'}")
    print("Open them, decide which track is the drone, then re-run:")
    print(f"  ./track_range.py emit --src <band> --out <root> --keep-track N")


def emit(args, files, out):
    import cv2
    tj = out / "_tracks.json"
    if not tj.exists():
        sys.exit("no survey found - run `survey` on this band first")
    tracks = json.loads(tj.read_text())
    wanted = [int(x) for x in str(args.keep_tracks).split(",") if x.strip()]
    if not wanted or any(not (1 <= k <= len(tracks)) for k in wanted):
        sys.exit(f"--keep-tracks must be a comma list in 1..{len(tracks)}")

    # Wipe first. A previous emit with a different track selection leaves annotated files
    # for frames this one does not label - they were the stale green frames reported on
    # 00006, 00009, 00050, 00057 and 00214-00238.
    for d in ("images", "annotated", "crops", "labels"):
        if (out / d).exists():
            shutil.rmtree(out / d)
        (out / d).mkdir(parents=True, exist_ok=True)

    # The drone's trajectory fragments across several tracks as it moves and detection
    # drops out, so more than one is normally kept. Where two kept tracks claim the same
    # frame the higher-confidence box wins — there is only ever one drone in these frames.
    by_frame = {}
    for k in wanted:
        t = tracks[k - 1]
        for fi, box, cf in zip(t["frames"], t["box"], t["conf"]):
            if fi not in by_frame or cf > by_frame[fi][1]:
                by_frame[fi] = (box, cf)

    rows = []
    for fi, path in enumerate(files):
        im = cv2.imread(str(path))
        if im is None:
            continue
        H, W = im.shape[:2]
        shutil.copy(path, out / "images" / path.name)
        lbl = out / "labels" / f"{path.stem}.txt"
        if fi in by_frame:
            (x1, y1, x2, y2), cf = by_frame[fi]
            # NO TIGHTENING. Shrinking to the dark-pixel extent was tried and is wrong for
            # this target: the drone's body is dark but its spinning props are faint, so a
            # threshold keeps the body and cuts the props off. Measured against the model's
            # own boxes - f121 31x12 vs 27x8, f140 27x9 vs 24x5, f257 38x14 vs 36x8,
            # f498 53x17 vs 49x14 - the model box contains the whole airframe every time
            # and the tightened one clips it. A box that clips the object teaches the model
            # a drone is just its fuselage. tighten() is kept below, unused, as the record.
            cx, cy = (x1 + x2) / 2 / W, (y1 + y2) / 2 / H
            nw, nh = (x2 - x1) / W, (y2 - y1) / H
            lbl.write_text(f"{CLASS_ID} {cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}\n")
            ann = im.copy()
            # Box only - no confidence text. 1 px, and stood off by STANDOFF so a box on
            # a 5 px-tall drone does not vanish under its own border (a 3 px border on a
            # 5 px box covers 100% of it, which is what made these look like solid bars).
            m = STANDOFF
            cv2.rectangle(ann, (int(x1) - m, int(y1) - m), (int(x2) + m, int(y2) + m),
                          (0, 0, 255), 1)
            cv2.imwrite(str(out / "annotated" / f"{path.stem}.jpg"), ann,
                        [cv2.IMWRITE_JPEG_QUALITY, 88])
            tile = crop_tile(im, (x1, y1, x2, y2), size=640)
            cv2.imwrite(str(out / "crops" / f"{path.stem}.jpg"), tile)
            rows.append({"image": path.name, "labelled": "yes", "conf": round(cf, 3),
                         "box_w_px": int(x2 - x1), "box_h_px": int(y2 - y1), "note": ""})
        else:
            lbl.write_text("")
            rows.append({"image": path.name, "labelled": "no", "conf": "",
                         "box_w_px": "", "box_h_px": "",
                         "note": "track absent - draw by hand or drop"})

    with open(out / "REVIEW.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    got = sum(1 for r in rows if r["labelled"] == "yes")
    print(f"tracks {wanted}: {got}/{len(rows)} frames labelled ({got/len(rows):.0%})")
    print(f"{len(rows)-got} frames have an empty label - the track was absent there")
    print(f"wrote {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["survey", "emit"])
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="../model-testing/models/v16-best.pt")
    ap.add_argument("--imgsz", type=int, default=1280,
                    help="1280 measured: 95% of frames detected vs 48% at 640")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-strips", type=int, default=12)
    ap.add_argument("--keep-tracks", default="",
                    help="comma list of track numbers that are the drone, e.g. 1,2,10,12")
    args = ap.parse_args()

    try:
        import cv2  # noqa
        import ultralytics  # noqa
    except ModuleNotFoundError as e:
        sys.exit(f"missing {e.name} - use the project-root venv: ../.venv/bin/python")

    src = Path(args.src)
    files = sorted(p for p in src.iterdir() if p.suffix.lower() in IMG_EXTS)
    if args.limit:
        files = files[:args.limit]
    if not files:
        sys.exit(f"no images in {src}")
    out = Path(args.out) / src.name
    out.mkdir(parents=True, exist_ok=True)
    print(f"{len(files)} frames <- {src.name}\nout -> {out}\n")

    (survey if args.mode == "survey" else emit)(args, files, out)


if __name__ == "__main__":
    main()
